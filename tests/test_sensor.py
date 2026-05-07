# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
)
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.sensor import (
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
    HostawayReservationSensor,
)


def _make_entry(
    selected: list[int] | None = None,
) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        selected: Selected listing IDs.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
            CONF_SELECTED_LISTINGS: selected or [100],
        },
        options={},
        unique_id="test-client-id",
    )


def _make_listing(listing_id: int = 100, name: str = "Beach House") -> HostawayListing:
    """Create a HostawayListing for testing.

    Args:
        listing_id: The listing ID.
        name: The listing name.

    Returns:
        A HostawayListing instance.
    """
    return HostawayListing(
        id=listing_id,
        name=name,
        status="active",
        property_type="apartment",
        bedrooms=2,
        bathrooms=1.5,
        max_guests=4,
        base_price=150.0,
        currency="USD",
    )


def _make_reservation(
    res_id: int = 1001,
    listing_id: int = 100,
    guest_name: str = "John Doe",
    check_in: str = "2025-08-01",
    check_out: str = "2025-08-05",
    status: str = "confirmed",
    door_code: str | None = "1234",
    door_code_vendor: str | None = "smartlock",
    door_code_instruction: str | None = "Use keypad",
    num_guests: int | None = 3,
) -> HostawayReservation:
    """Create a HostawayReservation for testing.

    Args:
        res_id: Reservation ID.
        listing_id: Associated listing ID.
        guest_name: Guest name.
        check_in: Check-in date.
        check_out: Check-out date.
        status: Reservation status.
        door_code: Door code.
        door_code_vendor: Door code vendor.
        door_code_instruction: Door code instruction.
        num_guests: Number of guests.

    Returns:
        A HostawayReservation instance.
    """
    return HostawayReservation(
        id=res_id,
        listing_id=listing_id,
        guest_name=guest_name,
        check_in=check_in,
        check_out=check_out,
        status=status,
        door_code=door_code,
        door_code_vendor=door_code_vendor,
        door_code_instruction=door_code_instruction,
        num_guests=num_guests,
    )


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
        """unique_id format: {entry_id}_{listing_id}_{attribute_key}."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        desc = LISTING_SENSOR_DESCRIPTIONS[0]  # status
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

        # Find the status description
        status_desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        sensor = HostawayListingSensor(coordinator, 100, entry, status_desc)
        assert sensor.native_value == "active"

        # Update with new listing data
        new_listing = HostawayListing(id=100, name="Beach House", status="inactive")
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

        # Remove the listing from data
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
        assert (DOMAIN, "100") in device_info["identifiers"]
        assert device_info["name"] == "Beach House"
        assert device_info["manufacturer"] == "Hostaway"
        assert device_info["model"] == "apartment"


class TestReservationSensor:
    """Tests for HostawayReservationSensor."""

    async def test_state_is_guest_name(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Entity state is guest_name."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(1001, 100, "John Doe")
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationSensor(
            res_coord, listings_coord, reservation, entry
        )
        assert sensor.native_value == "John Doe"

    async def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Extra state attributes include expected fields."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(
            1001,
            100,
            "Jane Smith",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="confirmed",
            door_code="5678",
            door_code_vendor="yale",
            door_code_instruction="Side door",
            num_guests=2,
        )
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationSensor(
            res_coord, listings_coord, reservation, entry
        )
        attrs = sensor.extra_state_attributes
        assert attrs["check_in"] == "2025-08-01"
        assert attrs["check_out"] == "2025-08-05"
        assert attrs["status"] == "confirmed"
        assert attrs["door_code"] == "5678"
        assert attrs["door_code_vendor"] == "yale"
        assert attrs["door_code_instruction"] == "Side door"
        assert attrs["num_guests"] == 2

    async def test_unique_id_format(
        self,
        hass: HomeAssistant,
    ) -> None:
        """unique_id: {entry_id}_{reservation_id}."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(1001, 100)
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationSensor(
            res_coord, listings_coord, reservation, entry
        )
        expected = f"{entry.unique_id}_1001"
        assert sensor.unique_id == expected

    async def test_status_change_updates_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Status change updates entity attributes."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(1001, 100, status="confirmed")
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationSensor(
            res_coord, listings_coord, reservation, entry
        )
        assert sensor.extra_state_attributes["status"] == "confirmed"

        # Update reservation with new status
        updated_res = _make_reservation(1001, 100, status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[updated_res])
        await res_coord.async_refresh()

        # Sensor reads from coordinator data
        sensor2 = HostawayReservationSensor(
            res_coord, listings_coord, updated_res, entry
        )
        assert sensor2.extra_state_attributes["status"] == "checked_in"

    async def test_device_info_from_listings_coordinator(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Device info comes from listings coordinator."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(1001, 100)
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House")]
        )
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationSensor(
            res_coord, listings_coord, reservation, entry
        )
        device_info = sensor.device_info
        assert device_info is not None
        assert (DOMAIN, "100") in device_info["identifiers"]
        assert device_info["name"] == "Beach House"
