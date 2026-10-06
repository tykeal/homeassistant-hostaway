# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway custom-field service foundation."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.hostaway.api.custom_fields import (
    CustomFieldWriteGenerationRegistry,
    CustomFieldWriteSafetyGates,
    HostawayCustomFieldDefinition,
    ListingCustomFieldEvidenceState,
    ReservationCustomFieldEvidenceState,
)
from custom_components.hostaway.api.exceptions import (
    HostawayConnectionError,
    HostawayMutationResultError,
    HostawayRateLimitError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayListing, HostawayReservation
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


def _listing_with_custom_fields(
    listing_id: int,
    custom_field_values: list[dict[str, object]],
) -> HostawayListing:
    """Return a parsed listing with customFieldValues."""
    return HostawayListing.from_api_response(
        {
            "id": listing_id,
            "name": "Beach House",
            "customFieldValues": custom_field_values,
        }
    )


def _reservation_with_custom_fields(
    reservation_id: int,
    custom_field_values: list[dict[str, object]],
) -> HostawayReservation:
    """Return a parsed reservation with customFieldValues."""
    return HostawayReservation.from_api_response(
        {
            "id": reservation_id,
            "listingMapId": 123,
            "guestName": "Guest",
            "arrivalDate": "2026-01-01",
            "departureDate": "2026-01-02",
            "status": "confirmed",
            "customFieldValues": custom_field_values,
        }
    )


def _enabled_listing_gates() -> CustomFieldWriteSafetyGates:
    """Return listing write gates for the verified partial strategy."""
    gates = CustomFieldWriteSafetyGates()
    object.__setattr__(gates, "listing_partial_put_verified", True)
    object.__setattr__(gates, "listing_payload_strategy", "partial")
    return gates


def _enabled_reservation_gates() -> CustomFieldWriteSafetyGates:
    """Return reservation write gates for the verified partial strategy."""
    gates = CustomFieldWriteSafetyGates()
    object.__setattr__(gates, "reservation_no_clobber_verified", True)
    object.__setattr__(gates, "reservation_payload_strategy", "partial")
    return gates


def _listing_write_identity() -> dict[str, object]:
    """Return account-bound listing write evidence for tests."""
    return {
        "account_id": 1,
        "config_entry_id": "entry-1",
        "listing_custom_field_evidence": ListingCustomFieldEvidenceState(
            account_id=1,
            config_entry_id="entry-1",
            partial_put_verified=True,
            payload_strategy="partial",
        ),
    }


async def test_listing_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Listing writes reject while live safety gate is false."""
    api_client = SimpleNamespace(
        get_listing=AsyncMock(), update_listing_custom_fields=AsyncMock()
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(2)],
            last_refresh_succeeded=True,
        ),
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
    api_client.update_listing_custom_fields.assert_not_called()


async def test_reservation_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Reservation writes reject while live safety gate is false."""
    api_client = SimpleNamespace(
        get_reservation=AsyncMock(),
        update_reservation_custom_fields=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(2, "reservation")],
            last_refresh_succeeded=True,
        ),
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
    api_client.update_reservation_custom_fields.assert_not_called()


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


async def test_unavailable_definitions_reject_before_gate(
    hass: HomeAssistant,
) -> None:
    """Writes fail closed when no definitions are available."""
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(
            data=[],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError, match="definitions are unavailable"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )


async def test_set_custom_field_resolves_entry_before_target_id(
    hass: HomeAssistant,
) -> None:
    """set_custom_field multi-entry ambiguity fails before target_id validation."""
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[]),
    }
    hass.data[DOMAIN]["entry-2"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[]),
    }

    with pytest.raises(
        ServiceValidationError,
        match="config_entry_id required when multiple entries exist",
    ):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": True,
                "customFieldId": 1,
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
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[_definition(1)])
    }

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
    assert (
        definitions["set_custom_field"].supports_response is SupportsResponse.OPTIONAL
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


async def test_get_custom_field_values_resolves_entry_before_target_id(
    hass: HomeAssistant,
) -> None:
    """Multi-entry ambiguity fails before target_id validation."""
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[]),
    }
    hass.data[DOMAIN]["entry-2"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[]),
    }

    with pytest.raises(
        ServiceValidationError,
        match="config_entry_id required when multiple entries exist",
    ):
        await async_handle_get_custom_field_values(
            hass,
            ServiceCall(
                hass,
                DOMAIN,
                "get_custom_field_values",
                {"target_type": "listing", "target_id": True},
            ),
        )


async def test_get_custom_field_values_direct_read_response(
    hass: HomeAssistant,
) -> None:
    """Value service direct reads and returns resolved/unset/unresolved fields."""
    definitions = [_definition(1), _definition(2)]
    api_client = SimpleNamespace(
        get_listing=AsyncMock(
            return_value=_listing_with_custom_fields(
                123,
                [
                    {"customFieldId": 1, "value": "A1"},
                    {"customFieldId": 99, "value": "mystery"},
                ],
            )
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
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

    api_client.get_listing.assert_awaited_once_with(123)
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


async def test_get_custom_field_values_reservation_response(
    hass: HomeAssistant,
) -> None:
    """Value service supports reservation targets with resolved/unresolved values."""
    definitions = [_definition(5, "reservation", var_name="cleaner_note")]
    api_client = SimpleNamespace(
        get_reservation=AsyncMock(
            return_value=_reservation_with_custom_fields(
                456,
                [
                    {"customFieldId": 5, "value": "Bring linen"},
                    {"customFieldId": 99, "value": "Mystery"},
                ],
            )
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(data=definitions),
    }

    result = await async_handle_get_custom_field_values(
        hass,
        ServiceCall(
            hass,
            DOMAIN,
            "get_custom_field_values",
            {"target_type": "reservation", "target_id": 456},
        ),
    )

    api_client.get_reservation.assert_awaited_once_with(456)
    assert result == {
        "custom_fields": {
            "custom_field_5": {
                "customFieldId": 5,
                "varName": "cleaner_note",
                "name": "Cleaner Note",
                "type": "text",
                "possibleValues": [],
                "value": "Bring linen",
                "resolved": True,
            },
            "custom_field_99": {
                "customFieldId": 99,
                "value": "Mystery",
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
    api_client = SimpleNamespace(
        get_listing=AsyncMock(
            return_value=_listing_with_custom_fields(
                123,
                [{"customFieldId": 1, "value": "A1"}],
            )
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
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
    assert "custom_field_key_allocations" not in hass.data[DOMAIN]["entry-1"]


async def test_get_custom_field_values_preserves_response_key_reservations(
    hass: HomeAssistant,
) -> None:
    """Listing value-service suffixes collisions across one response."""
    definitions = [_definition(1, var_name="field_2")]
    api_client = SimpleNamespace(
        get_listing=AsyncMock(
            return_value=_listing_with_custom_fields(
                123,
                [{"customFieldId": 2, "value": "raw"}],
            )
        )
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
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
    api_client = SimpleNamespace(
        get_listing=AsyncMock(side_effect=HostawayResponseError("not found"))
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
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
    api_client = SimpleNamespace(
        get_listing=AsyncMock(), update_listing_custom_fields=AsyncMock()
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(data=[_definition(1)]),
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises((ServiceValidationError, ValueError)):
        await async_handle_set_custom_field(hass, data)

    api_client.get_listing.assert_not_called()
    api_client.update_listing_custom_fields.assert_not_called()


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


async def test_set_custom_field_var_name_rejects_bool(
    hass: HomeAssistant,
) -> None:
    """Boolean varName input is not coerced into an identifier string."""
    definition = _definition(1, var_name="True")
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": SimpleNamespace(data=[definition]),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    with pytest.raises(ServiceValidationError, match="varName must"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "varName": True,
                "value": "new",
            },
        )


async def test_set_custom_field_rejects_account_id_mismatch(
    hass: HomeAssistant,
) -> None:
    """Configured write evidence must match the API-observed account id."""
    definition = HostawayCustomFieldDefinition.from_api_dict(
        {
            "id": 1,
            "accountId": 2,
            "name": "Parking Bay",
            "varName": "parking_bay",
            "possibleValues": [],
            "type": "text",
            "objectType": "listing",
            "isPublic": 0,
            "sortOrder": 1,
        }
    )
    assert definition is not None
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(),
        update_listing_custom_fields=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[definition],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    with pytest.raises(ServiceValidationError, match="does not match"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.update_listing_custom_fields.assert_not_called()


async def test_set_custom_field_rejects_missing_account_id(
    hass: HomeAssistant,
) -> None:
    """Any definition without accountId keeps writes fail-closed."""
    bound = _definition(1)
    unbound = HostawayCustomFieldDefinition(
        custom_field_id=2,
        account_id=None,
        name="Other",
        var_name="other",
        field_type="text",
        object_type="listing",
        possible_values=[],
        is_public=False,
        sort_order=2,
    )
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(),
        update_listing_custom_fields=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[bound, unbound],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    with pytest.raises(ServiceValidationError, match="does not match"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.update_listing_custom_fields.assert_not_called()


async def test_set_custom_field_writes_listing_and_verifies_readback(
    hass: HomeAssistant,
) -> None:
    """Verified listing writes send merged arrays and return exact success."""
    before = {
        "id": 123,
        "name": "Beach House",
        "description": "unmodeled field",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    after = {
        **before,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, after]),
        update_listing_custom_fields=AsyncMock(return_value={}),
    )
    coordinator_listing = _listing_with_custom_fields(
        123,
        [{"customFieldId": 1, "value": "old"}],
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
        "listings_coordinator": SimpleNamespace(data={123: coordinator_listing}),
    }

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.update_listing_custom_fields.assert_awaited_once_with(
        123,
        {
            "customFieldValues": [
                {"customFieldId": 1, "value": "new"},
                {"customFieldId": 2, "value": "keep"},
            ]
        },
    )
    assert result == {
        "target_type": "listing",
        "target_id": 123,
        "customFieldId": 1,
        "varName": "parking_bay",
        "addressed_by": "customFieldId",
        "result": "success",
    }
    patched = hass.data[DOMAIN]["entry-1"]["listings_coordinator"].data[123]
    assert patched.custom_fields[1] == "new"


async def test_set_custom_field_transport_error_reads_back_no_retry(
    hass: HomeAssistant,
) -> None:
    """Ambiguous transport failures read back instead of retrying stale arrays."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    after = {
        **before,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "external-new"},
        ],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, after]),
        update_listing_custom_fields=AsyncMock(
            side_effect=HostawayConnectionError("response lost")
        ),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    with pytest.raises(ServiceValidationError, match="ambiguous transport/server"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.update_listing_custom_fields.assert_awaited_once()
    assert api_client.get_listing_payload.await_count == 2


async def test_set_custom_field_rate_limit_remerges_fresh_snapshot(
    hass: HomeAssistant,
) -> None:
    """Rate-limit retries re-read and preserve concurrent custom values."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    refreshed = {
        **before,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "external-new"},
        ],
    }
    after = {
        **refreshed,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "external-new"},
        ],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, refreshed, after]),
        update_listing_custom_fields=AsyncMock(
            side_effect=[HostawayRateLimitError("rate", retry_after=0), {}]
        ),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    result = await async_handle_set_custom_field(
        hass,
        {
            "target_type": "listing",
            "target_id": 123,
            "customFieldId": 1,
            "value": "new",
        },
    )

    assert result is not None
    assert result["result"] == "success"
    assert api_client.update_listing_custom_fields.await_args_list[0].args[1] == {
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ]
    }
    assert api_client.update_listing_custom_fields.await_args_list[1].args[1] == {
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "external-new"},
        ]
    }


async def test_set_custom_field_succeeds_without_local_listing_entity(
    hass: HomeAssistant,
) -> None:
    """Writes do not require an existing listing sensor entity."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    after = {
        **before,
        "customFieldValues": [{"customFieldId": 1, "value": "new"}],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, after]),
        update_listing_custom_fields=AsyncMock(return_value={}),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
        "listings_coordinator": SimpleNamespace(data={}),
    }

    result = await async_handle_set_custom_field(
        hass,
        {
            "target_type": "listing",
            "target_id": 123,
            "customFieldId": 1,
            "value": "new",
        },
    )

    assert result == {
        "target_type": "listing",
        "target_id": 123,
        "customFieldId": 1,
        "varName": "parking_bay",
        "addressed_by": "customFieldId",
        "result": "success",
    }
    api_client.update_listing_custom_fields.assert_awaited_once_with(
        123,
        {"customFieldValues": [{"customFieldId": 1, "value": "new"}]},
    )
    assert api_client.get_listing_payload.await_count == 2
    assert hass.data[DOMAIN]["entry-1"]["listings_coordinator"].data == {}


async def test_set_custom_field_verifies_malformed_success_response(
    hass: HomeAssistant,
) -> None:
    """A malformed success response still requires mandatory read-back."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    after = {
        **before,
        "customFieldValues": [{"customFieldId": 1, "value": "new"}],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, after]),
        update_listing_custom_fields=AsyncMock(
            side_effect=HostawayMutationResultError(
                "Update response missing 'result' object"
            )
        ),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    result = await async_handle_set_custom_field(
        hass,
        {
            "target_type": "listing",
            "target_id": 123,
            "customFieldId": 1,
            "value": "new",
        },
    )

    result_data = cast(dict[str, Any], result)
    assert result_data["result"] == "success"
    assert api_client.get_listing_payload.await_count == 2


async def test_set_custom_field_serializes_same_target_writes(
    hass: HomeAssistant,
) -> None:
    """Concurrent writes to one target read after the prior write completes."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old-1"},
            {"customFieldId": 2, "value": "old-2"},
        ],
    }
    after_first = {
        **before,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new-1"},
            {"customFieldId": 2, "value": "old-2"},
        ],
    }
    after_second = {
        **before,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new-1"},
            {"customFieldId": 2, "value": "new-2"},
        ],
    }
    state = {"reads": 0, "early_second_read": False}
    first_update_in_progress = False
    first_write_read_back = False
    update_payloads: list[dict[str, Any]] = []

    async def _get_listing_payload(_target_id: int) -> dict[str, Any]:
        """Return snapshots that expose un-serialized pre-read races."""
        nonlocal first_write_read_back

        state["reads"] += 1
        if state["reads"] == 1:
            return before
        if first_update_in_progress:
            state["early_second_read"] = True
            return before
        if not first_write_read_back:
            first_write_read_back = True
            return after_first
        if len(update_payloads) < 2:
            return after_first
        return after_second

    async def _update_listing(_target_id: int, payload: dict[str, Any]) -> dict:
        """Record listing update payloads."""
        nonlocal first_update_in_progress

        update_payloads.append(payload)
        if len(update_payloads) == 1:
            first_update_in_progress = True
            try:
                await asyncio.sleep(0)
            finally:
                first_update_in_progress = False
        return {}

    api_client = SimpleNamespace(
        get_listing_payload=_get_listing_payload,
        update_listing_custom_fields=_update_listing,
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1), _definition(2, var_name="second")],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
        "listings_coordinator": SimpleNamespace(data={}),
    }

    await asyncio.gather(
        async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new-1",
            },
        ),
        async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 2,
                "value": "new-2",
            },
        ),
    )

    assert state["early_second_read"] is False
    assert update_payloads[1]["customFieldValues"] == [
        {"customFieldId": 1, "value": "new-1"},
        {"customFieldId": 2, "value": "new-2"},
    ]


async def test_set_custom_field_rejects_silent_non_persistence(
    hass: HomeAssistant,
) -> None:
    """Read-back detects Hostaway success without custom-value persistence."""
    before = {
        "id": 456,
        "listingMapId": 123,
        "guestName": "Guest",
        "arrivalDate": "2026-01-01",
        "departureDate": "2026-01-02",
        "status": "confirmed",
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    after = {**before}
    api_client = SimpleNamespace(
        get_reservation_payload=AsyncMock(side_effect=[before, after]),
        update_reservation_custom_fields=AsyncMock(return_value={}),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "account_id": 1,
        "config_entry_id": "entry-1",
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1, "reservation")],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_reservation_gates(),
        "reservation_custom_field_evidence": ReservationCustomFieldEvidenceState(
            account_id=1,
            config_entry_id="entry-1",
            custom_field_values_round_trip_verified=True,
            payload_strategy="partial",
        ),
    }

    with pytest.raises(
        ServiceValidationError,
        match="success-without-custom-field-persistence bug",
    ):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "reservation",
                "target_id": 456,
                "customFieldId": 1,
                "value": "new",
            },
        )


async def test_set_custom_field_patches_verified_reservation(
    hass: HomeAssistant,
) -> None:
    """Successful reservation writes patch coordinator data and generation."""
    reservation_values = [{"customFieldId": 1, "value": "old"}]
    before = {
        "id": 456,
        "listingMapId": 123,
        "guestName": "Guest",
        "arrivalDate": "2026-01-01",
        "departureDate": "2026-01-02",
        "status": "confirmed",
        "customFieldValues": reservation_values,
    }
    after = {
        **before,
        "customFieldValues": [{"customFieldId": 1, "value": "new"}],
    }
    generation_registry = CustomFieldWriteGenerationRegistry()
    api_client = SimpleNamespace(
        get_reservation_payload=AsyncMock(side_effect=[before, after]),
        update_reservation_custom_fields=AsyncMock(return_value={}),
    )
    coordinator = SimpleNamespace(
        data={123: [_reservation_with_custom_fields(456, reservation_values)]}
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "account_id": 1,
        "config_entry_id": "entry-1",
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1, "reservation")],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_reservation_gates(),
        "custom_field_write_generations": generation_registry,
        "reservation_custom_field_evidence": ReservationCustomFieldEvidenceState(
            account_id=1,
            config_entry_id="entry-1",
            custom_field_values_round_trip_verified=True,
            payload_strategy="partial",
        ),
        "reservations_coordinator": coordinator,
    }

    result = await async_handle_set_custom_field(
        hass,
        {
            "target_type": "reservation",
            "target_id": 456,
            "customFieldId": 1,
            "value": "new",
        },
    )

    api_client.update_reservation_custom_fields.assert_awaited_once_with(
        456,
        {"customFieldValues": [{"customFieldId": 1, "value": "new"}]},
    )
    patched = coordinator.data[123][0]
    assert patched.custom_fields[1] == "new"
    assert generation_registry.current("reservation", 456) == 1
    assert result == {
        "target_type": "reservation",
        "target_id": 456,
        "customFieldId": 1,
        "varName": "parking_bay",
        "addressed_by": "customFieldId",
        "result": "success",
    }


async def test_set_custom_field_pre_write_read_failure_is_clear(
    hass: HomeAssistant,
) -> None:
    """Enabled writes fail before mutation when the pre-write read fails."""
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=HostawayResponseError("not found")),
        update_listing_custom_fields=AsyncMock(return_value={}),
    )
    coordinator_listing = _listing_with_custom_fields(
        123,
        [{"customFieldId": 1, "value": "old"}],
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
        "listings_coordinator": SimpleNamespace(data={123: coordinator_listing}),
    }

    with pytest.raises(ServiceValidationError, match="Unable to read listing 123"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.update_listing_custom_fields.assert_not_called()
    patched = hass.data[DOMAIN]["entry-1"]["listings_coordinator"].data[123]
    assert patched.custom_fields[1] == "old"


async def test_set_custom_field_api_error_does_not_publish_success(
    hass: HomeAssistant,
) -> None:
    """Mutation API errors raise clearly without local success state."""
    before = {
        "id": 123,
        "name": "Beach House",
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(return_value=before),
        update_listing_custom_fields=AsyncMock(
            side_effect=HostawayResponseError("boom")
        ),
    )
    coordinator_listing = _listing_with_custom_fields(
        123,
        [{"customFieldId": 1, "value": "old"}],
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
        "listings_coordinator": SimpleNamespace(data={123: coordinator_listing}),
    }

    with pytest.raises(ServiceValidationError, match="Unable to update listing 123"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )

    assert api_client.get_listing_payload.await_count == 1
    patched = hass.data[DOMAIN]["entry-1"]["listings_coordinator"].data[123]
    assert patched.custom_fields[1] == "old"


async def test_set_custom_field_rejects_unrelated_readback_change(
    hass: HomeAssistant,
) -> None:
    """Read-back rejects unrelated data changes without blind recovery."""
    before = {
        "id": 123,
        "name": "Beach House",
        "description": "keep",
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    after = {
        **before,
        "description": "clobbered",
        "customFieldValues": [{"customFieldId": 1, "value": "new"}],
    }
    api_client = SimpleNamespace(
        get_listing_payload=AsyncMock(side_effect=[before, after]),
        update_listing_custom_fields=AsyncMock(return_value={}),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1)],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_listing_gates(),
        **_listing_write_identity(),
    }

    with pytest.raises(ServiceValidationError, match="recovery was not attempted"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 123,
                "customFieldId": 1,
                "value": "new",
            },
        )


async def test_reservation_write_evidence_is_account_bound(
    hass: HomeAssistant,
) -> None:
    """Reservation write enablement does not cross account boundaries."""
    api_client = SimpleNamespace(
        get_reservation=AsyncMock(),
        update_reservation_custom_fields=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "account_id": 1,
        "config_entry_id": "entry-1",
        "custom_fields_coordinator": SimpleNamespace(
            data=[_definition(1, "reservation")],
            last_refresh_succeeded=True,
        ),
        "custom_field_write_safety": _enabled_reservation_gates(),
        "reservation_custom_field_evidence": ReservationCustomFieldEvidenceState(
            account_id=100,
            config_entry_id="entry-1",
            custom_field_values_round_trip_verified=True,
            payload_strategy="partial",
        ),
    }

    with pytest.raises(ServiceValidationError, match="does not match"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "reservation",
                "target_id": 456,
                "customFieldId": 1,
                "value": "new",
            },
        )

    api_client.get_reservation.assert_not_called()
    api_client.update_reservation_custom_fields.assert_not_called()


def test_service_documentation_covers_custom_field_contracts() -> None:
    """services.yaml documents the custom-field service contracts."""
    text = (
        __import__("pathlib")
        .Path("custom_components/hostaway/services.yaml")
        .read_text()
    )
    services = yaml.safe_load(text)

    for definition in SERVICE_DEFINITIONS:
        assert f"{definition.name}:" in text
    assert "custom_fields" in services["get_custom_fields"]["description"]
    assert "customFieldId" in services["get_custom_fields"]["description"]
    assert "objectType" in services["get_custom_fields"]["description"]
    assert "isPublic" in services["get_custom_fields"]["description"]
    assert "sortOrder" in services["get_custom_fields"]["description"]
    assert '{"custom_fields": []}' in services["get_custom_fields"]["description"]
    assert (
        "Task definitions are not returned"
        in services["get_custom_fields"]["description"]
    )
    assert "hidden" in services["get_custom_field_values"]["description"]
    assert "resolved" in services["get_custom_field_values"]["description"]
    assert "value" in services["get_custom_field_values"]["description"]
    assert '{"custom_fields": {}}' in services["get_custom_field_values"]["description"]
    set_description = services["set_custom_field"]["description"]
    assert "Set one Hostaway custom variable" in set_description
    assert "customFieldId" in set_description
    assert "varName" in set_description
    assert "Use null to clear" in set_description
    assert "target_type" in set_description
    assert '"target_type": "listing"' in set_description
    assert '"result": "success"' in set_description
    assert '"addressed_by": "varName"' in set_description
    assert services["set_custom_field"]["fields"]["config_entry_id"]["description"]


def test_door_code_documentation_distinguishes_built_ins() -> None:
    """set_door_code docs distinguish built-ins from custom variables."""
    text = (
        __import__("pathlib")
        .Path("custom_components/hostaway/services.yaml")
        .read_text()
    )

    assert "Set built-in Hostaway reservation door-code fields" in text
    assert "These are not custom variables" in text
    assert "set_custom_field" in text
