# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway sensor platform setup."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.hostaway.api.custom_fields import HostawayCustomFieldDefinition
from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.sensor import async_setup_entry
from custom_components.hostaway.sensor.custom_fields import (
    HostawayListingCustomFieldSensor,
)
from custom_components.hostaway.sensor.listing import (
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
)
from tests.sensor.conftest import _make_entry, _make_listing


def _listing_with_custom_fields(
    listing_id: int,
    values: list[dict[str, object]],
) -> HostawayListing:
    """Return a listing parsed with customFieldValues."""
    return HostawayListing.from_api_response(
        {
            "id": listing_id,
            "name": "Beach House",
            "customFieldValues": values,
        }
    )


def _definition(custom_field_id: int, var_name: str) -> HostawayCustomFieldDefinition:
    """Return a listing custom-field definition."""
    parsed = HostawayCustomFieldDefinition.from_api_dict(
        {
            "id": custom_field_id,
            "accountId": 1,
            "name": var_name.replace("_", " ").title(),
            "varName": var_name,
            "possibleValues": [],
            "type": "text",
            "objectType": "listing",
            "isPublic": 0,
            "sortOrder": custom_field_id,
        }
    )
    assert parsed is not None
    return parsed


async def test_entity_ids_via_async_setup_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async_setup_entry registers and cleans up listing listeners."""
    entry = _make_entry(selected=[100, 200, 300])
    entry.add_to_hass(hass)
    api_client = AsyncMock()
    api_client.get_all_listings = AsyncMock(
        return_value=[_make_listing(100, "Beach House")]
    )
    api_client.get_all_reservations = AsyncMock(return_value=[])

    listings_coord = HostawayListingsCoordinator(hass, entry, api_client)
    await listings_coord.async_refresh()
    res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
    await res_coord.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "listings_coordinator": listings_coord,
        "reservations_coordinator": res_coord,
    }

    added_batches: list[list[Entity]] = []
    unload_callbacks: list[Callable[[], None]] = []
    listener_holder: dict[str, Callable[[], None]] = {}
    listener_active = {"value": True}

    def _async_add_entities(
        entities: Iterable[Entity],
        update_before_add: bool = False,
    ) -> None:
        """Collect entity batches registered by async_setup_entry."""
        del update_before_add
        added_batches.append(list(entities))

    def _remove_listener() -> None:
        """Mark the captured listener as removed."""
        listener_active["value"] = False

    def _async_add_listener(
        listener: Callable[[], None],
    ) -> Callable[[], None]:
        """Capture the listener that async_setup_entry registers."""
        listener_holder["listener"] = listener
        return _remove_listener

    def _async_on_unload(callback: Callable[[], None]) -> None:
        """Capture the unload callback registered on the config entry."""
        unload_callbacks.append(callback)

    monkeypatch.setattr(listings_coord, "async_add_listener", _async_add_listener)
    monkeypatch.setattr(entry, "async_on_unload", _async_on_unload)

    try:
        await async_setup_entry(
            hass,
            entry,
            cast(AddEntitiesCallback, _async_add_entities),
        )

        assert len(added_batches) == 1
        initial_entities = added_batches[0]
        assert len(initial_entities) == len(LISTING_SENSOR_DESCRIPTIONS) + 1
        assert len(unload_callbacks) == 1
        assert "listener" in listener_holder

        for entity in initial_entities:
            unique_id = entity.unique_id
            assert unique_id is not None
            assert "_100_" in unique_id
            if isinstance(entity, HostawayListingSensor):
                obj_id = entity.suggested_object_id
                assert obj_id is not None
                assert obj_id.startswith("hostaway_")

        assert listings_coord.data is not None
        assert res_coord.data is not None
        listings_coord.data[200] = _make_listing(200, "Mountain Cabin")
        res_coord.data[200] = []
        if listener_active["value"]:
            listener_holder["listener"]()

        assert len(added_batches) == 2
        new_entities = added_batches[1]
        assert len(new_entities) == len(LISTING_SENSOR_DESCRIPTIONS) + 1
        assert all(
            entity.unique_id is not None and "_200_" in entity.unique_id
            for entity in new_entities
        )

        unload_callbacks[0]()
        listings_coord.data[300] = _make_listing(300, "City Loft")
        res_coord.data[300] = []
        if listener_active["value"]:
            listener_holder["listener"]()

        assert len(added_batches) == 2
    finally:
        await listings_coord.async_shutdown()
        await res_coord.async_shutdown()


async def test_custom_field_sensors_added_initially_and_at_runtime(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async_setup_entry creates dynamic custom-field sensors without restart."""
    entry = _make_entry(selected=[100])
    entry.add_to_hass(hass)
    api_client = AsyncMock()
    api_client.get_all_listings = AsyncMock(
        return_value=[
            _listing_with_custom_fields(100, [{"customFieldId": 1, "value": "A1"}])
        ]
    )
    api_client.get_all_reservations = AsyncMock(return_value=[])
    listings_coord = HostawayListingsCoordinator(hass, entry, api_client)
    await listings_coord.async_refresh()
    res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
    await res_coord.async_refresh()
    definitions = [_definition(1, "parking_bay"), _definition(2, "gate_code")]
    definitions_coord = SimpleNamespace(
        data=definitions,
        get_definition=lambda custom_field_id, object_type: next(
            (
                definition
                for definition in definitions
                if definition.custom_field_id == custom_field_id
                and object_type == "listing"
            ),
            None,
        ),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "listings_coordinator": listings_coord,
        "reservations_coordinator": res_coord,
        "custom_fields_coordinator": definitions_coord,
    }

    added_batches: list[list[Entity]] = []
    listener_holder: dict[str, Callable[[], None]] = {}

    def _async_add_listener(listener: Callable[[], None]) -> Callable[[], None]:
        """Capture the listener that async_setup_entry registers."""
        listener_holder["listener"] = listener
        return lambda: None

    def _async_add_entities(
        entities: Iterable[Entity],
        update_before_add: bool = False,
    ) -> None:
        """Collect entity batches registered by async_setup_entry."""
        del update_before_add
        added_batches.append(list(entities))

    monkeypatch.setattr(listings_coord, "async_add_listener", _async_add_listener)

    try:
        await async_setup_entry(
            hass,
            entry,
            cast(AddEntitiesCallback, _async_add_entities),
        )

        custom_entities = [
            entity
            for entity in added_batches[0]
            if isinstance(entity, HostawayListingCustomFieldSensor)
        ]
        assert [entity.allocated_key for entity in custom_entities] == [
            "custom_parking_bay"
        ]

        assert listings_coord.data is not None
        listings_coord.data[100] = _listing_with_custom_fields(
            100,
            [
                {"customFieldId": 1, "value": "A1"},
                {"customFieldId": 2, "value": "1234"},
            ],
        )
        listener_holder["listener"]()

        runtime_custom_entities = [
            entity
            for entity in added_batches[1]
            if isinstance(entity, HostawayListingCustomFieldSensor)
        ]
        assert [entity.allocated_key for entity in runtime_custom_entities] == [
            "custom_gate_code"
        ]
    finally:
        await listings_coord.async_shutdown()
        await res_coord.async_shutdown()
