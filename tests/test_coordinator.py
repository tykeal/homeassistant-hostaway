# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway DataUpdateCoordinators."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.exceptions import HostawayApiError
from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)


def _make_entry(
    selected: list[int] | None = None,
    options: dict | None = None,
) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        selected: Selected listing IDs.
        options: Options dict overrides.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
            CONF_SELECTED_LISTINGS: selected or [100, 200],
        },
        options=options or {},
        unique_id="test-client-id",
    )


def _make_listing(listing_id: int, name: str = "Test") -> HostawayListing:
    """Create a HostawayListing for testing.

    Args:
        listing_id: The listing ID.
        name: The listing name.

    Returns:
        A HostawayListing instance.
    """
    return HostawayListing(id=listing_id, name=name)


def _make_reservation(
    res_id: int,
    listing_id: int,
    check_in: str = "2025-08-01",
    guest_name: str = "Guest",
) -> HostawayReservation:
    """Create a HostawayReservation for testing.

    Args:
        res_id: Reservation ID.
        listing_id: Associated listing ID.
        check_in: Check-in date string.
        guest_name: Guest name.

    Returns:
        A HostawayReservation instance.
    """
    return HostawayReservation(
        id=res_id,
        listing_id=listing_id,
        guest_name=guest_name,
        check_in=check_in,
        check_out="2025-08-05",
        status="confirmed",
    )


class TestHostawayListingsCoordinator:
    """Tests for HostawayListingsCoordinator."""

    async def test_successful_fetch_returns_dict_keyed_by_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Successful fetch returns dict[int, HostawayListing]."""
        entry = _make_entry(selected=[100, 200])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[
                _make_listing(100, "Beach House"),
                _make_listing(200, "Mountain Cabin"),
                _make_listing(300, "City Flat"),
            ]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert coordinator.data is not None
        assert 100 in coordinator.data
        assert 200 in coordinator.data
        assert coordinator.data[100].name == "Beach House"
        assert coordinator.data[200].name == "Mountain Cabin"

    async def test_only_includes_selected_listings(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Only listings in CONF_SELECTED_LISTINGS are returned."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[
                _make_listing(100, "Selected"),
                _make_listing(200, "Not Selected"),
            ]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert 100 in coordinator.data
        assert 200 not in coordinator.data

    async def test_configurable_poll_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Poll interval uses options, defaults to DEFAULT_SCAN_INTERVAL."""
        entry_default = _make_entry()
        entry_default.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator_default = HostawayListingsCoordinator(
            hass, entry_default, api_client
        )
        assert coordinator_default.update_interval == timedelta(
            minutes=DEFAULT_SCAN_INTERVAL
        )

        entry_custom = _make_entry(options={CONF_SCAN_INTERVAL: 10})
        entry_custom.add_to_hass(hass)
        coordinator_custom = HostawayListingsCoordinator(hass, entry_custom, api_client)
        assert coordinator_custom.update_interval == timedelta(minutes=10)

    async def test_api_error_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """API error raises UpdateFailed."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            side_effect=HostawayApiError("API down")
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert coordinator.last_update_success is False

    async def test_retains_last_good_data_on_failure(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Retains last good data on transient failure (FR-016)."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Good")]
        )

        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        assert coordinator.data == {100: _make_listing(100, "Good")}

        # Now fail
        api_client.get_all_listings = AsyncMock(
            side_effect=HostawayApiError("transient")
        )
        await coordinator.async_refresh()

        # Data retained from last successful fetch
        assert coordinator.data == {100: _make_listing(100, "Good")}


class TestHostawayReservationsCoordinator:
    """Tests for HostawayReservationsCoordinator."""

    async def test_successful_fetch_returns_dict_keyed_by_listing_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Successful fetch returns dict[int, list[HostawayReservation]]."""
        entry = _make_entry(selected=[100, 200])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(
            side_effect=lambda lid: [
                _make_reservation(1, lid, "2025-08-01"),
            ]
        )

        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert coordinator.data is not None
        assert 100 in coordinator.data
        assert 200 in coordinator.data
        assert len(coordinator.data[100]) == 1
        assert len(coordinator.data[200]) == 1

    async def test_reservations_sorted_by_check_in(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Reservations sorted by check_in date within each listing."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(
            return_value=[
                _make_reservation(2, 100, "2025-09-01"),
                _make_reservation(1, 100, "2025-08-01"),
                _make_reservation(3, 100, "2025-08-15"),
            ]
        )

        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        dates = [r.check_in for r in coordinator.data[100]]
        assert dates == ["2025-08-01", "2025-08-15", "2025-09-01"]

    async def test_configurable_poll_interval(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Poll interval uses options for reservation scan interval."""
        entry_default = _make_entry()
        entry_default.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = HostawayReservationsCoordinator(hass, entry_default, api_client)
        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_RESERVATION_SCAN_INTERVAL
        )

        entry_custom = _make_entry(options={CONF_RESERVATION_SCAN_INTERVAL: 7})
        entry_custom.add_to_hass(hass)
        coordinator_custom = HostawayReservationsCoordinator(
            hass, entry_custom, api_client
        )
        assert coordinator_custom.update_interval == timedelta(minutes=7)

    async def test_only_fetches_for_selected_listings(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Only fetches reservations for selected listings."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        # Only called for listing 100, not 200
        api_client.get_all_reservations.assert_called_once_with(100)

    async def test_api_error_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """API error raises UpdateFailed."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(
            side_effect=HostawayApiError("timeout")
        )

        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert coordinator.last_update_success is False

    async def test_handles_pagination_via_get_all_reservations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Pagination handled by get_all_reservations (tested via mock)."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        # Simulating that get_all_reservations already handles pagination
        api_client.get_all_reservations = AsyncMock(
            return_value=[
                _make_reservation(1, 100, "2025-08-01"),
                _make_reservation(2, 100, "2025-08-10"),
                _make_reservation(3, 100, "2025-08-20"),
            ]
        )

        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()

        assert len(coordinator.data[100]) == 3
