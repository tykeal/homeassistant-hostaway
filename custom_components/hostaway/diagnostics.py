# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Diagnostics for a Hostaway config entry.

The interesting question when an operator opens a support ticket is
whether the integration is being paced, and by which of the two counters
Hostaway applies. Everything here is read from a limiter snapshot and the
coordinators' shed counts, and nothing that identifies the account is
included: the entry ``unique_id`` is the client id, so the limiter is
named by its own random label instead.
"""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.hostaway.api.const import RATE_LIMIT_WINDOW_SECONDS
from custom_components.hostaway.api.rate_limit import AccountRateLimiter
from custom_components.hostaway.api.rate_limit.queueing import (
    GateSnapshot,
    LimiterSnapshot,
)
from custom_components.hostaway.const import CONF_CLIENT_ID, DOMAIN
from custom_components.hostaway.coordinator import HostawayShedAwareCoordinator
from custom_components.hostaway.rate_limit_registry import (
    budget_source,
    entry_budget,
    get_registry,
)

APPLIED_COUNTERS: tuple[str, ...] = (
    "account",
    "ip",
    "endpoint",
    "provider",
    "unknown",
)

GATE_NAMES: tuple[str, ...] = ("account", "ip")

COORDINATOR_KEYS: dict[str, str] = {
    "listings": "listings_coordinator",
    "reservations": "reservations_coordinator",
    "custom_fields": "custom_fields_coordinator",
}


def _gate_payload(gate: GateSnapshot) -> dict[str, Any]:
    """Describe one gate.

    Args:
        gate: The gate snapshot to describe.

    Returns:
        A JSON-serializable view of the gate.
    """
    return {
        "budget": gate.budget,
        "effective_budget": gate.effective_budget,
        "window_seconds": gate.window_seconds,
        "admitted_in_window": gate.admitted_in_window,
        "waiting_interactive": gate.waiting_interactive,
        "waiting_scheduled": gate.waiting_scheduled,
        "suppressed": gate.suppressed,
        "suppressed_for_seconds": gate.suppressed_for_seconds,
    }


def _unavailable_gate(budget: int) -> dict[str, Any]:
    """Describe a gate that has not been built yet.

    Diagnostics are downloaded precisely when an entry is unhappy, so the
    payload keeps its documented shape even when the entry never got as
    far as creating a limiter. The budget is the one the entry asks for;
    everything measured is reported as nothing measured.

    Args:
        budget: The budget this entry configures.

    Returns:
        A gate object with the same keys a real gate would have.
    """
    return {
        "budget": budget,
        "effective_budget": budget,
        "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
        "admitted_in_window": 0,
        "waiting_interactive": 0,
        "waiting_scheduled": 0,
        "suppressed": False,
        "suppressed_for_seconds": None,
    }


def _limiter_for(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entry_data: dict[str, Any],
) -> AccountRateLimiter | None:
    """Find the limiter pacing this entry's account, if there is one.

    The registry outlives any single entry, so an entry that is unloaded
    or failed to set up can still be reported against the limiter its
    account is really being paced by.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being diagnosed.
        entry_data: The integration's stored data for that entry.

    Returns:
        The account's limiter, or ``None`` when none has been built.
    """
    api_client = entry_data.get("api_client")
    limiter = getattr(api_client, "limiter", None)
    if isinstance(limiter, AccountRateLimiter):
        return limiter

    account_key = entry.data.get(CONF_CLIENT_ID)
    if not isinstance(account_key, str):
        return None
    return get_registry(hass).limiters.get(account_key)


def _shed_counts(entry_data: dict[str, Any]) -> dict[str, int]:
    """Collect the per-coordinator shed counts.

    The fixed keys are used rather than the coordinators' own names,
    which embed the entry ``unique_id`` and therefore the client id.

    Args:
        entry_data: The integration's stored data for one entry.

    Returns:
        A shed count for each coordinator, zero when it is absent.
    """
    counts: dict[str, int] = {}
    for label, key in COORDINATOR_KEYS.items():
        coordinator = entry_data.get(key)
        counts[label] = (
            coordinator.shed_count
            if isinstance(coordinator, HostawayShedAwareCoordinator)
            else 0
        )
    return counts


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return credential-free diagnostics for one config entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being diagnosed.

    Returns:
        A JSON-serializable payload describing the rate-limit state.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN) or {}
    entry_data: dict[str, Any] = domain_data.get(entry.entry_id) or {}
    limiter = _limiter_for(hass, entry, entry_data)
    budget = entry_budget(entry)

    rate_limit: dict[str, Any] = {
        "limiter_label": None,
        "budget_source": budget_source(entry),
        "gates": {name: _unavailable_gate(budget) for name in GATE_NAMES},
        "admitted_total": 0,
        "rate_limited_total": 0,
        "rate_limited_by_counter": dict.fromkeys(APPLIED_COUNTERS, 0),
        "shed_total": 0,
        "shed_by_coordinator": _shed_counts(entry_data),
    }

    if limiter is None:
        return {"rate_limit": rate_limit}

    snapshot: LimiterSnapshot = limiter.snapshot()
    by_counter = dict.fromkeys(APPLIED_COUNTERS, 0)
    for counter, count in snapshot.rate_limited_by_counter.items():
        key = counter if counter in by_counter else "unknown"
        by_counter[key] += count

    gates = dict(rate_limit["gates"])
    gates.update({name: _gate_payload(gate) for name, gate in snapshot.gates.items()})
    rate_limit.update(
        {
            "limiter_label": limiter.label,
            "gates": gates,
            "admitted_total": snapshot.admitted_total,
            "rate_limited_total": snapshot.rate_limited_total,
            "rate_limited_by_counter": by_counter,
            "shed_total": snapshot.shed_total,
        }
    )

    return {"rate_limit": rate_limit}
