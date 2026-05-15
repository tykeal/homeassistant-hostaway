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
    HostawayReservationLockedError,
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


class TestServiceLifecycle:
    """Tests for service registration and unregistration."""

    async def test_services_registered_on_setup(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Services are registered after entry setup."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        assert hass.services.has_service(DOMAIN, "set_door_code")
        assert hass.services.has_service(DOMAIN, "get_reservations")

    async def test_services_removed_on_last_unload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Services are removed when last entry unloads."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        assert hass.services.has_service(DOMAIN, "set_door_code")

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(DOMAIN, "set_door_code")
        assert not hass.services.has_service(DOMAIN, "get_reservations")

    async def test_services_registered_once_multi_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Services are registered once across multiple entries."""
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

        assert hass.services.has_service(DOMAIN, "set_door_code")
        assert hass.services.has_service(DOMAIN, "get_reservations")


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
        """Rejects empty door_code at schema level."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(vol.MultipleInvalid):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 99001, "door_code": ""},
                blocking=True,
            )

    async def test_validates_door_code_whitespace_only(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Rejects whitespace-only door_code at schema level."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with pytest.raises(vol.MultipleInvalid, match="non-empty"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 99001, "door_code": "   "},
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
                "set_door_code",
                {
                    "reservation_id": 99001,
                    "door_code": "1234",
                },
                blocking=True,
            )


class TestLockedReservationHandling:
    """Tests for HostawayReservationLockedError handling in set_door_code."""

    @pytest.fixture(autouse=True)
    def _clear_locked_state(self) -> Any:
        """Reset module-level rate-limit state between tests."""
        from custom_components.hostaway import services as services_mod

        services_mod._LOCKED_RESERVATION_LOG_STATE.clear()
        yield
        services_mod._LOCKED_RESERVATION_LOG_STATE.clear()

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayReservationLockedError(
            "Reservation locked: PUT /v1/reservations/59426054 "
            'returned 403; body: {"status":"fail","result":"Cannot modify"}'
        ),
    )
    async def test_door_code_locked_reservation_logs_and_returns(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Locked reservation does not raise; emits WARNING with id."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with caplog.at_level("DEBUG", logger="custom_components.hostaway.services"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 59426054, "door_code": "1234"},
                blocking=True,
            )

        mock_update.assert_called_once()
        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "59426054" in r.getMessage()
        ]
        assert warnings, (
            "Expected WARNING mentioning reservation id; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayReservationLockedError(
            "Reservation locked: PUT /v1/reservations/59426054 returned 403"
        ),
    )
    async def test_door_code_locked_rate_limit_emits_debug_on_repeat(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Second locked event within cooldown logs at DEBUG, not WARNING."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with caplog.at_level("DEBUG", logger="custom_components.hostaway.services"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 59426054, "door_code": "1234"},
                blocking=True,
            )
            warn_after_first = [
                r
                for r in caplog.records
                if r.levelname == "WARNING" and "59426054" in r.getMessage()
            ]
            assert len(warn_after_first) == 1

            caplog.clear()

            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 59426054, "door_code": "1234"},
                blocking=True,
            )

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        debugs = [
            r
            for r in caplog.records
            if r.levelname == "DEBUG"
            and "rate-limited" in r.getMessage()
            and "59426054" in r.getMessage()
        ]
        assert not warnings, (
            "Expected no WARNING on repeat call; got: "
            f"{[r.getMessage() for r in warnings]}"
        )
        assert debugs, "Expected DEBUG rate-limited message on repeat"

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayReservationLockedError(
            "Reservation locked: PUT /v1/reservations/X returned 403"
        ),
    )
    async def test_door_code_locked_rate_limit_separate_reservations(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Different reservation_ids each produce their own WARNING."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        with caplog.at_level("WARNING", logger="custom_components.hostaway.services"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 111, "door_code": "1234"},
                blocking=True,
            )
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 222, "door_code": "1234"},
                blocking=True,
            )

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("111" in m for m in warnings)
        assert any("222" in m for m in warnings)

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.update_reservation",
        new_callable=AsyncMock,
        side_effect=HostawayReservationLockedError(
            "Reservation locked: PUT /v1/reservations/333 returned 403"
        ),
    )
    async def test_door_code_locked_rate_limit_resets_after_cooldown(
        self,
        mock_update: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After cooldown expires a new WARNING is emitted for same id."""
        from custom_components.hostaway import services as services_mod

        entry = _make_entry()
        await _setup_entry(hass, entry)

        fake_now = [1000.0]

        def fake_monotonic() -> float:
            """Return the controllable monotonic clock value."""
            return fake_now[0]

        monkeypatch.setattr(services_mod.time, "monotonic", fake_monotonic)

        with caplog.at_level("WARNING", logger="custom_components.hostaway.services"):
            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 333, "door_code": "1234"},
                blocking=True,
            )
            first_warnings = [
                r
                for r in caplog.records
                if r.levelname == "WARNING" and "333" in r.getMessage()
            ]
            assert len(first_warnings) == 1

            caplog.clear()
            fake_now[0] += services_mod._LOCKED_LOG_COOLDOWN_SECONDS + 1

            await hass.services.async_call(
                DOMAIN,
                "set_door_code",
                {"reservation_id": 333, "door_code": "1234"},
                blocking=True,
            )

        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "333" in r.getMessage()
        ]
        assert warnings, "Expected WARNING after cooldown elapsed"


class TestGetReservations:
    """Tests for the hostaway.get_reservations service."""

    async def test_fires_event_with_correct_payload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Successful call fires event with snake_case payload."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        # Seed reservations coordinator cache
        reservation = _make_reservation(
            id=99001,
            guest_name="John Doe",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="confirmed",
            door_code="1234",
        )
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: [reservation]})

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
                {"listing_id": 12345, "force_refresh": True},
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
                "force_refresh": True,
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

    async def test_returns_data_from_coordinator_cache(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Returns cached data without hitting the API."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        # Seed coordinator cache
        reservation = _make_reservation(
            id=99001,
            listing_id=12345,
            guest_name="Jane Doe",
            check_in="2025-09-01",
            check_out="2025-09-05",
            status="confirmed",
            door_code="4321",
        )
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: [reservation]})

        listing = HostawayListing(id=12345, name="Beach House")
        coord = hass.data[DOMAIN][entry.entry_id]["listings_coordinator"]
        coord.async_set_updated_data({12345: listing})

        # Spy on API client to ensure it is NOT called
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        api_client.get_all_reservations = AsyncMock()

        result = await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 12345},
            blocking=True,
            return_response=True,
        )

        api_client.get_all_reservations.assert_not_called()
        assert result["listing_id"] == 12345  # type: ignore[index]
        assert result["listing_name"] == "Beach House"  # type: ignore[index]
        reservations = result["reservations"]  # type: ignore[index]
        assert len(reservations) == 1  # type: ignore[arg-type]
        assert reservations[0]["guest_name"] == "Jane Doe"  # type: ignore[call-overload, index]

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    async def test_force_refresh_hits_api(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """force_refresh=True bypasses cache and hits API."""
        mock_get.return_value = [
            _make_reservation(
                id=99002,
                guest_name="Fresh Data",
                check_in="2025-10-01",
                check_out="2025-10-05",
            ),
        ]
        entry = _make_entry()
        await _setup_entry(hass, entry)

        # Seed coordinator with stale data
        stale = _make_reservation(id=99001, guest_name="Stale Data")
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: [stale]})

        result = await hass.services.async_call(
            DOMAIN,
            "get_reservations",
            {"listing_id": 12345, "force_refresh": True},
            blocking=True,
            return_response=True,
        )

        mock_get.assert_called_once_with(12345)
        reservations = result["reservations"]  # type: ignore[index]
        assert reservations[0]["guest_name"] == "Fresh Data"  # type: ignore[call-overload, index]


class TestFindReservation:
    """Tests for the hostaway.find_reservation service."""

    async def test_finds_match_in_local_cache(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Finds a reservation from coordinator cache."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        reservation = _make_reservation(
            id=99001,
            listing_id=12345,
            guest_name="John Doe",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="confirmed",
            door_code="1234",
        )
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: [reservation]})

        result = await hass.services.async_call(
            DOMAIN,
            "find_reservation",
            {
                "guest_name": "John Doe",
                "check_in": "2025-08-01",
                "check_out": "2025-08-05",
            },
            blocking=True,
            return_response=True,
        )

        assert result["found"] is True  # type: ignore[index]
        assert result["reservation"]["id"] == 99001  # type: ignore[call-overload, index]
        assert result["reservation"]["door_code"] == "1234"  # type: ignore[call-overload, index]

    async def test_case_insensitive_match(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Guest name match is case-insensitive."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        reservation = _make_reservation(
            id=99002,
            listing_id=12345,
            guest_name="Jane Smith",
            check_in="2025-09-01",
            check_out="2025-09-05",
        )
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: [reservation]})

        result = await hass.services.async_call(
            DOMAIN,
            "find_reservation",
            {
                "guest_name": "jane smith",
                "check_in": "2025-09-01",
                "check_out": "2025-09-05",
            },
            blocking=True,
            return_response=True,
        )

        assert result["found"] is True  # type: ignore[index]
        assert result["reservation"]["id"] == 99002  # type: ignore[call-overload, index]

    async def test_returns_not_found(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Returns not-found when no match exists."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        result = await hass.services.async_call(
            DOMAIN,
            "find_reservation",
            {
                "guest_name": "Nobody Here",
                "check_in": "2025-01-01",
                "check_out": "2025-01-05",
            },
            blocking=True,
            return_response=True,
        )

        assert result["found"] is False  # type: ignore[index]
        assert result["reservation"] is None  # type: ignore[index]

    @patch(
        "custom_components.hostaway.services.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
    )
    async def test_falls_back_to_api(
        self,
        mock_get: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Falls back to API when not in cache."""
        mock_get.return_value = [
            _make_reservation(
                id=99003,
                listing_id=12345,
                guest_name="API Guest",
                check_in="2025-11-01",
                check_out="2025-11-05",
                status="confirmed",
                door_code="9999",
            ),
        ]
        entry = _make_entry()
        await _setup_entry(hass, entry)

        # Cache has no matching reservation
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({12345: []})

        result = await hass.services.async_call(
            DOMAIN,
            "find_reservation",
            {
                "guest_name": "API Guest",
                "check_in": "2025-11-01",
                "check_out": "2025-11-05",
                "listing_id": 12345,
            },
            blocking=True,
            return_response=True,
        )

        mock_get.assert_called_once_with(12345)
        assert result["found"] is True  # type: ignore[index]
        assert result["reservation"]["id"] == 99003  # type: ignore[call-overload, index]

    async def test_service_registered(
        self,
        hass: HomeAssistant,
    ) -> None:
        """find_reservation service is registered on setup."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        assert hass.services.has_service(DOMAIN, "find_reservation")

    async def test_service_removed_on_unload(
        self,
        hass: HomeAssistant,
    ) -> None:
        """find_reservation service removed on last entry unload."""
        entry = _make_entry()
        await _setup_entry(hass, entry)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(DOMAIN, "find_reservation")

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
                "find_reservation",
                {
                    "guest_name": "John Doe",
                    "check_in": "2025-08-01",
                    "check_out": "2025-08-05",
                },
                blocking=True,
                return_response=True,
            )

    async def test_multi_entry_selects_correct_cache(
        self,
        hass: HomeAssistant,
    ) -> None:
        """config_entry_id searches the correct entry's cache."""
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

        # Seed entry2's coordinator with a reservation
        reservation = _make_reservation(
            id=88001,
            listing_id=67890,
            guest_name="Entry2 Guest",
            check_in="2025-12-01",
            check_out="2025-12-05",
            status="confirmed",
        )
        res_coord = hass.data[DOMAIN][entry2.entry_id]["reservations_coordinator"]
        res_coord.async_set_updated_data({67890: [reservation]})

        result = await hass.services.async_call(
            DOMAIN,
            "find_reservation",
            {
                "guest_name": "Entry2 Guest",
                "check_in": "2025-12-01",
                "check_out": "2025-12-05",
                "config_entry_id": entry2.entry_id,
            },
            blocking=True,
            return_response=True,
        )

        assert result["found"] is True  # type: ignore[index]
