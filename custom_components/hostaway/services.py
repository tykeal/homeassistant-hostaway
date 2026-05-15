# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for the Hostaway integration."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayReservationLockedError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayReservation
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


def _positive_int(value: Any) -> int:
    """Validate and coerce a value to a positive integer.

    Rejects booleans and floats with fractional parts to
    prevent silent coercion of unintended values.

    Args:
        value: The value to validate.

    Returns:
        The validated positive integer.

    Raises:
        vol.Invalid: If the value is not a valid positive integer.
    """
    if isinstance(value, bool):
        raise vol.Invalid("boolean is not a valid integer")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise vol.Invalid("expected a finite number")
        if value != int(value):
            raise vol.Invalid("float with fractional part is not valid")
        value = int(value)
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as exc:
            raise vol.Invalid("expected an integer") from exc
    if value <= 0:
        raise vol.Invalid("must be a positive integer")
    result: int = value
    return result


def _non_empty_string(value: Any) -> str:
    """Validate a non-empty string, stripping whitespace.

    Args:
        value: The value to validate.

    Returns:
        The stripped string.

    Raises:
        vol.Invalid: If the value is not a non-empty string.
    """
    if not isinstance(value, str):
        raise vol.Invalid("expected a string")
    stripped = value.strip()
    if not stripped:
        raise vol.Invalid("must be a non-empty string")
    return stripped


def _strict_string(value: Any) -> str:
    """Validate that a value is a string without coercion.

    Args:
        value: The value to validate.

    Returns:
        The original string value.

    Raises:
        vol.Invalid: If the value is not a string.
    """
    if not isinstance(value, str):
        raise vol.Invalid("expected a string")
    return value


SERVICE_SET_DOOR_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("reservation_id"): _positive_int,
        vol.Required("door_code"): _non_empty_string,
        vol.Optional("door_code_vendor"): vol.Maybe(_strict_string),
        vol.Optional("door_code_instruction"): vol.Maybe(
            _strict_string,
        ),
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_RESERVATIONS_SCHEMA = vol.Schema(
    {
        vol.Required("listing_id"): _positive_int,
        vol.Optional("force_refresh"): vol.Boolean(),
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_FIND_RESERVATION_SCHEMA = vol.Schema(
    {
        vol.Required("guest_name"): _non_empty_string,
        vol.Required("check_in"): _non_empty_string,
        vol.Required("check_out"): _non_empty_string,
        vol.Optional("listing_id"): _positive_int,
        vol.Optional("config_entry_id"): _strict_string,
    }
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


async def async_handle_set_door_code(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle hostaway.set_door_code service call.

    Validates inputs, builds a camelCase payload, and sends
    a PUT request to update the reservation's door code.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Raises:
        ServiceValidationError: On invalid input or missing resource.
        HomeAssistantError: On API failure.
    """
    reservation_id: int = call.data["reservation_id"]
    door_code: str = call.data["door_code"]

    payload: dict[str, Any] = {"doorCode": door_code}
    if call.data.get("door_code_vendor") is not None:
        payload["doorCodeVendor"] = call.data["door_code_vendor"]
    if call.data.get("door_code_instruction") is not None:
        payload["doorCodeInstruction"] = call.data["door_code_instruction"]

    entry_data = _resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        await api_client.update_reservation(reservation_id, payload)
    except HostawayReservationLockedError as exc:
        _log_locked_reservation(reservation_id, exc)
        return
    except HostawayResponseError as exc:
        if "not found" in str(exc).lower():
            raise ServiceValidationError(
                f"Reservation {reservation_id} not found",
            ) from exc
        raise HomeAssistantError(
            f"Failed to update reservation: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to update reservation: {exc}",
        ) from exc


async def async_handle_get_reservations(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any] | None:
    """Handle hostaway.get_reservations service call.

    Returns cached reservations when available. Falls back to
    the API when the listing is not in cache or when
    force_refresh is True. Fires a backwards-compat event and
    returns the data for SupportsResponse.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Event data dict if return_response is True, else None.

    Raises:
        ServiceValidationError: On invalid input.
        HomeAssistantError: On API failure.
    """
    listing_id: int = call.data["listing_id"]
    force_refresh: bool = call.data.get("force_refresh", False)

    entry_data = _resolve_entry_data(hass, call.data)
    listings_coordinator = entry_data["listings_coordinator"]
    reservations_coordinator = entry_data["reservations_coordinator"]

    reservations = None

    # Use cached data unless force_refresh is requested
    if not force_refresh and reservations_coordinator.data is not None:
        cached = reservations_coordinator.data.get(listing_id)
        if cached is not None:
            reservations = cached
        # listing_id not tracked locally; fall through to API

    if reservations is None:
        api_client: HostawayApiClient = entry_data["api_client"]
        try:
            reservations = await api_client.get_all_reservations(
                listing_id,
            )
        except HostawayResponseError as exc:
            if "not found" in str(exc).lower():
                _LOGGER.debug(
                    "Listing %d not found, returning empty list",
                    listing_id,
                )
                reservations = []
            else:
                raise HomeAssistantError(
                    f"Failed to fetch reservations: {exc}",
                ) from exc
        except HostawayApiError as exc:
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc

    listing_name = "Unknown"
    if listings_coordinator.data:
        listing = listings_coordinator.data.get(listing_id)
        if listing:
            listing_name = listing.name

    event_data: dict[str, Any] = {
        "listing_id": listing_id,
        "listing_name": listing_name,
        "reservations": [
            {
                "id": r.id,
                "guest_name": r.guest_name,
                "check_in": r.check_in,
                "check_out": r.check_out,
                "status": r.status,
                "door_code": r.door_code,
            }
            for r in reservations
        ],
    }
    hass.bus.async_fire(
        "hostaway_reservations_retrieved",
        event_data,
        context=call.context,
    )

    if call.return_response:
        return event_data
    return None


async def async_handle_find_reservation(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.find_reservation service call.

    Searches for a reservation by guest name and dates, first in
    the coordinator cache and optionally via the API.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Dict with found flag and reservation data if matched.

    Raises:
        ServiceValidationError: On invalid input.
        HomeAssistantError: On API failure.
    """
    guest_name: str = call.data["guest_name"]
    check_in: str = call.data["check_in"]
    check_out: str = call.data["check_out"]
    listing_id: int | None = call.data.get("listing_id")

    entry_data = _resolve_entry_data(hass, call.data)
    reservations_coordinator = entry_data["reservations_coordinator"]

    def _match(r: HostawayReservation) -> bool:
        """Check if reservation matches search criteria."""
        return (
            guest_name.lower() in r.guest_name.lower()
            and r.check_in == check_in
            and r.check_out == check_out
        )

    # Search coordinator cache
    if reservations_coordinator.data:
        if listing_id is not None:
            cached = reservations_coordinator.data.get(listing_id, [])
            for r in cached:
                if _match(r):
                    return _reservation_result(r)
        else:
            for res_list in reservations_coordinator.data.values():
                for r in res_list:
                    if _match(r):
                        return _reservation_result(r)

    # Fall back to API if listing_id provided
    if listing_id is not None:
        api_client: HostawayApiClient = entry_data["api_client"]
        try:
            reservations = await api_client.get_all_reservations(
                listing_id,
            )
        except HostawayResponseError as exc:
            if "not found" in str(exc).lower():
                return {"found": False, "reservation": None}
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        for r in reservations:
            if _match(r):
                return _reservation_result(r)

    return {"found": False, "reservation": None}


def _reservation_result(r: HostawayReservation) -> dict[str, Any]:
    """Build a successful find_reservation result dict.

    Args:
        r: The matched reservation object.

    Returns:
        Dict with found=True and reservation details.
    """
    return {
        "found": True,
        "reservation": {
            "id": r.id,
            "listing_id": r.listing_id,
            "guest_name": r.guest_name,
            "check_in": r.check_in,
            "check_out": r.check_out,
            "status": r.status,
            "door_code": r.door_code,
            "confirmation_code": r.confirmation_code,
        },
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Hostaway domain-level services.

    Idempotent: skips registration for services that already
    exist. Safe to call on every config entry setup.

    Args:
        hass: Home Assistant instance.
    """

    async def _handle_set_door_code(call: ServiceCall) -> None:
        """Delegate to set_door_code handler."""
        await async_handle_set_door_code(hass, call)

    async def _handle_get_reservations(
        call: ServiceCall,
    ) -> dict[str, Any] | None:
        """Delegate to get_reservations handler."""
        return await async_handle_get_reservations(hass, call)

    if not hass.services.has_service(DOMAIN, "set_door_code"):
        hass.services.async_register(
            DOMAIN,
            "set_door_code",
            _handle_set_door_code,
            schema=SERVICE_SET_DOOR_CODE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, "get_reservations"):
        hass.services.async_register(
            DOMAIN,
            "get_reservations",
            _handle_get_reservations,
            schema=SERVICE_GET_RESERVATIONS_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _handle_find_reservation(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Delegate to find_reservation handler."""
        return await async_handle_find_reservation(hass, call)

    if not hass.services.has_service(DOMAIN, "find_reservation"):
        hass.services.async_register(
            DOMAIN,
            "find_reservation",
            _handle_find_reservation,
            schema=SERVICE_FIND_RESERVATION_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
