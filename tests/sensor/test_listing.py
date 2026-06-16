# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway listing sensor entities."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.coordinator import HostawayListingsCoordinator
from custom_components.hostaway.sensor.listing import (
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
)
from tests.sensor.conftest import _make_entry, _make_listing


class TestListingSensor:
    """Tests for HostawayListingSensor."""

    async def test_entity_created_per_listing_per_attribute(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Entity created per listing per attribute description."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        sensors = [
            HostawayListingSensor(coordinator, 100, entry, desc)
            for desc in LISTING_SENSOR_DESCRIPTIONS
        ]
        assert len(sensors) == len(LISTING_SENSOR_DESCRIPTIONS)

    async def test_unique_id_format(
        self,
        hass: HomeAssistant,
    ) -> None:
        """unique_id format: {unique_id}_{listing_id}_{attribute_key}."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        desc = LISTING_SENSOR_DESCRIPTIONS[0]
        sensor = HostawayListingSensor(coordinator, 100, entry, desc)
        expected = f"{entry.unique_id}_100_{desc.key}"
        assert sensor.unique_id == expected

    async def test_state_updates_when_coordinator_data_changes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """State updates when coordinator data changes."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        listing = _make_listing(100)
        api_client.get_all_listings = AsyncMock(return_value=[listing])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        assert sensor.native_value == "active"

        new_listing = _make_listing(100, status="inactive")
        api_client.get_all_listings = AsyncMock(return_value=[new_listing])
        await coordinator.async_refresh()
        assert sensor.native_value == "inactive"

    async def test_entity_unavailable_when_listing_removed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Entity becomes unavailable when listing removed."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        assert sensor.available is True

        coordinator.data = {}
        assert sensor.available is False

    async def test_base_price_sensor_value(
        self,
        hass: HomeAssistant,
    ) -> None:
        """base_price sensor returns correct value."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        price_desc = next(
            d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "base_price"
        )
        sensor = HostawayListingSensor(coordinator, 100, entry, price_desc)
        assert sensor.native_value == 150.0

    async def test_device_info(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Device info derived from listing data."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        device_info = sensor.device_info
        assert device_info is not None
        assert (DOMAIN, "test-client-id_100") in device_info["identifiers"]
        assert device_info["name"] == "Beach House"
        assert device_info["manufacturer"] == "Hostaway"
        assert device_info["model"] == "apartment"

    async def test_device_info_prefers_internal_name(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Device name uses internal_name when available."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House", internal_name="Suite 1")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        device_info = sensor.device_info
        assert device_info is not None
        assert device_info["name"] == "Suite 1"

    async def test_suggested_object_id_follows_fr007(
        self,
        hass: HomeAssistant,
    ) -> None:
        """suggested_object_id follows hostaway_<listing>_<attr>."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        assert sensor.suggested_object_id == "hostaway_beach_house_status"

    async def test_suggested_object_id_prefers_internal_name(
        self,
        hass: HomeAssistant,
    ) -> None:
        """suggested_object_id uses internal_name when set."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House", internal_name="Suite 1")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        assert sensor.suggested_object_id == "hostaway_suite_1_status"

    async def test_listing_id_diagnostic_sensor(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Listing ID sensor is a standalone diagnostic."""
        from homeassistant.const import EntityCategory

        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        lid_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "listing_id")
        sensor = HostawayListingSensor(coordinator, 100, entry, lid_desc)
        assert sensor.native_value == 100
        assert lid_desc.entity_category == EntityCategory.DIAGNOSTIC

    async def test_external_name_sensor(
        self,
        hass: HomeAssistant,
    ) -> None:
        """External name sensor returns listing.name."""
        from homeassistant.const import EntityCategory

        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Oceanview Villa")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        ext_desc = next(
            d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "external_name"
        )
        sensor = HostawayListingSensor(coordinator, 100, entry, ext_desc)
        assert sensor.native_value == "Oceanview Villa"
        assert ext_desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_all_listing_sensors_are_diagnostic(self) -> None:
        """Every listing sensor description has DIAGNOSTIC category."""
        from homeassistant.const import EntityCategory

        for desc in LISTING_SENSOR_DESCRIPTIONS:
            assert desc.entity_category == EntityCategory.DIAGNOSTIC, (
                f"{desc.key} missing EntityCategory.DIAGNOSTIC"
            )
