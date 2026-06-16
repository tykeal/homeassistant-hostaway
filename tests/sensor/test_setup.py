# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway sensor platform setup."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.sensor import async_setup_entry
from custom_components.hostaway.sensor.listing import (
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
)
from tests.sensor.conftest import _make_entry, _make_listing


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
