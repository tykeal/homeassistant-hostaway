# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway DataUpdateCoordinators."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.hostaway.api.const import RATE_LIMIT_WINDOW_SECONDS
from custom_components.hostaway.api.custom_fields import (
    CustomFieldWriteGenerationRegistry,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayRateLimitShedError,
    HostawayRateLimitWaitTimeout,
)
from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.api.rate_limit.context import (
    FIRST_REFRESH_POLICY,
    SCHEDULED_POLICY,
    WaitPolicy,
    current_request_context,
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
    _DEFINITION_RETRY_DELAYS,
    HostawayCustomFieldsCoordinator,
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from tests.api.test_rate_limit import make_limiter, settle
from tests.helpers import FakeClock

_COORDINATOR_LOGGER = "custom_components.hostaway.coordinator"


def _shed() -> HostawayRateLimitShedError:
    """Build the signal a limiter raises when it drops a refresh.

    Returns:
        A shed error that waited two seconds.
    """
    return HostawayRateLimitShedError("no capacity", waited=2.0)


def _definition_payload() -> dict:
    """Build one Hostaway custom-field definition payload.

    Returns:
        A definition dict as the API returns it.
    """
    return {
        "id": 1,
        "accountId": 2,
        "name": "Field",
        "varName": "field",
        "possibleValues": None,
        "type": "text",
        "objectType": "listing",
        "isPublic": 1,
        "sortOrder": 1,
    }


def _definitions_response(results: list[dict]) -> httpx.Response:
    """Build a definitions listing response.

    Args:
        results: The definition payloads to return.

    Returns:
        An httpx response carrying those definitions.
    """
    return httpx.Response(
        200,
        json={
            "status": "success",
            "result": results,
            "limit": 500,
            "page": 1,
            "totalPages": 1,
        },
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

    async def test_inflight_refresh_preserves_written_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A stale listing poll cannot overwrite a verified write patch."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        registry = CustomFieldWriteGenerationRegistry()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "custom_field_write_generations": registry,
        }
        api_client = AsyncMock()
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        coordinator.data = {100: _make_listing(100, "Old")}

        async def _get_all_listings() -> list[HostawayListing]:
            """Return stale data after a write patches and advances generation."""
            coordinator.data = {100: _make_listing(100, "Patched")}
            registry.advance("listing", 100)
            return [_make_listing(100, "Stale")]

        api_client.get_all_listings = _get_all_listings

        await coordinator.async_refresh()

        assert coordinator.data == {100: _make_listing(100, "Patched")}


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

    async def test_inflight_refresh_preserves_written_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A stale reservation poll cannot overwrite a verified write patch."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        registry = CustomFieldWriteGenerationRegistry()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "custom_field_write_generations": registry,
        }
        api_client = AsyncMock()
        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        coordinator.data = {100: [_make_reservation(1, 100, guest_name="Old")]}

        async def _get_all_reservations(listing_id: int) -> list[HostawayReservation]:
            """Return stale data after a write patches and advances generation."""
            coordinator.data = {100: [_make_reservation(1, 100, guest_name="Patched")]}
            registry.advance("reservation", 1)
            return [_make_reservation(1, listing_id, guest_name="Stale")]

        api_client.get_all_reservations = _get_all_reservations

        await coordinator.async_refresh()

        assert coordinator.data == {
            100: [_make_reservation(1, 100, guest_name="Patched")]
        }

    async def test_late_inflight_refresh_preserves_written_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A later listing fetch cannot publish an earlier stale reservation."""
        entry = _make_entry(selected=[100, 200])
        entry.add_to_hass(hass)
        registry = CustomFieldWriteGenerationRegistry()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "custom_field_write_generations": registry,
        }
        api_client = AsyncMock()
        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        coordinator.data = {
            100: [_make_reservation(1, 100, guest_name="Old")],
            200: [_make_reservation(2, 200, guest_name="Other")],
        }

        async def _get_all_reservations(listing_id: int) -> list[HostawayReservation]:
            """Patch listing 100 after its stale fetch has already completed."""
            if listing_id == 200:
                coordinator.data = {
                    100: [_make_reservation(1, 100, guest_name="Patched")],
                    200: [_make_reservation(2, 200, guest_name="Other")],
                }
                registry.advance("reservation", 1)
            return [_make_reservation(listing_id // 100, listing_id, "2025-08-01")]

        api_client.get_all_reservations = _get_all_reservations

        await coordinator.async_refresh()

        assert coordinator.data[100] == [
            _make_reservation(1, 100, guest_name="Patched")
        ]

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


class TestHostawayCustomFieldsCoordinator:
    """Tests for custom-field definitions coordinator foundation."""

    async def test_refresh_state_and_stale_retention(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator tracks refresh success and retains stale data."""
        from custom_components.hostaway.api.exceptions import HostawayApiError
        from custom_components.hostaway.coordinator import (
            HostawayCustomFieldsCoordinator,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client._request = AsyncMock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "status": "success",
                    "result": [
                        {
                            "id": 1,
                            "accountId": 2,
                            "name": "Field",
                            "varName": "field",
                            "possibleValues": None,
                            "type": "text",
                            "objectType": "listing",
                            "isPublic": 1,
                            "sortOrder": 1,
                        }
                    ],
                    "limit": 500,
                    "page": 1,
                    "totalPages": 1,
                },
            )
        )
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)
        assert coordinator.last_refresh_succeeded is False

        await coordinator.async_refresh_retaining_stale()

        assert coordinator.last_refresh_succeeded is True
        assert len(coordinator.data) == 1
        stale = coordinator.data
        api_client._request = AsyncMock(side_effect=HostawayApiError("down"))
        await coordinator.async_refresh_retaining_stale()

        assert coordinator.last_refresh_succeeded is False
        assert coordinator.last_refresh_error is not None
        assert coordinator.data == stale

    async def test_unexpected_refresh_error_fails_closed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Unexpected definition refresh errors update failure state."""
        from custom_components.hostaway.coordinator import (
            HostawayCustomFieldsCoordinator,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client._request = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)

        await coordinator.async_refresh_retaining_stale()

        assert coordinator.last_refresh_succeeded is False
        assert isinstance(coordinator.last_refresh_error, RuntimeError)


class TestCoordinatorShedding:
    """Tests for refreshes dropped to protect the rate-limit budget."""

    async def test_a_shed_refresh_keeps_the_last_good_data(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SC-005: shedding publishes nothing and keeps entities available.

        Args:
            hass: Home Assistant instance.
            caplog: Captured log records.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "First")],
        )
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        good = coordinator.data
        assert good is not None

        api_client.get_all_listings = AsyncMock(side_effect=_shed())
        interval = coordinator.update_interval
        debouncer = coordinator._debounced_refresh
        with caplog.at_level(logging.WARNING, logger=_COORDINATOR_LOGGER):
            await coordinator.async_refresh()

        assert coordinator.data == good
        assert coordinator.last_update_success is True
        assert coordinator.shed_count == 1
        # SC-016: shedding must not reschedule or re-debounce anything.
        assert coordinator.update_interval == interval
        assert coordinator._debounced_refresh is debouncer
        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and record.name == _COORDINATOR_LOGGER
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "listings" in message
        assert "rate limit" in message
        assert entry.data[CONF_CLIENT_ID] not in message

    async def test_the_limiter_hears_about_every_shed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Shed cycles are reported to the limiter that caused them.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        limiter = Mock()
        api_client.limiter = limiter
        api_client.get_all_listings = AsyncMock(side_effect=_shed())
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        coordinator._first_refresh_complete = True

        await coordinator.async_refresh()

        assert limiter.note_shed.call_count == 1

    async def test_sustained_shedding_reports_on_a_cooldown(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SC-006: the first drop warns, repeats go quiet for 300 seconds.

        Args:
            hass: Home Assistant instance.
            caplog: Captured log records.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client.get_all_listings = AsyncMock(side_effect=_shed())
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        coordinator._first_refresh_complete = True
        elapsed = 0.0

        def _clock() -> float:
            """Return the faked monotonic reading.

            Returns:
                Seconds since the test started.
            """
            return elapsed

        with (
            patch("custom_components.hostaway.coordinator.time.monotonic", _clock),
            caplog.at_level(logging.DEBUG, logger=_COORDINATOR_LOGGER),
        ):
            await coordinator.async_refresh()
            elapsed = 299.0
            await coordinator.async_refresh()
            elapsed = 300.0
            await coordinator.async_refresh()

        shed_records = [
            record
            for record in caplog.records
            if record.name == _COORDINATOR_LOGGER and "dropped" in record.getMessage()
        ]
        assert [record.levelno for record in shed_records] == [
            logging.WARNING,
            logging.DEBUG,
            logging.WARNING,
        ]
        # The summary counts the quiet drops rather than losing them.
        assert "3 in total" in shed_records[-1].getMessage()
        assert "2 dropped since the last report" in shed_records[-1].getMessage()

    async def test_a_shed_mid_fan_out_abandons_the_whole_cycle(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-015: a partial reservations sweep is never published.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100, 200])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client.get_all_reservations = AsyncMock(
            side_effect=[
                [_make_reservation(1, 100)],
                [_make_reservation(2, 200)],
            ],
        )
        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        good = coordinator.data
        assert set(good) == {100, 200}

        api_client.get_all_reservations = AsyncMock(
            side_effect=[[_make_reservation(3, 100)], _shed()],
        )
        await coordinator.async_refresh()

        assert coordinator.data == good
        assert coordinator.last_update_success is True

    async def test_the_first_refresh_is_never_shed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-015: setup waits for data; later cycles give way.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        policies: list[WaitPolicy] = []
        api_client = AsyncMock()
        api_client.limiter = Mock()

        async def _record() -> list[HostawayListing]:
            """Record the policy the ambient context carries.

            Returns:
                An empty listings list.
            """
            policies.append(current_request_context().policy)
            return []

        api_client.get_all_listings = _record
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)

        await coordinator.async_refresh()
        # An empty result must still count as having completed, so the
        # second cycle may not be treated as another first refresh.
        assert coordinator.data == {}
        await coordinator.async_refresh()

        assert policies == [FIRST_REFRESH_POLICY, SCHEDULED_POLICY]

    async def test_a_failed_first_refresh_stays_a_first_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A setup that never got data keeps its unsheddable deadline.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        policies: list[WaitPolicy] = []
        api_client = AsyncMock()
        api_client.limiter = Mock()

        async def _fail() -> list[HostawayListing]:
            """Record the policy, then fail.

            Returns:
                Never returns.

            Raises:
                HostawayApiError: Always.
            """
            policies.append(current_request_context().policy)
            raise HostawayApiError("down")

        api_client.get_all_listings = _fail
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)

        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert policies == [FIRST_REFRESH_POLICY, FIRST_REFRESH_POLICY]

    async def test_definitions_are_not_loaded_until_they_load(
        self,
        hass: HomeAssistant,
    ) -> None:
        """T029: an empty cache is not the same as an empty account.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client._request = AsyncMock(side_effect=_shed())
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)
        coordinator._first_refresh_complete = True

        await coordinator.async_refresh_retaining_stale()

        assert coordinator.data == []
        assert coordinator.definitions_loaded is False
        assert coordinator.last_update_success is True
        coordinator._cancel_definition_retry()

    async def test_an_empty_account_counts_as_loaded(
        self,
        hass: HomeAssistant,
    ) -> None:
        """T029: a successful fetch of nothing is still a load.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client._request = AsyncMock(return_value=_definitions_response([]))
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)

        await coordinator.async_refresh_retaining_stale()

        assert coordinator.data == []
        assert coordinator.definitions_loaded is True

    async def test_definitions_retry_until_they_arrive(
        self,
        hass: HomeAssistant,
    ) -> None:
        """T029: a shed first attempt converges on a bounded retry.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client._request = AsyncMock(side_effect=_shed())
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)
        coordinator._first_refresh_complete = True

        await coordinator.async_refresh_retaining_stale()
        assert coordinator.definitions_loaded is False

        api_client._request = AsyncMock(
            return_value=_definitions_response([_definition_payload()]),
        )
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=31),
        )
        await hass.async_block_till_done()

        assert coordinator.definitions_loaded is True
        assert len(coordinator.data) == 1

    async def test_definition_retries_are_bounded(
        self,
        hass: HomeAssistant,
    ) -> None:
        """T029: a persistently unreachable account is not hammered.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client._request = AsyncMock(side_effect=_shed())
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)
        coordinator._first_refresh_complete = True

        await coordinator.async_refresh_retaining_stale()
        for delay in _DEFINITION_RETRY_DELAYS:
            async_fire_time_changed(
                hass,
                dt_util.utcnow() + timedelta(seconds=delay + 1),
            )
            await hass.async_block_till_done()

        assert coordinator.definitions_loaded is False
        assert coordinator._definition_retry_unsub is None
        assert coordinator._definition_retries == len(_DEFINITION_RETRY_DELAYS)

    async def test_a_shed_leaves_entities_available(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-005: a dropped cycle is invisible to the entities.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "First")],
        )
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        await coordinator.async_refresh()
        entity = CoordinatorEntity(coordinator)
        assert entity.available is True

        api_client.get_all_listings = AsyncMock(side_effect=_shed())
        await coordinator.async_refresh()

        assert entity.available is True

    async def test_the_reservations_first_refresh_is_never_shed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-015: setup-time reservation fetches wait rather than give way.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        policies: list[WaitPolicy] = []
        api_client = AsyncMock()
        api_client.limiter = Mock()

        async def _record(listing_id: int) -> list[HostawayReservation]:
            """Record the policy the ambient context carries.

            Args:
                listing_id: The listing being fetched.

            Returns:
                One reservation for that listing.
            """
            policies.append(current_request_context().policy)
            return [_make_reservation(1, listing_id)]

        api_client.get_all_reservations = _record
        coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_refresh()

        assert policies == [FIRST_REFRESH_POLICY, SCHEDULED_POLICY]

    async def test_a_first_refresh_failure_blocks_setup(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A setup that cannot fetch must not report itself ready.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = Mock()
        api_client.get_all_listings = AsyncMock(
            side_effect=HostawayRateLimitWaitTimeout("no capacity", waited=30.0),
        )
        coordinator = HostawayListingsCoordinator(hass, entry, api_client)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_config_entry_first_refresh()

    async def test_the_definitions_first_refresh_is_never_shed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-015: definitions get the unsheddable deadline too.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        policies: list[WaitPolicy] = []
        api_client = AsyncMock()
        api_client.limiter = Mock()

        async def _record(*_args: object, **_kwargs: object) -> httpx.Response:
            """Record the policy the ambient context carries.

            Args:
                *_args: Ignored request arguments.
                **_kwargs: Ignored request keywords.

            Returns:
                A definitions response with no definitions.
            """
            policies.append(current_request_context().policy)
            return _definitions_response([])

        api_client._request = _record
        coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)

        await coordinator.async_refresh_retaining_stale()

        assert policies == [FIRST_REFRESH_POLICY]
        assert coordinator.definitions_loaded is True

    async def test_a_saturated_limiter_sheds_cycles_not_setup(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-015: the asymmetry holds against a real saturated limiter.

        Args:
            hass: Home Assistant instance.
        """
        clock = FakeClock()
        limiter = make_limiter(clock, account_budget=1, ip_budget=1)
        # The coordinators build their contexts from the context module's
        # clock, which must be the limiter's clock or the deadlines are
        # measured against a different timeline entirely.
        monotonic = patch(
            "custom_components.hostaway.api.rate_limit.context.time.monotonic",
            clock.now,
        )
        with monotonic:
            await limiter.acquire("GET", "/v1/listings")
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.limiter = limiter

        async def _paced_listings() -> list[HostawayListing]:
            """Fetch listings through the saturated limiter.

            Returns:
                One listing, once capacity allows.
            """
            await limiter.acquire("GET", "/v1/listings")
            return [_make_listing(100, "First")]

        async def _paced_definitions(
            *_args: object,
            **_kwargs: object,
        ) -> httpx.Response:
            """Fetch definitions through the saturated limiter.

            Args:
                *_args: Ignored request arguments.
                **_kwargs: Ignored request keywords.

            Returns:
                A definitions response, once capacity allows.
            """
            await limiter.acquire("GET", "/v1/customFields")
            return _definitions_response([_definition_payload()])

        api_client.get_all_listings = _paced_listings
        api_client._request = _paced_definitions
        listings = HostawayListingsCoordinator(hass, entry, api_client)
        listings.data = {100: _make_listing(100, "First")}
        listings._first_refresh_complete = True
        definitions = HostawayCustomFieldsCoordinator(hass, entry, api_client)

        with monotonic:
            cycle = asyncio.create_task(listings.async_refresh())
            setup = asyncio.create_task(
                definitions.async_refresh_retaining_stale(),
            )
            await settle()
            # The scheduled cycle's two-second deadline passes first.
            clock.advance(SCHEDULED_POLICY.duration + 0.1)
            await settle()

            assert cycle.done()
            assert listings.shed_count == 1
            assert definitions.definitions_loaded is False
            assert not setup.done()

            # The window rolls over and the setup fetch gets its capacity.
            clock.advance(RATE_LIMIT_WINDOW_SECONDS)
            await settle()
            await setup

        assert definitions.definitions_loaded is True
        assert len(definitions.data) == 1
        limiter.close()
