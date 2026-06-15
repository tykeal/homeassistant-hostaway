# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Hostaway service handlers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hostaway.api.exceptions import HostawayReservationLockedError
from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_LOCKED_LOG_COOLDOWN_SECONDS = 3600
_LOCKED_RESERVATION_LOG_STATE: dict[int, float] = {}


def _prune_locked_state(now: float) -> None:
    """Drop log-state entries older than twice the cooldown.

    Keeps the in-process state bounded for long-lived HA instances
    that may see many distinct reservation IDs over time. Pruning
    is opportunistic — invoked when a new WARNING is about to be
    emitted — and uses a 2x cooldown threshold so entries are
    retained at least long enough to suppress repeats but cannot
    grow without bound.

    Args:
        now: Current ``time.monotonic()`` value.
    """
    stale_threshold = 2 * _LOCKED_LOG_COOLDOWN_SECONDS
    stale = [
        rid
        for rid, ts in _LOCKED_RESERVATION_LOG_STATE.items()
        if (now - ts) >= stale_threshold
    ]
    for rid in stale:
        del _LOCKED_RESERVATION_LOG_STATE[rid]


def _log_locked_reservation(
    reservation_id: int,
    exc: HostawayReservationLockedError,
) -> None:
    """Log a locked-reservation event with a per-reservation cooldown.

    The first failure for a given ``reservation_id`` emits a WARNING
    that includes the exception message (which already carries the
    HTTP method, path, status, and a redacted body snippet from the
    client). Subsequent failures for the same reservation within
    ``_LOCKED_LOG_COOLDOWN_SECONDS`` are demoted to DEBUG so the HA
    log is not flooded by a repeating automation (e.g., the
    ~2-minute door-code refresh loop).

    State is module-level and best-effort: a HA restart resets it,
    which is acceptable. Uses :func:`time.monotonic` so wall-clock
    changes cannot break the cooldown. The state dict is pruned
    of entries older than twice the cooldown on each WARNING
    emission so it stays bounded on long-lived instances.

    Args:
        reservation_id: Hostaway reservation ID that was rejected.
        exc: The raised locked-reservation exception. Its string
            form is appended to the WARNING for diagnostic context.
    """
    now = time.monotonic()
    last = _LOCKED_RESERVATION_LOG_STATE.get(reservation_id)
    if last is None or (now - last) >= _LOCKED_LOG_COOLDOWN_SECONDS:
        _prune_locked_state(now)
        _LOCKED_RESERVATION_LOG_STATE[reservation_id] = now
        _LOGGER.warning(
            "Skipping doorCode update for reservation %s: Hostaway "
            "refused the request as non-writable (likely "
            "channel-managed or in conflict). %s",
            reservation_id,
            exc,
        )
        return
    _LOGGER.debug(
        "Locked-reservation update suppressed (rate-limited) for reservation %s",
        reservation_id,
    )


def _resolve_entry_data(
    hass: HomeAssistant,
    call_data: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the correct config entry data for a service call.

    When multiple entries exist, the caller must provide
    ``config_entry_id`` to disambiguate.

    Args:
        hass: Home Assistant instance.
        call_data: Service call data dictionary.

    Returns:
        The runtime data dict for the resolved config entry.

    Raises:
        ServiceValidationError: If the entry cannot be resolved.
    """
    entries: dict[str, Any] = hass.data.get(DOMAIN, {})
    config_entry_id = call_data.get("config_entry_id")

    if config_entry_id:
        if config_entry_id not in entries:
            raise ServiceValidationError(
                f"Config entry {config_entry_id} not found",
            )
        result: dict[str, Any] = entries[config_entry_id]
        return result

    if len(entries) == 0:
        raise ServiceValidationError(
            "No Hostaway config entries are loaded",
        )

    if len(entries) == 1:
        first: dict[str, Any] = next(iter(entries.values()))
        return first

    raise ServiceValidationError(
        "config_entry_id required when multiple entries exist",
    )


def _get_listing_name_index(listings_coordinator: Any) -> dict[str, int]:
    """Return a cached internal-name index for listing lookups.

    Args:
        listings_coordinator: The listings coordinator for the entry.

    Returns:
        Mapping of ``internal_name`` to Hostaway listing ID.

    Raises:
        ServiceValidationError: If listings data is unavailable.
    """
    listings: dict[int, HostawayListing] | None = listings_coordinator.data
    if listings is None:
        raise ServiceValidationError(
            "Listings data not available for name resolution",
        )

    cache_key = id(listings)
    cached_key = getattr(
        listings_coordinator,
        "_hostaway_listing_name_index_key",
        None,
    )
    cached_index = getattr(
        listings_coordinator,
        "_hostaway_listing_name_index",
        None,
    )
    if cache_key != cached_key or not isinstance(cached_index, dict):
        cached_index = {
            listing.internal_name: listing.id
            for listing in listings.values()
            if listing.internal_name is not None
        }
        listings_coordinator._hostaway_listing_name_index_key = cache_key
        listings_coordinator._hostaway_listing_name_index = cached_index

    result: dict[str, int] = cached_index
    return result


def _resolve_listing_id(
    call_data: dict[str, Any],
    entry_data: dict[str, Any],
) -> int | None:
    """Resolve a listing ID from call data.

    If ``listing_id`` is provided directly, it takes precedence.
    If ``listing_name`` is provided, resolves it via a cached
    ``internal_name`` index built from the listings coordinator.

    Args:
        call_data: Service call data dictionary.
        entry_data: Runtime data for the resolved config entry.

    Returns:
        The resolved listing ID, or None if neither field present.

    Raises:
        ServiceValidationError: If listing_name is not found in
            the coordinator cache or listings data is unavailable.
    """
    if "listing_id" in call_data:
        result: int = call_data["listing_id"]
        return result

    if "listing_name" not in call_data:
        return None

    listing_name: str = call_data["listing_name"]
    listings_coordinator = entry_data["listings_coordinator"]
    listing_name_index = _get_listing_name_index(listings_coordinator)

    if listing_name in listing_name_index:
        return listing_name_index[listing_name]

    raise ServiceValidationError(
        f"Listing '{listing_name}' not found",
    )
