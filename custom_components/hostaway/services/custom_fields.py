# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for Hostaway custom field support."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.hostaway.api.custom_fields import (
    LISTING_OBJECT_TYPE,
    RESERVATION_OBJECT_TYPE,
    CustomFieldWriteSafetyGates,
    validate_identifier,
)
from custom_components.hostaway.services.helpers import _resolve_entry_data


def _call_data(call: ServiceCall | dict[str, Any]) -> dict[str, Any]:
    """Return service-call data from Home Assistant or direct tests."""
    if isinstance(call, dict):
        return call
    data: dict[str, Any] = dict(call.data)
    return data


async def async_handle_get_custom_fields(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return cached Hostaway custom-field definitions."""
    entry_data = _resolve_entry_data(hass, _call_data(call))
    coordinator = entry_data.get("custom_fields_coordinator")
    definitions = [] if coordinator is None else (coordinator.data or [])
    return {
        "custom_fields": [definition.as_service_dict() for definition in definitions]
    }


async def async_handle_get_custom_field_values(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Placeholder read handler for later phases."""
    _resolve_entry_data(hass, _call_data(call))
    return {"custom_fields": {}}


async def async_handle_set_custom_field(
    hass: HomeAssistant, call: ServiceCall | dict[str, Any]
) -> ServiceResponse:
    """Reject custom-field writes until live safety verification passes."""
    data = _call_data(call)
    target_type = data.get("target_type")
    target_id = validate_identifier(data.get("target_id"), "target_id")
    del target_id
    if target_type not in {LISTING_OBJECT_TYPE, RESERVATION_OBJECT_TYPE}:
        raise ServiceValidationError("target_type must be listing or reservation")
    entry_data = _resolve_entry_data(hass, data)
    coordinator = entry_data.get("custom_fields_coordinator")
    if coordinator is not None and not coordinator.last_refresh_succeeded:
        raise ServiceValidationError(
            "custom field definitions refresh failed; writes are disabled "
            "until the next successful refresh"
        )
    gates = entry_data.get("custom_field_write_safety")
    if not isinstance(gates, CustomFieldWriteSafetyGates):
        gates = CustomFieldWriteSafetyGates()
    if target_type == LISTING_OBJECT_TYPE and not gates.listing_partial_put_verified:
        raise ServiceValidationError(
            "listing custom-field writes are disabled until live safety "
            "verification passes"
        )
    if (
        target_type == RESERVATION_OBJECT_TYPE
        and not gates.reservation_no_clobber_verified
    ):
        raise ServiceValidationError(
            "reservation custom-field writes are disabled until live safety "
            "verification passes"
        )
    raise ServiceValidationError(
        "custom-field writes are disabled until the write path ships"
    )
