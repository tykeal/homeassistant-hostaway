# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Edge case tests for the Hostaway integration.

Tests token expiry during coordinator refresh, listing
deletion, pagination with >100 reservations, and concurrent
service calls without deadlock.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.models import (
    AccessToken,
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
)
from tests.helpers import (
    make_listing_response,
    make_reservation_response,
)


def _make_entry() -> MockConfigEntry:
    """Create a config entry for edge case tests.

    Returns:
        A MockConfigEntry configured for edge case testing.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (edge)",
        data={
            CONF_CLIENT_ID: "edge-client-id",
            CONF_CLIENT_SECRET: "edge-client-secret",
            CONF_SELECTED_LISTINGS: [101],
        },
        unique_id="edge-client-id",
    )


LISTING_1 = HostawayListing.from_api_response(
    make_listing_response(id=101, name="Edge Villa"),
)

RESERVATION_1 = HostawayReservation.from_api_response(
    make_reservation_response(
        id=7001,
        listingMapId=101,
        guestName="Edge Guest",
        arrivalDate="2025-11-01",
        departureDate="2025-11-05",
    ),
)


class TestTokenExpiryDuringRefresh:
    """Token invalidation during coordinator lifecycle."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[RESERVATION_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[LISTING_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_coordinator_refresh_after_token_invalidation(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator refresh succeeds after token invalidation.

        Verifies that invalidating a seeded token clears the
        cache and coordinator refreshes still succeed.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        lc = data["listings_coordinator"]

        # Seed a token so invalidate has something to clear
        tm = data["token_manager"]
        tm.seed_token(
            AccessToken(
                access_token="seeded-token",
                token_type="Bearer",
                expires_in=86400,
                issued_at=datetime.now(UTC),
            )
        )
        assert tm._cached_token is not None

        tm.invalidate()
        assert tm._cached_token is None

        # Coordinator refresh still works with mocked API
        await lc.async_refresh()
        assert lc.data is not None
        assert 101 in lc.data


class TestListingDeletedInHostaway:
    """Listing removed from Hostaway makes sensor unavailable."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[RESERVATION_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[LISTING_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_listing_removed_sensors_unavailable(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Sensors become unavailable when listing is deleted."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        lc = data["listings_coordinator"]

        # Verify listing is present
        assert 101 in lc.data

        # Second refresh returns empty (listing deleted)
        mock_listings.return_value = []
        await lc.async_refresh()

        assert 101 not in lc.data

        # Sensor states should show unavailable
        states = hass.states.async_all("sensor")
        listing_sensors = [
            s
            for s in states
            if "edge_villa" in s.entity_id and "reservation" not in s.entity_id
        ]
        assert len(listing_sensors) > 0
        for sensor in listing_sensors:
            assert sensor.state == "unavailable"


class TestLargeReservationSets:
    """Coordinator handles large numbers of reservations."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[LISTING_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_coordinator_stores_all_250_reservations(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator stores all 250 reservations from API."""
        # Build 250 unique reservations
        all_reservations = [
            HostawayReservation.from_api_response(
                make_reservation_response(
                    id=8000 + i,
                    listingMapId=101,
                    guestName=f"Guest {i}",
                    arrivalDate=f"2025-{1 + i // 30:02d}-{1 + i % 28:02d}",
                    departureDate=f"2025-{1 + i // 30:02d}-{2 + i % 28:02d}",
                ),
            )
            for i in range(250)
        ]
        mock_reservations.return_value = all_reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        data = hass.data[DOMAIN][entry.entry_id]
        rc = data["reservations_coordinator"]
        assert len(rc.data[101]) == 250


class TestConcurrentServiceCalls:
    """Concurrent service calls complete without deadlock."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[RESERVATION_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[LISTING_1],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_concurrent_set_door_code_no_deadlock(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Two concurrent set_door_code calls complete."""
        mock_update.return_value = {"id": 7001, "doorCode": "x"}

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        call_1 = hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {"reservation_id": 7001, "door_code": "1111"},
            blocking=True,
        )
        call_2 = hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {"reservation_id": 7001, "door_code": "2222"},
            blocking=True,
        )

        # Must complete within 5 seconds (no deadlock)
        await asyncio.wait_for(
            asyncio.gather(call_1, call_2),
            timeout=5.0,
        )

        assert mock_update.call_count == 2
