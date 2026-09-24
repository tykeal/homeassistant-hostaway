# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for Hostaway custom field support."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.hostaway.api.custom_fields import (
    LISTING_OBJECT_TYPE,
    RESERVATION_OBJECT_TYPE,
    SUPPORTED_OBJECT_TYPES,
    CustomFieldDefinitionError,
    CustomFieldWriteSafetyGates,
    HostawayCustomFieldCollection,
    HostawayCustomFieldDefinition,
    definitions_for_object_type,
    lookup_definition_by_id,
    read_listing_with_custom_fields,
    read_reservation_with_custom_fields,
    resolve_var_name,
    validate_custom_field_value,
    validate_identifier,
)
from custom_components.hostaway.api.exceptions import HostawayApiError
from custom_components.hostaway.sensor.custom_fields import (
    ListingCustomFieldKeyAllocation,
)
from custom_components.hostaway.services.helpers import _resolve_entry_data


@dataclass(frozen=True)
class _ValueResponseContext:
    """Shared context for custom-field value response key allocation."""

    hass: HomeAssistant
    entry_data: dict[str, Any]
    target_type: str
    target_id: int
    definitions: list[HostawayCustomFieldDefinition]


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
        "custom_fields": [
            definition.as_service_dict()
            for definition in definitions
            if definition.object_type in SUPPORTED_OBJECT_TYPES
        ]
    }


async def async_handle_get_custom_field_values(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return custom-field values for one listing or reservation target."""
    data = _call_data(call)
    target_type = _validate_target_type(data.get("target_type"))
    target_id = validate_identifier(data.get("target_id"), "target_id")
    entry_data = _resolve_entry_data(hass, data)
    definitions = _entry_definitions(entry_data)
    raw_object = await _read_target(entry_data, target_type, target_id)
    collection = HostawayCustomFieldCollection.from_object(raw_object)
    return cast(
        ServiceResponse,
        {
            "custom_fields": _custom_field_values_response(
                hass,
                entry_data,
                target_type,
                target_id,
                collection,
                definitions,
            )
        },
    )


async def async_handle_set_custom_field(
    hass: HomeAssistant, call: ServiceCall | dict[str, Any]
) -> ServiceResponse:
    """Reject custom-field writes until live safety verification passes."""
    data = _call_data(call)
    target_type = _validate_target_type(data.get("target_type"))
    target_id = validate_identifier(data.get("target_id"), "target_id")
    del target_id
    entry_data = _resolve_entry_data(hass, data)
    _validate_set_request(data, entry_data, target_type)
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


def _validate_target_type(value: object) -> str:
    """Return a supported target type or raise a service validation error."""
    if value not in {LISTING_OBJECT_TYPE, RESERVATION_OBJECT_TYPE}:
        raise ServiceValidationError("target_type must be listing or reservation")
    return str(value)


def _entry_definitions(
    entry_data: dict[str, Any],
) -> list[HostawayCustomFieldDefinition]:
    """Return cached custom-field definitions for a runtime entry."""
    coordinator = entry_data.get("custom_fields_coordinator")
    if coordinator is None:
        return []
    definitions: list[HostawayCustomFieldDefinition] = list(
        getattr(coordinator, "data", None) or []
    )
    return definitions


async def _read_target(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> dict[str, Any]:
    """Read one Hostaway target with custom-field resources included."""
    api_client = entry_data.get("api_client")
    request = getattr(api_client, "_request", None)
    if request is None:
        raise ServiceValidationError("Hostaway API client is not available")
    try:
        if target_type == LISTING_OBJECT_TYPE:
            return await read_listing_with_custom_fields(request, target_id)
        return await read_reservation_with_custom_fields(request, target_id)
    except HostawayApiError as exc:
        raise ServiceValidationError(
            f"Unable to read {target_type} {target_id}: {exc}"
        ) from exc


def _custom_field_values_response(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    collection: HostawayCustomFieldCollection,
    definitions: list[HostawayCustomFieldDefinition],
) -> dict[str, dict[str, Any]]:
    """Build the exact get_custom_field_values response mapping."""
    object_definitions = definitions_for_object_type(definitions, target_type)
    definition_by_id = {
        definition.custom_field_id: definition for definition in object_definitions
    }
    custom_field_ids = set(definition_by_id)
    custom_field_ids.update(collection.values)
    result: dict[str, dict[str, Any]] = {}
    context = _ValueResponseContext(
        hass=hass,
        entry_data=entry_data,
        target_type=target_type,
        target_id=target_id,
        definitions=object_definitions,
    )
    for custom_field_id in sorted(custom_field_ids):
        definition = definition_by_id.get(custom_field_id)
        value = collection.values.get(custom_field_id)
        key = _response_key(
            context,
            custom_field_id,
            definition,
        )
        if definition is None:
            result[key] = {
                "customFieldId": custom_field_id,
                "value": None if value is None else value.value,
                "resolved": False,
            }
            continue
        result[key] = {
            "customFieldId": custom_field_id,
            "varName": definition.var_name,
            "name": definition.name,
            "type": definition.field_type,
            "possibleValues": list(definition.possible_values),
            "value": None if value is None else value.value,
            "resolved": True,
        }
    return result


def _response_key(
    context: _ValueResponseContext,
    custom_field_id: int,
    definition: HostawayCustomFieldDefinition | None,
) -> str:
    """Return a response key for listing or reservation custom-field values."""
    if context.target_type == RESERVATION_OBJECT_TYPE:
        return f"custom_field_{custom_field_id}"
    allocation = _listing_allocation(
        context.hass,
        context.entry_data,
        context.target_id,
    )
    preview = allocation.clone()
    return preview.allocate(custom_field_id, definition, context.definitions)


def _listing_allocation(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    listing_id: int,
) -> ListingCustomFieldKeyAllocation:
    """Return persistent allocation state for a listing without mutating it."""
    allocations: dict[int, ListingCustomFieldKeyAllocation] = entry_data.setdefault(
        "custom_field_key_allocations",
        {},
    )
    allocation = allocations.get(listing_id)
    if allocation is not None:
        return allocation
    entry = getattr(entry_data.get("listings_coordinator"), "config_entry", None)
    if entry is None:
        allocation = ListingCustomFieldKeyAllocation(listing_id=listing_id)
    else:
        allocation = ListingCustomFieldKeyAllocation.from_entity_registry(
            hass,
            entry,
            listing_id,
        )
    allocations[listing_id] = allocation
    return allocation


def _validate_set_request(
    data: dict[str, Any],
    entry_data: dict[str, Any],
    target_type: str,
) -> HostawayCustomFieldDefinition | None:
    """Validate set_custom_field addressing and local value constraints."""
    has_id = "customFieldId" in data
    has_var_name = "varName" in data
    if has_id == has_var_name:
        raise ServiceValidationError("exactly one field identifier is required")
    if "value" not in data:
        raise ServiceValidationError("value is required")
    definitions = _entry_definitions(entry_data)
    if not definitions:
        return None
    try:
        if has_id:
            definition = lookup_definition_by_id(
                definitions,
                validate_identifier(data.get("customFieldId"), "customFieldId"),
                target_type,
            )
        else:
            definition = resolve_var_name(
                definitions,
                str(data.get("varName")),
                target_type,
            )
        validate_custom_field_value(definition, data["value"])
    except (CustomFieldDefinitionError, ValueError) as exc:
        raise ServiceValidationError(str(exc)) from exc
    return definition
