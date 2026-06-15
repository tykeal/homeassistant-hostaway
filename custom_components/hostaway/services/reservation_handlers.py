# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation-oriented Hostaway service handlers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayReservationLockedError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayReservation

from . import helpers

_LOGGER = logging.getLogger(__name__)


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

    entry_data = helpers._resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        await api_client.update_reservation(reservation_id, payload)
    except HostawayReservationLockedError as exc:
        helpers._log_locked_reservation(reservation_id, exc)
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

    entry_data = helpers._resolve_entry_data(hass, call.data)
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

    entry_data = helpers._resolve_entry_data(hass, call.data)
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
