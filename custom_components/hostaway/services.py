# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for the Hostaway integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayResponseError,
)
from custom_components.hostaway.const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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


SERVICE_SET_DOOR_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("reservation_id"): _positive_int,
        vol.Required("door_code"): _non_empty_string,
        vol.Optional("door_code_vendor"): vol.Maybe(str),
        vol.Optional("door_code_instruction"): vol.Maybe(str),
        vol.Optional("config_entry_id"): str,
    }
)

SERVICE_GET_RESERVATIONS_SCHEMA = vol.Schema(
    {
        vol.Required("listing_id"): _positive_int,
        vol.Optional("config_entry_id"): str,
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
) -> None:
    """Handle hostaway.get_reservations service call.

    Validates input, fetches reservations from the API, resolves
    the listing name from the coordinator cache, and fires a
    ``hostaway_reservations_retrieved`` event.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Raises:
        ServiceValidationError: On invalid input.
        HomeAssistantError: On API failure.
    """
    listing_id: int = call.data["listing_id"]

    entry_data = _resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]
    listings_coordinator = entry_data["listings_coordinator"]

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

    async def _handle_get_reservations(call: ServiceCall) -> None:
        """Delegate to get_reservations handler."""
        await async_handle_get_reservations(hass, call)

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
        )
