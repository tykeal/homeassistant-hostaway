# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""One rate limiter per Hostaway account, shared by every config entry.

Hostaway counts requests against the account *and* the originating IP, so
a limiter that lived on a config entry would be two limiters when a second
entry appeared and none at all for the few seconds a reload takes. An
unloaded entry therefore keeps its claim for one more window, so a reload
cannot widen the budget while it is halfway done. The
registry therefore lives outside ``hass.data[DOMAIN]``: that key is what
``async_unload_entry`` consults to decide whether the last entry has gone
and services should be removed, and a limiter parked there would keep it
truthy forever.
"""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.event import async_call_later

from custom_components.hostaway.api.const import (
    DEFAULT_RATE_LIMIT_BUDGET,
    RATE_LIMIT_CEILING,
    RATE_LIMIT_WINDOW_SECONDS,
)
from custom_components.hostaway.api.rate_limit import (
    AccountRateLimiter,
    shared_ip_gate,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_RATE_LIMIT_BUDGET,
    DATA_RATE_LIMITERS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LimiterRegistry:
    """Every limiter in the process, and what each entry asked for."""

    limiters: dict[str, AccountRateLimiter] = field(default_factory=dict)
    #: entry id -> (account key, the budget that entry configured)
    contributions: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: entry id -> monotonic deadline after which an unloaded entry's
    #: contribution stops counting. A reload clears the deadline long
    #: before it is reached.
    pending_release: dict[str, float] = field(default_factory=dict)
    #: entry id -> the timer that will come back and honour that deadline.
    release_timers: dict[str, CALLBACK_TYPE] = field(default_factory=dict)


def _now() -> float:
    """Return the monotonic clock the grace window is measured against.

    Returns:
        A monotonic timestamp in seconds.
    """
    return time.monotonic()


def entry_budget(entry: ConfigEntry) -> int:
    """Return the rate-limit budget an entry asks for.

    Args:
        entry: The config entry to read.

    Returns:
        The configured budget, or the default when absent or nonsensical.
    """
    return _stored_budget(entry) or DEFAULT_RATE_LIMIT_BUDGET


def _stored_budget(entry: ConfigEntry) -> int | None:
    """Return the usable budget an entry stores, if it stores one.

    Args:
        entry: The config entry to read.

    Returns:
        The stored budget, or ``None`` when nothing usable is stored.
    """
    value = entry.options.get(
        CONF_RATE_LIMIT_BUDGET,
        entry.data.get(CONF_RATE_LIMIT_BUDGET),
    )
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= RATE_LIMIT_CEILING:
        return None
    return value


def budget_source(entry: ConfigEntry) -> str:
    """Report whether the budget in force was chosen or defaulted.

    An operator reading diagnostics needs to know whether the figure
    they are looking at is one they set. A stored value that is out of
    range is not honoured, so it is reported as a default too.

    Args:
        entry: The config entry to read.

    Returns:
        ``"option"`` when the entry supplies a usable budget, else
        ``"default"``.
    """
    return "option" if _stored_budget(entry) is not None else "default"


def get_registry(hass: HomeAssistant) -> LimiterRegistry:
    """Return the process-wide limiter registry, creating it if needed.

    Args:
        hass: Home Assistant instance.

    Returns:
        The registry, which outlives any individual config entry.
    """
    registry = hass.data.get(DATA_RATE_LIMITERS)
    if not isinstance(registry, LimiterRegistry):
        registry = LimiterRegistry()
        hass.data[DATA_RATE_LIMITERS] = registry
    _expire_pending(registry)
    return registry


def _drop(registry: LimiterRegistry, entry_id: str) -> bool:
    """Remove one entry's contribution and any limiter it was the last of.

    Args:
        registry: The registry to mutate.
        entry_id: The config entry whose claim is being dropped.

    Returns:
        True if something was actually dropped.
    """
    registry.pending_release.pop(entry_id, None)
    cancel = registry.release_timers.pop(entry_id, None)
    if cancel is not None:
        cancel()
    dropped = registry.contributions.pop(entry_id, None)
    if dropped is None:
        return False
    account_key, _ = dropped
    if not any(key == account_key for key, _ in registry.contributions.values()):
        limiter = registry.limiters.pop(account_key, None)
        if limiter is not None:
            limiter.close()
    return True


def _expire_pending(registry: LimiterRegistry) -> None:
    """Retire unloaded entries whose grace window has run out.

    A timer normally brings us here on time. The check is repeated on
    every registry operation anyway, because a timer that was never
    scheduled — or that fired while the registry was busy — must not be
    the only thing standing between an unloaded entry and its claim.

    Args:
        registry: The registry to recompute.
    """
    if not registry.pending_release:
        return
    now = _now()
    expired = [
        entry_id
        for entry_id, deadline in registry.pending_release.items()
        if deadline <= now
    ]
    if not expired:
        return
    for entry_id in expired:
        _LOGGER.debug("Releasing the budget held by unloaded entry %s", entry_id)
        _drop(registry, entry_id)
    apply_minima(registry)


def _established_account(hass: HomeAssistant, registry: LimiterRegistry) -> str | None:
    """Return the one account this instance has already committed to.

    A registered entry settles the question outright. Failing that, the
    oldest configured entry does, even if it is not loaded: an entry that
    failed, was disabled, or was unloaded long enough for its claim to
    lapse is still an account the user has configured, and the config
    flow would have turned a second one away on its behalf.

    Args:
        hass: Home Assistant instance.
        registry: The registry to consult first.

    Returns:
        The account key, or None if this instance has no Hostaway account.
    """
    for key, _ in registry.contributions.values():
        return key
    for other in hass.config_entries.async_entries(DOMAIN):
        configured = other.data.get(CONF_CLIENT_ID)
        if isinstance(configured, str):
            return configured
    return None


def register_entry(hass: HomeAssistant, entry: ConfigEntry) -> AccountRateLimiter:
    """Attach a config entry to its account's limiter.

    Args:
        hass: Home Assistant instance.
        entry: The entry being set up.

    Returns:
        The limiter the entry's clients must pace against.

    Raises:
        ConfigEntryError: If the entry belongs to a second Hostaway
            account. Reusing the first account's limiter would alias the
            two onto one gate, so one account's refusal would suppress the
            other's traffic; this is not a condition that retrying fixes.
    """
    registry = get_registry(hass)
    account_key = entry.data[CONF_CLIENT_ID]
    registry.pending_release.pop(entry.entry_id, None)
    cancel = registry.release_timers.pop(entry.entry_id, None)
    if cancel is not None:
        cancel()
    established = _established_account(hass, registry)
    if established is not None and established != account_key:
        msg = (
            "The Hostaway integration supports a single account: the rate "
            "limit is shared per account and per IP, and pacing a second "
            "account against the first account's budget would let either "
            "one silence the other"
        )
        raise ConfigEntryError(msg)

    registry.contributions[entry.entry_id] = (account_key, entry_budget(entry))
    limiter = registry.limiters.get(account_key)
    if limiter is None:
        limiter = AccountRateLimiter(account_key)
        registry.limiters[account_key] = limiter
    apply_minima(registry)
    return limiter


def schedule_release(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the grace window on an entry that has just been unloaded.

    An unload is not necessarily goodbye: a reload is an unload followed
    immediately by a setup, and releasing the contribution in between
    would raise the shared budget for exactly as long as the reload takes
    — the window SC-012 exists to close. The contribution therefore keeps
    counting for one full rate-limit window. A reload reclaims it in
    milliseconds; a genuine unload lets it lapse.

    Args:
        hass: Home Assistant instance.
        entry: The entry that was unloaded.
    """
    registry = get_registry(hass)
    if entry.entry_id not in registry.contributions:
        return
    registry.pending_release[entry.entry_id] = _now() + RATE_LIMIT_WINDOW_SECONDS

    previous = registry.release_timers.pop(entry.entry_id, None)
    if previous is not None:
        previous()

    @callback
    def _release_is_due(_fired_at: datetime) -> None:
        """Honour the deadline even if nothing else asks the registry.

        A same-account entry that is still loaded acquires straight from
        the limiter and never touches the registry, so without this the
        departed entry's lower budget could hold both minima down for as
        long as the instance runs.

        Args:
            _fired_at: When the timer fired.
        """
        registry.release_timers.pop(entry.entry_id, None)
        _expire_pending(get_registry(hass))

    # Cancelled on shutdown: a Home Assistant that is stopping has no
    # budget left to protect.
    registry.release_timers[entry.entry_id] = async_call_later(
        hass,
        RATE_LIMIT_WINDOW_SECONDS,
        HassJob(
            _release_is_due,
            "hostaway rate limit claim release",
            cancel_on_shutdown=True,
        ),
    )


def release_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget an entry immediately, because it has been removed.

    Removal is final, so there is no reload to protect and no reason to
    wait out the grace window that :func:`schedule_release` starts.

    Args:
        hass: Home Assistant instance.
        entry: The entry being removed.
    """
    registry = get_registry(hass)
    if _drop(registry, entry.entry_id):
        apply_minima(registry)


def apply_minima(registry: LimiterRegistry) -> None:
    """Re-apply the budget minima to every live limiter.

    An account's gate gets the lowest budget any of its entries asked for,
    and the shared IP gate gets the lowest any entry asked for at all. A
    minimum is the only safe reconciliation: the server enforces one
    counter however many entries are pointed at it.

    Args:
        registry: The registry to recompute.
    """
    if not registry.contributions:
        # Nothing is configured any more, so the only thing that could
        # still consult the shared gate is a config flow. Leaving it at
        # the departed entry's ceiling would hold a new account to a
        # budget nobody chose; the in-window admissions stay, because
        # those requests really were sent.
        shared_ip_gate().reconfigure(DEFAULT_RATE_LIMIT_BUDGET)
        return
    ip_budget = min(budget for _, budget in registry.contributions.values())
    for account_key, limiter in registry.limiters.items():
        budgets = [
            budget
            for key, budget in registry.contributions.values()
            if key == account_key
        ]
        if not budgets:
            continue
        limiter.configure(
            account_budget=min(budgets),
            effective_ip_budget=ip_budget,
        )


@contextmanager
def validation_limiter(
    hass: HomeAssistant, client_id: str
) -> Iterator[AccountRateLimiter]:
    """Return the limiter a config flow should pace its probes against.

    An account that is already set up has a limiter whose window knows what
    has been sent; reusing it is the whole point. A brand-new account gets
    a transient one, which is excluded from the budget minima — it has no
    entry and no option behind it — but still shares the process-wide IP
    gate, because the IP counter does not care that this request is only a
    credential check.

    Args:
        hass: Home Assistant instance.
        client_id: The account being validated.

    Yields:
        A limiter to pace the validation requests against. A transient one
        is closed on the way out; a registered one is left alone.
    """
    registry = get_registry(hass)
    existing = registry.limiters.get(client_id)
    if existing is not None:
        yield existing
        return
    _LOGGER.debug("Using a transient validation limiter for a new account")
    transient = AccountRateLimiter(client_id)
    try:
        yield transient
    finally:
        transient.close()
