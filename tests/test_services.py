# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway service handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.exceptions import (
    HostawayRateLimitError,
    HostawayResponseError,
)
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


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-cli...)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
            CONF_SELECTED_LISTINGS: [12345],
        },
        unique_id="test-client-id",
        **overrides,  # type: ignore[arg-type]
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry with mocked API calls.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to set up.
    """
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.hostaway.HostawayApiClient.test_connection",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.hostaway.HostawayApiClient.get_all_listings",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "custom_components.hostaway.HostawayApiClient.get_all_reservations",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _make_reservation(**overrides: object) -> HostawayReservation:
    """Create a test HostawayReservation.

    Args:
        **overrides: Fields to override on the default reservation.

    Returns:
        A HostawayReservation instance.
    """
    defaults: dict[str, object] = {
        "id": 99001,
        "listing_id": 12345,
        "guest_name": "John Doe",
        "check_in": "2025-08-01",
        "check_out": "2025-08-05",
        "status": "confirmed",
        "door_code": "1234",
    }
    defaults.update(overrides)
    return HostawayReservation(**defaults)  # type: ignore[arg-type]


class TestSetDoorCode:
    """Tests for the hostaway.set_door_code service."""

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        return_value={"id": 99001, "doorCode": "1234"},
    )
    async def test_successful_door_code_update(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful call invokes update_reservation with doorCode."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        await hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {"reservation_id": 99001, "door_code": "1234"},
            blocking=True,
        )

        mock_update.assert_called_once_with(
            99001,
            {"doorCode": "1234"},
        )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        return_value={"id": 99001, "doorCode": "5678"},
    )
    async def test_with_optional_fields(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Optional vendor and instruction are included when provided."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        await hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {
                "reservation_id": 99001,
                "door_code": "5678",
                "door_code_vendor": "Yale",
                "door_code_instruction": "Use keypad",
            },
            blocking=True,
        )

        mock_update.assert_called_once_with(
            99001,
            {
                "doorCode": "5678",
                "doorCodeVendor": "Yale",
                "doorCodeInstruction": "Use keypad",
            },
        )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        return_value={"id": 99001, "doorCode": "1234"},
    )
    async def test_optional_fields_omitted_when_none(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Optional fields are excluded from payload when not provided."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        await hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {"reservation_id": 99001, "door_code": "1234"},
            blocking=True,
        )

        payload = mock_update.call_args[0][1]
        assert "doorCodeVendor" not in payload
        assert "doorCodeInstruction" not in payload

    async def test_validates_reservation_id_positive(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Rejects non-positive reservation_id at schema level."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(vol.MultipleInvalid, match="positive"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 0, "door_code": "1234"},
                blocking=True,
            )

        with pytest.raises(vol.MultipleInvalid, match="positive"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": -1, "door_code": "1234"},
                blocking=True,
            )

    async def test_validates_door_code_non_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Raises ServiceValidationError for empty door_code."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(ServiceValidationError, match="non-empty"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 99001, "door_code": ""},
                blocking=True,
            )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayResponseError("Resource not found: /v1/reservations/99999"),
    )
    async def test_api_404_raises_service_validation_error(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """API 404 raises ServiceValidationError with helpful message."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(ServiceValidationError, match="not found"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 99999, "door_code": "1234"},
                blocking=True,
            )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayRateLimitError("Rate limit exceeded", retry_after=60.0),
    )
    async def test_rate_limit_raises_ha_error(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Rate limit error propagates as HomeAssistantError."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(HomeAssistantError, match="update reservation"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 99001, "door_code": "1234"},
                blocking=True,
            )

    async def test_multi_entry_selects_correct_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """config_entry_id selects correct entry's API client."""
        entry1 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-1)",
            data={
                CONF_CLIENT_ID: "test-client-id-1",
                CONF_CLIENT_SECRET: "test-client-secret-1",
                CONF_SELECTED_LISTINGS: [12345],
            },
            unique_id="client-1",
        )
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
                CONF_SELECTED_LISTINGS: [67890],
            },
            unique_id="client-2",
        )
        await _setup_entry(hass, entry1)
        await _setup_entry(hass, entry2)

        # Spy on the specific api_client for entry2
        entry2_client = hass.data[DOMAIN][entry2.entry_id]["api_client"]
        mock_update = AsyncMock(
            return_value={"id": 99001, "doorCode": "1234"},
        )
        entry2_client.update_reservation = mock_update

        await hass.services.async_call(
            DOMAIN,
            "set_door_code",
            {
                "reservation_id": 99001,
                "door_code": "1234",
                "config_entry_id": entry2.entry_id,
            },
            blocking=True,
        )

        mock_update.assert_called_once_with(
            99001,
            {"doorCode": "1234"},
        )


class TestGetReservations:
    """Tests for the hostaway.get_reservations service."""

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    async def test_fires_event_with_correct_payload(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful call fires event with snake_case payload."""
        mock_get.return_value = [
            _make_reservation(
                id=99001,
                guest_name="John Doe",
                check_in="2025-08-01",
                check_out="2025-08-05",
                status="confirmed",
                door_code="1234",
            ),
        ]

        entry = _make_entry()
        await _setup_entry(hass, entry)

        # Inject a listing into the coordinator cache
        listing = HostawayListing(id=12345, name="Beach House")
        coord = hass.data[DOMAIN][entry.entry_id]["listings_coordinator"]
        coord.async_set_updated_data({12345: listing})

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 12345},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(events) == 1
        evt = events[0]
        assert evt["listing_id"] == 12345
        assert evt["listing_name"] == "Beach House"
        assert len(evt["reservations"]) == 1
        res = evt["reservations"][0]
        assert res["id"] == 99001
        assert res["guest_name"] == "John Doe"
        assert res["check_in"] == "2025-08-01"
        assert res["check_out"] == "2025-08-05"
        assert res["status"] == "confirmed"
        assert res["door_code"] == "1234"

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    async def test_listing_name_from_coordinator_cache(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Event payload includes listing_name from coordinator."""
        mock_get.return_value = []
        entry = _make_entry()
        await _setup_entry(hass, entry)

        listing = HostawayListing(id=12345, name="Mountain Cabin")
        coord = hass.data[DOMAIN][entry.entry_id]["listings_coordinator"]
        coord.async_set_updated_data({12345: listing})

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 12345},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert events[0]["listing_name"] == "Mountain Cabin"

    async def test_validates_listing_id_positive(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Rejects non-positive listing_id at schema level."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(vol.MultipleInvalid, match="positive"):
            await hass.services.async_call(
                DOMAIN,
                "get_reservations",
                {"listing_id": 0},
                blocking=True,
            )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    async def test_empty_reservations_fires_event(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Empty reservation list fires event with empty list."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 12345},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0]["reservations"] == []

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    async def test_listing_name_fallback_unknown(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """listing_name falls back to 'Unknown' when not in cache."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 99999},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert events[0]["listing_name"] == "Unknown"

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        side_effect=HostawayResponseError("Server error"),
    )
    async def test_api_error_raises_ha_error(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """API error propagates as HomeAssistantError."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(HomeAssistantError, match="fetch reservations"):
            await hass.services.async_call(
                DOMAIN,
                "get_reservations",
                {"listing_id": 12345},
                blocking=True,
            )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        side_effect=HostawayResponseError("Resource not found: /v1/reservations"),
    )
    async def test_404_listing_fires_empty_event(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """404 listing fires event with empty reservations list."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 99999},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0]["listing_id"] == 99999
        assert events[0]["reservations"] == []

    async def test_multi_entry_selects_correct_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """config_entry_id selects correct entry's client."""
        entry1 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-1)",
            data={
                CONF_CLIENT_ID: "test-client-id-1",
                CONF_CLIENT_SECRET: "test-client-secret-1",
                CONF_SELECTED_LISTINGS: [12345],
            },
            unique_id="client-1",
        )
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
                CONF_SELECTED_LISTINGS: [67890],
            },
            unique_id="client-2",
        )
        await _setup_entry(hass, entry1)
        await _setup_entry(hass, entry2)

        mock_get = AsyncMock(return_value=[])
        entry2_client = hass.data[DOMAIN][entry2.entry_id]["api_client"]
        entry2_client.get_all_reservations = mock_get

        events: list[Mapping[str, Any]] = []
        hass.bus.async_listen(
            "hostaway_reservations_retrieved",
            lambda evt: events.append(evt.data),
        )

        await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {
                "listing_id": 67890,
                "config_entry_id": entry2.entry_id,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        mock_get.assert_called_once_with(67890)
        assert len(events) == 1

    async def test_multi_entry_no_id_raises_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Missing config_entry_id with multiple entries raises."""
        entry1 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-1)",
            data={
                CONF_CLIENT_ID: "test-client-id-1",
                CONF_CLIENT_SECRET: "test-client-secret-1",
                CONF_SELECTED_LISTINGS: [12345],
            },
            unique_id="client-1",
        )
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (client-2)",
            data={
                CONF_CLIENT_ID: "test-client-id-2",
                CONF_CLIENT_SECRET: "test-client-secret-2",
                CONF_SELECTED_LISTINGS: [67890],
            },
            unique_id="client-2",
        )
        await _setup_entry(hass, entry1)
        await _setup_entry(hass, entry2)

        with pytest.raises(
            ServiceValidationError,
            match="config_entry_id required",
        ):
            await hass.services.async_call(
                DOMAIN,
                "get_reservations",
                {"listing_id": 12345},
                blocking=True,
            )
