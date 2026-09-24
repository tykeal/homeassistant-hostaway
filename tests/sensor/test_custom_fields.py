# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway listing custom-field sensors."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.hostaway.api.custom_fields import (
    HostawayCustomFieldDefinition,
)
from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.coordinator import HostawayListingsCoordinator
from custom_components.hostaway.sensor.custom_fields import (
    HostawayListingCustomFieldSensor,
    ListingCustomFieldKeyAllocation,
)
from tests.sensor.conftest import _make_entry


def _definition(
    custom_field_id: int,
    var_name: str,
    *,
    name: str | None = None,
    field_type: str = "text",
    possible_values: list[str] | None = None,
    is_public: int = 0,
) -> HostawayCustomFieldDefinition:
    """Return a listing custom-field definition."""
    parsed = HostawayCustomFieldDefinition.from_api_dict(
        {
            "id": custom_field_id,
            "accountId": 1,
            "name": name or var_name.replace("_", " ").title(),
            "varName": var_name,
            "possibleValues": possible_values or [],
            "type": field_type,
            "objectType": "listing",
            "isPublic": is_public,
            "sortOrder": custom_field_id,
        }
    )
    assert parsed is not None
    return parsed


def _listing(custom_field_values: list[object]) -> HostawayListing:
    """Return a parsed listing fixture with customFieldValues."""
    return HostawayListing.from_api_response(
        {
            "id": 100,
            "name": "Beach House",
            "customFieldValues": custom_field_values,
        }
    )


def test_key_allocator_unique_duplicate_and_fallback_keys() -> None:
    """Allocator follows varName, duplicate, fallback, and suffix rules."""
    definitions = [
        _definition(1, "parking_bay"),
        _definition(2, "door code"),
        _definition(3, "door-code"),
        _definition(4, "parking_bay"),
    ]
    allocation = ListingCustomFieldKeyAllocation(
        listing_id=100,
        reserved_keys={"custom_field_99", "custom_field_99_99"},
    )

    assert allocation.allocate(1, definitions[0], definitions) == "custom_parking_bay_1"
    assert allocation.allocate(2, definitions[1], definitions) == "custom_door_code_2"
    assert allocation.allocate(3, definitions[2], definitions) == "custom_door_code_3"
    assert allocation.allocate(99, None, definitions) == "custom_field_99_99_2"
    assert allocation.allocate(1, definitions[0], definitions) == "custom_parking_bay_1"


def test_key_allocator_preserves_persisted_fallback_after_resolution() -> None:
    """A fallback-key sensor keeps its key after metadata appears later."""
    definition = _definition(9, "gate_code")
    allocation = ListingCustomFieldKeyAllocation(
        listing_id=100,
        field_to_key={9: "custom_field_9"},
        persisted_keys={9: "custom_field_9"},
        reserved_keys={"custom_field_9"},
    )

    assert allocation.allocate(9, definition, [definition]) == "custom_field_9"


async def test_listing_custom_field_sensor_resolved_and_unresolved(
    hass: HomeAssistant,
) -> None:
    """Dynamic listing sensors expose resolved and unresolved metadata."""
    entry = _make_entry(selected=[100])
    entry.add_to_hass(hass)
    listing = _listing(
        [
            {"customFieldId": 9, "value": "A1"},
            {"customFieldId": 10, "value": None},
        ]
    )
    api_client = AsyncMock()
    api_client.get_all_listings = AsyncMock(return_value=[listing])
    coordinator = HostawayListingsCoordinator(hass, entry, api_client)
    await coordinator.async_refresh()
    definitions = [
        _definition(
            9,
            "parking_bay",
            field_type="dropdown",
            possible_values=["A1"],
        )
    ]
    definitions_coordinator = SimpleNamespace(
        data=definitions,
        get_definition=lambda custom_field_id, object_type: (
            definitions[0]
            if custom_field_id == 9 and object_type == "listing"
            else None
        ),
    )

    resolved = HostawayListingCustomFieldSensor(
        coordinator,
        cast(Any, definitions_coordinator),
        100,
        entry,
        9,
        "custom_parking_bay",
    )
    unresolved = HostawayListingCustomFieldSensor(
        coordinator,
        cast(Any, definitions_coordinator),
        100,
        entry,
        10,
        "custom_field_10",
    )

    assert resolved.native_value == "A1"
    assert resolved.suggested_object_id == "hostaway_beach_house_custom_parking_bay"
    assert resolved.extra_state_attributes == {
        "customFieldId": 9,
        "varName": "parking_bay",
        "name": "Parking Bay",
        "type": "dropdown",
        "possibleValues": ["A1"],
        "value": "A1",
        "resolved": True,
    }
    assert unresolved.native_value is None
    assert unresolved.extra_state_attributes == {
        "customFieldId": 10,
        "value": None,
        "resolved": False,
    }


async def test_listing_custom_field_sensor_listens_for_definition_refresh(
    hass: HomeAssistant,
) -> None:
    """Custom-field sensors subscribe to definitions metadata refreshes."""
    entry = _make_entry(selected=[100])
    entry.add_to_hass(hass)
    api_client = AsyncMock()
    api_client.get_all_listings = AsyncMock(
        return_value=[_listing([{"customFieldId": 9, "value": "A1"}])]
    )
    coordinator = HostawayListingsCoordinator(hass, entry, api_client)
    await coordinator.async_refresh()
    listeners: list[object] = []

    def _async_add_listener(listener: object) -> object:
        """Capture a definitions coordinator listener."""
        listeners.append(listener)
        return lambda: None

    definitions_coordinator = SimpleNamespace(
        data=[],
        get_definition=lambda custom_field_id, object_type: None,
        async_add_listener=_async_add_listener,
    )
    sensor = HostawayListingCustomFieldSensor(
        coordinator,
        cast(Any, definitions_coordinator),
        100,
        entry,
        9,
        "custom_field_9",
    )
    sensor.hass = hass

    await sensor.async_added_to_hass()

    assert listeners == [sensor.async_write_ha_state]
    await coordinator.async_shutdown()
