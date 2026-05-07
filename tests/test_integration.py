# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for full Hostaway lifecycle.

Tests the complete flow: entry setup via MockConfigEntry,
coordinator data fetch, sensor entity creation, entity
naming (FR-007), unique_id stability across unload/reload,
and end-to-end service calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
from tests.helpers import (
    make_listing_response,
    make_reservation_response,
)

# ── Test fixtures ─────────────────────────────────────────

LISTING_1 = HostawayListing.from_api_response(
    make_listing_response(id=101, name="Beach Villa"),
)
LISTING_2 = HostawayListing.from_api_response(
    make_listing_response(id=202, name="Mountain Lodge"),
)

RESERVATION_1 = HostawayReservation.from_api_response(
    make_reservation_response(
        id=5001,
        listingMapId=101,
        guestName="Alice Smith",
        arrivalDate="2025-09-01",
        departureDate="2025-09-05",
    ),
)
RESERVATION_2 = HostawayReservation.from_api_response(
    make_reservation_response(
        id=5002,
        listingMapId=202,
        guestName="Bob Jones",
        arrivalDate="2025-10-10",
        departureDate="2025-10-14",
    ),
)


def _make_entry() -> MockConfigEntry:
    """Create a config entry selecting both test listings.

    Returns:
        A MockConfigEntry configured for integration tests.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-int)",
        data={
            CONF_CLIENT_ID: "int-client-id",
            CONF_CLIENT_SECRET: "int-client-secret",
            CONF_SELECTED_LISTINGS: [101, 202],
        },
        unique_id="int-client-id",
    )


def _reservations_both(lid: int) -> list:
    """Return reservations for both listings."""
    return [RESERVATION_1] if lid == 101 else [RESERVATION_2]


def _reservations_single(lid: int) -> list:
    """Return reservations only for listing 101."""
    return [RESERVATION_1] if lid == 101 else []


class TestFullLifecycle:
    """Integration tests for the full entry lifecycle."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_setup_creates_coordinators_and_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates coordinators with fetched data."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1, LISTING_2]
        mock_reservations.side_effect = _reservations_both

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        lc = data["listings_coordinator"]
        rc = data["reservations_coordinator"]
        assert 101 in lc.data
        assert 202 in lc.data
        assert 101 in rc.data
        assert 202 in rc.data

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_sensor_entities_created(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Sensor entities are created for each listing attribute."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1, LISTING_2]
        mock_reservations.side_effect = _reservations_both

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # 5 listing sensors + 1 reservation status per listing
        # Verify specific listing sensors for each listing
        registry = er.async_get(hass)
        for lid in (101, 202):
            for key in ["status", "base_price", "bedrooms"]:
                uid = f"{entry.unique_id}_{lid}_{key}"
                eid = registry.async_get_entity_id(
                    "sensor",
                    DOMAIN,
                    uid,
                )
                assert eid is not None, f"Missing {uid}"
        # Verify reservation status sensors exist per listing
        for lid in (101, 202):
            uid = f"{entry.unique_id}_{lid}_reservation_status"
            eid = registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                uid,
            )
            assert eid is not None, f"Missing res status {uid}"

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_unique_id_format(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Listing sensor unique_ids follow entry_uid_listingId_key."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        listing_entity = registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{entry.unique_id}_101_status",
        )
        assert listing_entity is not None

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_unique_id_stability_across_reload(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unique IDs remain stable after unload and reload."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1]
        mock_reservations.side_effect = _reservations_single

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Collect unique IDs and states before unload
        registry = er.async_get(hass)
        ids_before = {
            e.unique_id for e in registry.entities.values() if e.platform == DOMAIN
        }
        assert len(ids_before) > 0
        states_before = hass.states.async_all("sensor")
        before_ids = {s.entity_id for s in states_before}

        # Unload and reload
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify reload succeeded
        assert entry.state is ConfigEntryState.LOADED

        # Verify sensor entities restored after reload
        states_after = hass.states.async_all("sensor")
        assert len(states_after) == len(states_before)
        after_ids = {s.entity_id for s in states_after}
        assert before_ids == after_ids

        ids_after = {
            e.unique_id for e in registry.entities.values() if e.platform == DOMAIN
        }
        assert ids_before == ids_after

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_service_call_end_to_end(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """set_door_code service calls API with correct payload."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1]
        mock_reservations.side_effect = _reservations_single
        mock_update.return_value = {
            "id": 5001,
            "doorCode": "9999",
        }

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {
                "reservation_id": 5001,
                "door_code": "9999",
                "door_code_vendor": "smartlock",
            },
            blocking=True,
        )

        mock_update.assert_called_once_with(
            5001,
            {
                "doorCode": "9999",
                "doorCodeVendor": "smartlock",
            },
        )

    @patch(
        "custom_components.hostaway.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
    )
    async def test_entity_naming_fr007(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Entity IDs follow FR-007: sensor.hostaway_<slug>_<attr>."""
        mock_test.return_value = True
        mock_listings.return_value = [LISTING_1]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify via entity registry that unique_ids resolve
        # to entity_ids containing the hostaway prefix and
        # listing slug
        registry = er.async_get(hass)
        expected_keys = ["status", "base_price", "bedrooms", "bathrooms", "max_guests"]
        for key in expected_keys:
            uid = f"{entry.unique_id}_101_{key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, uid)
            assert entity_id is not None, f"Missing entity for {uid}"
            assert "hostaway" in entity_id
            assert "beach_villa" in entity_id
