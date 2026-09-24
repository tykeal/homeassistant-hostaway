# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway custom-field service foundation."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.hostaway.api.custom_fields import (
    CustomFieldWriteSafetyGates,
    HostawayCustomFieldDefinition,
)
from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.services import SERVICE_DEFINITIONS
from custom_components.hostaway.services.custom_fields import (
    async_handle_get_custom_field_values,
    async_handle_get_custom_fields,
    async_handle_set_custom_field,
)
from custom_components.hostaway.services.schemas import (
    SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA,
    SERVICE_GET_CUSTOM_FIELDS_SCHEMA,
    SERVICE_SET_CUSTOM_FIELD_SCHEMA,
)


def _definition(
    custom_field_id: int,
    object_type: str = "listing",
    *,
    var_name: str = "parking_bay",
    field_type: str = "text",
    possible_values: list[str] | None = None,
) -> HostawayCustomFieldDefinition:
    """Return a custom-field definition fixture."""
    parsed = HostawayCustomFieldDefinition.from_api_dict(
        {
            "id": custom_field_id,
            "accountId": 1,
            "name": var_name.replace("_", " ").title(),
            "varName": var_name,
            "possibleValues": possible_values or [],
            "type": field_type,
            "objectType": object_type,
            "isPublic": 0,
            "sortOrder": custom_field_id,
        }
    )
    assert parsed is not None
    return parsed


async def test_listing_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Listing writes reject while live safety gate is false."""
    api_client = SimpleNamespace(get_listing=AsyncMock(), update_listing=AsyncMock())
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError, match="listing custom-field writes"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )

    api_client.get_listing.assert_not_called()
    api_client.update_listing.assert_not_called()


async def test_reservation_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Reservation writes reject while live safety gate is false."""
    api_client = SimpleNamespace(
        get_reservation=AsyncMock(),
        update_reservation=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError, match="reservation custom-field writes"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "reservation",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )

    api_client.get_reservation.assert_not_called()
    api_client.update_reservation.assert_not_called()


async def test_failed_definitions_refresh_rejects_before_gate(
    hass: HomeAssistant,
) -> None:
    """Latest definition refresh failure disables writes before gates."""
    coordinator = SimpleNamespace(last_refresh_succeeded=False)
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": coordinator,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(
        ServiceValidationError,
        match="custom field definitions refresh failed",
    ):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )


@pytest.mark.parametrize(
    ("schema", "data"),
    [
        (
            SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA,
            {"target_type": "listing", "target_id": "1"},
        ),
        (
            SERVICE_SET_CUSTOM_FIELD_SCHEMA,
            {"target_type": "listing", "target_id": 1.0, "value": "x"},
        ),
        (
            SERVICE_SET_CUSTOM_FIELD_SCHEMA,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": "2",
                "value": "x",
            },
        ),
    ],
)
def test_custom_field_schemas_use_strict_integer_ids(
    schema: vol.Schema,
    data: dict[str, object],
) -> None:
    """Custom-field schemas reject coerced identifier values."""
    with pytest.raises(vol.Invalid):
        schema(data)


async def test_bool_target_id_rejected_before_resolution(
    hass: HomeAssistant,
) -> None:
    """Boolean target identifiers are rejected as invalid."""
    with pytest.raises(ValueError, match="target_id"):
        await async_handle_set_custom_field(
            hass,
            {"target_type": "listing", "target_id": True, "value": "x"},
        )


def test_custom_field_read_service_schemas() -> None:
    """Read service schemas reject bool ids and invalid target types."""
    assert SERVICE_GET_CUSTOM_FIELDS_SCHEMA({}) == {}
    with pytest.raises(vol.Invalid):
        SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA(
            {"target_type": "listing", "target_id": True}
        )
    with pytest.raises(vol.Invalid):
        SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA({"target_type": "task", "target_id": 1})


def test_custom_field_services_registered_with_response_modes() -> None:
    """Custom-field services are declared with the required response modes."""
    definitions = {definition.name: definition for definition in SERVICE_DEFINITIONS}

    assert definitions["get_custom_fields"].supports_response is SupportsResponse.ONLY
    assert (
        definitions["get_custom_field_values"].supports_response
        is SupportsResponse.ONLY
    )


async def test_get_custom_fields_returns_cached_definitions(
    hass: HomeAssistant,
) -> None:
    """get_custom_fields returns the exact definitions envelope."""
    definitions = [
        _definition(1, "listing"),
        _definition(2, "reservation", var_name="cleaner_note"),
    ]
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=definitions)
    }

    result = await async_handle_get_custom_fields(
        hass,
        ServiceCall(hass, DOMAIN, "get_custom_fields", {}),
    )

    assert result == {
        "custom_fields": [
            {
                "customFieldId": 1,
                "varName": "parking_bay",
                "name": "Parking Bay",
                "type": "text",
                "objectType": "listing",
                "possibleValues": [],
                "isPublic": False,
                "sortOrder": 1,
            },
            {
                "customFieldId": 2,
                "varName": "cleaner_note",
                "name": "Cleaner Note",
                "type": "text",
                "objectType": "reservation",
                "possibleValues": [],
                "isPublic": False,
                "sortOrder": 2,
            },
        ]
    }


async def test_get_custom_fields_fails_closed_for_multiple_entries(
    hass: HomeAssistant,
) -> None:
    """Missing config_entry_id fails closed when multiple entries exist."""
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[_definition(1)])
    }
    hass.data[DOMAIN]["entry-2"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[_definition(2)])
    }

    with pytest.raises(
        ServiceValidationError,
        match="config_entry_id required when multiple entries exist",
    ):
        await async_handle_get_custom_fields(
            hass,
            ServiceCall(hass, DOMAIN, "get_custom_fields", {}),
        )

    result = await async_handle_get_custom_fields(
        hass,
        ServiceCall(
            hass,
            DOMAIN,
            "get_custom_fields",
            {"config_entry_id": "entry-2"},
        ),
    )
    result_data = cast(dict[str, Any], result)
    custom_fields = cast(list[dict[str, Any]], result_data["custom_fields"])
    assert custom_fields[0]["customFieldId"] == 2


async def test_get_custom_field_values_direct_read_response(
    hass: HomeAssistant,
) -> None:
    """Value service direct reads and returns resolved/unset/unresolved fields."""
    definitions = [_definition(1), _definition(2)]
    request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "result": {
                    "id": 123,
                    "customFieldValues": [
                        {"customFieldId": 1, "value": "A1"},
                        {"customFieldId": 99, "value": "mystery"},
                    ],
                },
            },
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": SimpleNamespace(_request=request),
        "custom_fields_coordinator": SimpleNamespace(data=definitions),
    }

    result = await async_handle_get_custom_field_values(
        hass,
        ServiceCall(
            hass,
            DOMAIN,
            "get_custom_field_values",
            {"target_type": "listing", "target_id": 123},
        ),
    )

    request.assert_awaited_once()
    assert request.await_args is not None
    assert request.await_args.kwargs["params"] == {"includeResources": 1}
    assert result == {
        "custom_fields": {
            "custom_parking_bay_1": {
                "customFieldId": 1,
                "varName": "parking_bay",
                "name": "Parking Bay",
                "type": "text",
                "possibleValues": [],
                "value": "A1",
                "resolved": True,
            },
            "custom_parking_bay_2": {
                "customFieldId": 2,
                "varName": "parking_bay",
                "name": "Parking Bay",
                "type": "text",
                "possibleValues": [],
                "value": None,
                "resolved": True,
            },
            "custom_field_99": {
                "customFieldId": 99,
                "value": "mystery",
                "resolved": False,
            },
        }
    }


async def test_get_custom_field_values_reuses_persisted_listing_keys(
    hass: HomeAssistant,
) -> None:
    """Listing value-service keys reuse persisted sensors without mutation."""
    definitions = [_definition(1), _definition(2, var_name="gate_code")]
    entry = SimpleNamespace(unique_id="client-id")
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "client-id_123_custom_field_1_custom_field_1",
        suggested_object_id="hostaway_beach_custom_field_1",
    )
    request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "result": {
                    "id": 123,
                    "customFieldValues": [{"customFieldId": 1, "value": "A1"}],
                },
            },
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": SimpleNamespace(_request=request),
        "custom_fields_coordinator": SimpleNamespace(data=definitions),
        "listings_coordinator": SimpleNamespace(config_entry=entry),
    }

    result = await async_handle_get_custom_field_values(
        hass,
        ServiceCall(
            hass,
            DOMAIN,
            "get_custom_field_values",
            {"target_type": "listing", "target_id": 123},
        ),
    )

    result_data = cast(dict[str, Any], result)
    custom_fields = cast(dict[str, Any], result_data["custom_fields"])
    assert "custom_field_1" in custom_fields
    assert "custom_gate_code" in custom_fields
    allocation = hass.data[DOMAIN]["entry-1"]["custom_field_key_allocations"][123]
    assert allocation.field_to_key == {1: "custom_field_1"}


async def test_get_custom_field_values_preserves_response_key_reservations(
    hass: HomeAssistant,
) -> None:
    """Listing value-service suffixes collisions across one response."""
    definitions = [_definition(1, var_name="field_2")]
    request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "result": {
                    "id": 123,
                    "customFieldValues": [{"customFieldId": 2, "value": "raw"}],
                },
            },
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": SimpleNamespace(_request=request),
        "custom_fields_coordinator": SimpleNamespace(data=definitions),
    }

    result = await async_handle_get_custom_field_values(
        hass,
        ServiceCall(
            hass,
            DOMAIN,
            "get_custom_field_values",
            {"target_type": "listing", "target_id": 123},
        ),
    )

    result_data = cast(dict[str, Any], result)
    custom_fields = cast(dict[str, Any], result_data["custom_fields"])
    assert custom_fields["custom_field_2"]["customFieldId"] == 1
    assert custom_fields["custom_field_2_2"]["customFieldId"] == 2


async def test_get_custom_field_values_missing_target_error(
    hass: HomeAssistant,
) -> None:
    """Inaccessible targets raise clear target errors."""
    request = AsyncMock(
        return_value=httpx.Response(
            404,
            json={"status": "fail", "message": "not found"},
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": SimpleNamespace(_request=request),
        "custom_fields_coordinator": SimpleNamespace(data=[]),
    }

    with pytest.raises(ServiceValidationError, match="Unable to read listing 404"):
        await async_handle_get_custom_field_values(
            hass,
            ServiceCall(
                hass,
                DOMAIN,
                "get_custom_field_values",
                {"target_type": "listing", "target_id": 404},
            ),
        )


@pytest.mark.parametrize(
    "data",
    [
        {"target_type": "listing", "target_id": 1, "value": "x"},
        {
            "target_type": "listing",
            "target_id": 1,
            "customFieldId": 1,
            "varName": "parking_bay",
            "value": "x",
        },
        {"target_type": "listing", "target_id": 1, "customFieldId": True, "value": "x"},
        {"target_type": "listing", "target_id": 1, "customFieldId": 1},
    ],
)
async def test_set_custom_field_identifier_validation(
    hass: HomeAssistant,
    data: dict[str, object],
) -> None:
    """set_custom_field rejects bad identifier combinations before reads."""
    api_client = SimpleNamespace(get_listing=AsyncMock(), update_listing=AsyncMock())
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(data=[_definition(1)]),
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises((ServiceValidationError, ValueError)):
        await async_handle_set_custom_field(hass, data)

    api_client.get_listing.assert_not_called()
    api_client.update_listing.assert_not_called()


@pytest.mark.parametrize(
    ("definition", "value"),
    [
        (_definition(1, field_type="text"), 1),
        (_definition(1, field_type="textarea"), 1),
        (_definition(1, field_type="number"), True),
        (_definition(1, field_type="dropdown", possible_values=["A"]), "B"),
    ],
)
async def test_set_custom_field_value_validation(
    hass: HomeAssistant,
    definition: HostawayCustomFieldDefinition,
    value: object,
) -> None:
    """set_custom_field validates known field types before write gates."""
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[definition]),
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": definition.custom_field_id,
                "value": value,
            },
        )
