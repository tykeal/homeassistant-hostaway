# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway reservation helpers and sensor entities."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hostaway.api.custom_fields import (
    HostawayCustomFieldDefinition,
)
from custom_components.hostaway.api.models import HostawayReservation
from custom_components.hostaway.const import CONF_FILTER_CANCELLED, DOMAIN
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.sensor.helpers import (
    _STATUS_TO_DERIVED,
    _build_reservation_attributes,
    _derive_state,
    _select_reservation,
)
from custom_components.hostaway.sensor.reservation import (
    HostawayReservationStatusSensor,
)
from tests.sensor.conftest import _make_entry, _make_listing, _make_reservation

_TODAY = date(2025, 8, 5)
# Inside the default _make_reservation stay (2025-08-01 to 2025-08-05).
_FIXTURE_TODAY = date(2025, 8, 3)
_FIXTURE_NOW = datetime(2025, 8, 3, 12, tzinfo=UTC)


def _custom_definition(custom_field_id: int) -> HostawayCustomFieldDefinition:
    """Return a reservation custom-field definition fixture."""
    parsed = HostawayCustomFieldDefinition.from_api_dict(
        {
            "id": custom_field_id,
            "accountId": 1,
            "name": "Cleaner Note",
            "varName": "cleaner_note",
            "possibleValues": [],
            "type": "textarea",
            "objectType": "reservation",
            "isPublic": 0,
            "sortOrder": 1,
        }
    )
    assert parsed is not None
    return parsed


async def _make_reservation_sensor(
    hass: HomeAssistant,
    reservations: list[HostawayReservation],
    *,
    filter_cancelled: bool,
) -> HostawayReservationStatusSensor:
    """Create a reservation sensor with prepared coordinator data.

    Args:
        hass: Home Assistant instance for the test.
        reservations: Reservation records to provide to the coordinator.
        filter_cancelled: Whether the sensor should hide cancelled records.

    Returns:
        A reservation sensor with refreshed coordinators.
    """
    entry = _make_entry(selected=[100])
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_FILTER_CANCELLED: filter_cancelled},
    )

    api_client = AsyncMock()
    api_client.get_all_reservations = AsyncMock(return_value=reservations)
    listings_api = AsyncMock()
    listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])

    listings_coordinator = HostawayListingsCoordinator(hass, entry, listings_api)
    await listings_coordinator.async_refresh()
    reservations_coordinator = HostawayReservationsCoordinator(
        hass,
        entry,
        api_client,
    )
    await reservations_coordinator.async_refresh()

    return HostawayReservationStatusSensor(
        reservations_coordinator,
        listings_coordinator,
        100,
        entry,
    )


def _pin_now(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    """Pin Home Assistant's local time for date-dependent sensor tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        now: Timezone-aware time returned by ``dt_util.now``.
    """
    monkeypatch.setattr(dt_util, "now", lambda: now)


def _assert_consistent_selection(
    sensor: HostawayReservationStatusSensor,
) -> dict[str, Any]:
    """Assert the state and attributes describe the same reservation.

    Args:
        sensor: Reservation sensor to evaluate.

    Returns:
        The evaluated extra state attributes.
    """
    attrs = sensor.extra_state_attributes
    state = sensor.native_value
    if attrs["reservation_id"] is None:
        assert state == "no_reservation"
        return attrs
    assert state == _STATUS_TO_DERIVED.get(attrs["status"], "unknown")
    upcoming_ids = [item["id"] for item in attrs["upcoming_reservations"]]
    assert attrs["reservation_id"] in upcoming_ids
    return attrs


class TestUpcomingReservationFiltering:
    """Tests for the upcoming reservation date cutoff."""

    async def test_filters_only_strictly_past_reservations(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Retain today and future stays in order, while preserving shape."""
        now = datetime(2025, 8, 5, 12, tzinfo=UTC)
        monkeypatch.setattr(dt_util, "now", lambda: now)
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-01",
                check_out="2025-08-04",
                status="checked_out",
            ),
            _make_reservation(
                2,
                check_in="2025-08-01",
                check_out="2025-08-05",
                status="checked_in",
            ),
            _make_reservation(
                3,
                check_in="2025-08-05",
                check_out="2025-08-05",
                status="confirmed",
            ),
            _make_reservation(
                4,
                check_in="2025-08-10",
                check_out="2025-08-12",
                status="confirmed",
            ),
            _make_reservation(
                5,
                check_in="2025-08-20",
                check_out="2025-08-22",
                status="cancelled",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=True,
        )

        upcoming = sensor.extra_state_attributes["upcoming_reservations"]

        assert [item["id"] for item in upcoming] == [2, 3, 4]
        assert upcoming[0] == {
            "id": 2,
            "guest_name": "John Doe",
            "check_in": "2025-08-01",
            "check_out": "2025-08-05",
            "status": "checked_in",
        }

    @pytest.mark.parametrize(
        ("now", "check_out_dates", "expected_ids"),
        [
            (
                datetime(
                    2025,
                    1,
                    1,
                    0,
                    30,
                    tzinfo=ZoneInfo("Pacific/Kiritimati"),
                ),
                ["2024-12-31", "2025-01-01"],
                [2],
            ),
            (
                datetime(
                    2025,
                    3,
                    9,
                    3,
                    30,
                    tzinfo=ZoneInfo("America/Los_Angeles"),
                ),
                ["2025-03-08", "2025-03-09"],
                [2],
            ),
        ],
    )
    async def test_uses_home_assistant_local_date(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        now: datetime,
        check_out_dates: list[str],
        expected_ids: list[int],
    ) -> None:
        """Use the local calendar date across UTC and DST boundaries."""
        monkeypatch.setattr(dt_util, "now", lambda: now)
        reservations = [
            _make_reservation(
                index,
                check_in=check_out,
                check_out=check_out,
            )
            for index, check_out in enumerate(check_out_dates, start=1)
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=False,
        )

        upcoming = sensor.extra_state_attributes["upcoming_reservations"]

        assert [item["id"] for item in upcoming] == expected_ids

    async def test_future_booking_drives_state_over_stale_past(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Select the future booking, not a past stay left checked in."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        past = _make_reservation(
            1,
            check_in="2025-08-01",
            check_out="2025-08-04",
            status="checked_in",
        )
        future = _make_reservation(
            2,
            check_in="2025-08-10",
            check_out="2025-08-12",
            status="confirmed",
        )
        sensor = await _make_reservation_sensor(
            hass,
            [past, future],
            filter_cancelled=False,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == "awaiting_checkin"
        assert attrs["reservation_id"] == 2
        assert attrs["check_out"] == "2025-08-12"
        assert [item["id"] for item in attrs["upcoming_reservations"]] == [2]


class TestDateAwareSelectionSensor:
    """Tests for date-aware selection through the reservation sensor."""

    async def test_past_only_listing_reports_no_reservation(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Past-only listings use the existing no-selection attributes."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        reservations = [
            _make_reservation(
                1,
                check_in="2025-07-01",
                check_out="2025-07-04",
                status="new",
            ),
            _make_reservation(
                2,
                check_in="2025-08-01",
                check_out="2025-08-04",
                status="checked_in",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=True,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == "no_reservation"
        assert attrs == _build_reservation_attributes(None, [], 100)
        assert attrs["custom_fields"] == {}
        assert attrs["listing_id"] == 100
        assert attrs["upcoming_reservations"] == []

    @pytest.mark.parametrize(
        ("filter_cancelled", "expected_state", "expected_id"),
        [(True, "awaiting_checkin", 2), (False, "cancelled", 1)],
    )
    async def test_cancellation_filter_with_upcoming_booking(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        filter_cancelled: bool,
        expected_state: str,
        expected_id: int,
    ) -> None:
        """The cancellation option is applied before date grouping."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-03",
                check_out="2025-08-07",
                status="cancelled",
            ),
            _make_reservation(
                2,
                check_in="2025-08-10",
                check_out="2025-08-12",
                status="confirmed",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=filter_cancelled,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == expected_state
        assert attrs["reservation_id"] == expected_id

    @pytest.mark.parametrize(
        ("filter_cancelled", "expected_state", "expected_id"),
        [(True, "no_reservation", None), (False, "cancelled", 2)],
    )
    async def test_cancelled_only_non_past_reservation(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        filter_cancelled: bool,
        expected_state: str,
        expected_id: int | None,
    ) -> None:
        """A lone non-past cancelled stay follows the cancellation option."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-01",
                check_out="2025-08-04",
                status="confirmed",
            ),
            _make_reservation(
                2,
                check_in="2025-08-10",
                check_out="2025-08-12",
                status="cancelled",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=filter_cancelled,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == expected_state
        assert attrs["reservation_id"] == expected_id

    async def test_same_day_turnover_selects_arrival(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A checked-out departure yields to the confirmed arrival."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-01",
                check_out="2025-08-05",
                status="checked_out",
            ),
            _make_reservation(
                2,
                check_in="2025-08-05",
                check_out="2025-08-08",
                status="confirmed",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=True,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == "awaiting_checkin"
        assert attrs["reservation_id"] == 2
        assert [item["id"] for item in attrs["upcoming_reservations"]] == [1, 2]

    async def test_uses_home_assistant_local_date_for_selection(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Group stays by the local date, not the UTC date."""
        _pin_now(
            monkeypatch,
            datetime(2025, 8, 5, 0, 30, tzinfo=ZoneInfo("Pacific/Kiritimati")),
        )
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-01",
                check_out="2025-08-04",
                status="checked_in",
            ),
            _make_reservation(
                2,
                check_in="2025-08-05",
                check_out="2025-08-07",
                status="confirmed",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=True,
        )

        attrs = _assert_consistent_selection(sensor)

        assert sensor.native_value == "awaiting_checkin"
        assert attrs["reservation_id"] == 2

    async def test_selection_follows_coordinator_refresh(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refreshed coordinator data changes the selection."""
        _pin_now(monkeypatch, datetime(2025, 8, 5, 12, tzinfo=UTC))
        upcoming = _make_reservation(
            2,
            check_in="2025-08-10",
            check_out="2025-08-12",
            status="confirmed",
        )
        sensor = await _make_reservation_sensor(
            hass,
            [upcoming],
            filter_cancelled=True,
        )
        assert _assert_consistent_selection(sensor)["reservation_id"] == 2

        current = _make_reservation(
            1,
            check_in="2025-08-03",
            check_out="2025-08-06",
            status="confirmed",
        )
        sensor.coordinator.async_set_updated_data({100: [current, upcoming]})
        assert _assert_consistent_selection(sensor)["reservation_id"] == 1

        sensor.coordinator.async_set_updated_data({100: []})
        assert _assert_consistent_selection(sensor)["reservation_id"] is None
        assert sensor.native_value == "no_reservation"

    async def test_selection_follows_local_date(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Advancing the date moves selection to the next stay, then none."""
        now = [datetime(2025, 8, 5, 12, tzinfo=UTC)]
        monkeypatch.setattr(dt_util, "now", lambda: now[0])
        reservations = [
            _make_reservation(
                1,
                check_in="2025-08-01",
                check_out="2025-08-05",
                status="checked_in",
            ),
            _make_reservation(
                2,
                check_in="2025-08-10",
                check_out="2025-08-12",
                status="confirmed",
            ),
        ]
        sensor = await _make_reservation_sensor(
            hass,
            reservations,
            filter_cancelled=True,
        )

        assert sensor.native_value == "checked_in"
        assert _assert_consistent_selection(sensor)["reservation_id"] == 1

        now[0] = datetime(2025, 8, 6, 12, tzinfo=UTC)
        assert sensor.native_value == "awaiting_checkin"
        assert _assert_consistent_selection(sensor)["reservation_id"] == 2

        now[0] = datetime(2025, 8, 13, 12, tzinfo=UTC)
        assert sensor.native_value == "no_reservation"
        assert _assert_consistent_selection(sensor)["reservation_id"] is None


class TestSelectReservation:
    """Tests for _select_reservation status priority among current stays."""

    def test_empty_returns_none(self) -> None:
        """Empty list returns None."""
        assert _select_reservation([], _FIXTURE_TODAY) is None

    def test_checked_in_wins_over_confirmed(self) -> None:
        """checked_in has higher priority than confirmed."""
        confirmed = _make_reservation(1, status="confirmed")
        checked_in = _make_reservation(2, status="checked_in")
        result = _select_reservation([confirmed, checked_in], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_confirmed_wins_over_checked_out(self) -> None:
        """confirmed has higher priority than checked_out."""
        checked_out = _make_reservation(1, status="checked_out")
        confirmed = _make_reservation(2, status="confirmed")
        result = _select_reservation([checked_out, confirmed], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_checked_out_wins_over_cancelled(self) -> None:
        """checked_out has higher priority than cancelled."""
        cancelled = _make_reservation(1, status="cancelled")
        checked_out = _make_reservation(2, status="checked_out")
        result = _select_reservation([cancelled, checked_out], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_unknown_status_sorts_last(self) -> None:
        """Unknown status sorts after all known statuses."""
        unknown = _make_reservation(1, status="totally_unknown")
        cancelled = _make_reservation(2, status="cancelled")
        result = _select_reservation([unknown, cancelled], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_new_status_wins_over_checked_out(self) -> None:
        """new has higher priority than checked_out."""
        checked_out = _make_reservation(1, status="checked_out")
        new = _make_reservation(2, status="new")
        result = _select_reservation([checked_out, new], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_pending_wins_over_owner_stay(self) -> None:
        """pending has higher priority than ownerStay."""
        owner = _make_reservation(1, status="ownerStay")
        pending = _make_reservation(2, status="pending")
        result = _select_reservation([owner, pending], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 2

    def test_single_reservation(self) -> None:
        """Single reservation is selected."""
        res = _make_reservation(42, status="confirmed")
        result = _select_reservation([res], _FIXTURE_TODAY)
        assert result is not None
        assert result.id == 42


class TestDateAwareSelection:
    """Tests for date-aware reservation selection."""

    @staticmethod
    def _res(
        res_id: int,
        check_in: str,
        check_out: str,
        status: str = "confirmed",
    ) -> HostawayReservation:
        """Return a reservation with the given dates and status."""
        return _make_reservation(
            res_id,
            check_in=check_in,
            check_out=check_out,
            status=status,
        )

    @staticmethod
    def _selected_id(
        reservations: list[HostawayReservation],
        today: date = _TODAY,
    ) -> int | None:
        """Return the selected reservation ID, or None."""
        selected = _select_reservation(reservations, today)
        return None if selected is None else selected.id

    def test_stale_checked_in_past_loses_to_future_confirmed(self) -> None:
        """A past stay still checked in is never selected."""
        past = self._res(1, "2025-08-01", "2025-08-04", "checked_in")
        future = self._res(2, "2025-08-10", "2025-08-12", "confirmed")
        assert self._selected_id([past, future]) == 2

    def test_equal_priority_past_new_loses_to_future_confirmed(self) -> None:
        """An older equal-priority stay first in coordinator order loses."""
        past = self._res(1, "2025-07-01", "2025-07-04", "new")
        future = self._res(2, "2025-08-10", "2025-08-12", "confirmed")
        assert self._selected_id([past, future]) == 2

    @pytest.mark.parametrize(
        ("check_in", "check_out"),
        [("2025-08-01", "2025-08-05"), ("2025-08-05", "2025-08-05")],
        ids=["checkout-today", "zero-night"],
    )
    def test_stay_ending_today_is_current(
        self,
        check_in: str,
        check_out: str,
    ) -> None:
        """Checkout-today and zero-night stays outrank upcoming stays."""
        current = self._res(1, check_in, check_out, "inquiry")
        upcoming = self._res(2, "2025-08-06", "2025-08-08", "checked_in")
        assert self._selected_id([upcoming, current]) == 1

    def test_current_beats_better_status_upcoming(self) -> None:
        """A stay in progress outranks a better-status future booking."""
        current = self._res(1, "2025-08-03", "2025-08-07", "pending")
        upcoming = self._res(2, "2025-08-10", "2025-08-12", "checked_in")
        assert self._selected_id([current, upcoming]) == 1

    @pytest.mark.parametrize(
        ("departing_status", "arriving_status", "expected_id"),
        [
            ("checked_in", "confirmed", 1),
            ("checked_out", "confirmed", 2),
            ("checked_out", "inquiry", 1),
            ("confirmed", "new", 2),
        ],
    )
    @pytest.mark.parametrize("reverse", [False, True])
    def test_same_day_turnover(
        self,
        departing_status: str,
        arriving_status: str,
        expected_id: int,
        reverse: bool,
    ) -> None:
        """Status decides a turnover first; equal priority picks the arrival."""
        departing = self._res(1, "2025-08-01", "2025-08-05", departing_status)
        arriving = self._res(2, "2025-08-05", "2025-08-08", arriving_status)
        reservations = [departing, arriving]
        if reverse:
            reservations.reverse()
        assert self._selected_id(reservations) == expected_id

    def test_upcoming_status_beats_sooner_check_in(self) -> None:
        """Status priority outranks chronology among upcoming stays."""
        sooner = self._res(1, "2025-08-10", "2025-08-12", "pending")
        later = self._res(2, "2025-08-20", "2025-08-22", "confirmed")
        assert self._selected_id([sooner, later]) == 2

    def test_upcoming_equal_priority_picks_sooner_check_in(self) -> None:
        """The sooner check-in wins among equal-priority upcoming stays."""
        later = self._res(1, "2025-08-20", "2025-08-22", "new")
        sooner = self._res(2, "2025-08-10", "2025-08-12", "confirmed")
        assert self._selected_id([later, sooner]) == 2

    @pytest.mark.parametrize(
        ("check_in", "check_out"),
        [("2025-08-03", "2025-08-07"), ("2025-08-10", "2025-08-12")],
        ids=["current", "upcoming"],
    )
    @pytest.mark.parametrize("reverse", [False, True])
    def test_full_tie_picks_lowest_id(
        self,
        check_in: str,
        check_out: str,
        reverse: bool,
    ) -> None:
        """Fully tied dated stays select the lowest ID in any input order."""
        reservations = [
            self._res(7, check_in, check_out),
            self._res(3, check_in, check_out),
            self._res(5, check_in, check_out),
        ]
        if reverse:
            reservations.reverse()
        assert self._selected_id(reservations) == 3

    def test_past_check_out_is_never_selected(self) -> None:
        """A check-out before today excludes the reservation."""
        reservation = self._res(1, "2025-08-01", "2025-08-04", "checked_in")
        assert self._selected_id([reservation]) is None

    def test_past_only_returns_none(self) -> None:
        """Only past reservations yields no selection."""
        reservations = [
            self._res(1, "2025-07-01", "2025-07-04", "checked_in"),
            self._res(2, "2025-08-01", "2025-08-04", "new"),
        ]
        assert self._selected_id(reservations) is None

    def test_empty_returns_none(self) -> None:
        """An empty list yields no selection."""
        assert self._selected_id([]) is None

    def test_unknown_status_ranks_last_within_group(self) -> None:
        """Unknown statuses rank last in their group, not across groups."""
        unknown = self._res(1, "2025-08-03", "2025-08-07", "totally_unknown")
        inquiry = self._res(2, "2025-08-04", "2025-08-06", "inquiryDenied")
        upcoming = self._res(3, "2025-08-10", "2025-08-12", "checked_in")
        assert self._selected_id([unknown, inquiry, upcoming]) == 2
        assert self._selected_id([unknown, upcoming]) == 1

    def test_does_not_mutate_input(self) -> None:
        """Selection leaves the input list and its order unchanged."""
        reservations = [
            self._res(1, "2025-07-01", "2025-07-04"),
            self._res(2, "2025-08-10", "2025-08-12"),
            self._res(3, "2025-08-03", "2025-08-07"),
        ]
        snapshot = list(reservations)
        self._selected_id(reservations)
        assert reservations == snapshot

    def test_dated_selection_does_not_warn(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Selecting among past and upcoming reservations logs nothing."""
        reservations = [
            self._res(1, "2025-07-30", "2025-08-01"),
            self._res(2, "2025-08-10", "2025-08-12"),
        ]

        with caplog.at_level(logging.WARNING):
            assert self._selected_id(reservations) == 2

        assert caplog.records == []


class TestDeriveState:
    """Tests for _derive_state helper."""

    def test_none_returns_no_reservation(self) -> None:
        """None returns no_reservation."""
        assert _derive_state(None) == "no_reservation"

    def test_confirmed_maps_to_awaiting_checkin(self) -> None:
        """confirmed maps to awaiting_checkin."""
        res = _make_reservation(status="confirmed")
        assert _derive_state(res) == "awaiting_checkin"

    def test_checked_in_passes_through(self) -> None:
        """checked_in maps to checked_in."""
        res = _make_reservation(status="checked_in")
        assert _derive_state(res) == "checked_in"

    def test_checked_out_passes_through(self) -> None:
        """checked_out maps to checked_out."""
        res = _make_reservation(status="checked_out")
        assert _derive_state(res) == "checked_out"

    def test_cancelled_maps_to_cancelled(self) -> None:
        """cancelled maps to cancelled."""
        res = _make_reservation(status="cancelled")
        assert _derive_state(res) == "cancelled"

    def test_new_maps_to_awaiting_checkin(self) -> None:
        """new maps to awaiting_checkin."""
        res = _make_reservation(status="new")
        assert _derive_state(res) == "awaiting_checkin"

    def test_modified_maps_to_awaiting_checkin(self) -> None:
        """modified maps to awaiting_checkin."""
        res = _make_reservation(status="modified")
        assert _derive_state(res) == "awaiting_checkin"

    def test_pending_maps_to_pending_approval(self) -> None:
        """pending maps to pending_approval."""
        res = _make_reservation(status="pending")
        assert _derive_state(res) == "pending_approval"

    def test_unconfirmed_maps_to_pending_approval(self) -> None:
        """unconfirmed maps to pending_approval."""
        res = _make_reservation(status="unconfirmed")
        assert _derive_state(res) == "pending_approval"

    def test_awaiting_payment_maps_to_awaiting_guest(self) -> None:
        """awaitingPayment maps to awaiting_guest."""
        res = _make_reservation(status="awaitingPayment")
        assert _derive_state(res) == "awaiting_guest"

    def test_awaiting_verification_maps_to_awaiting_guest(
        self,
    ) -> None:
        """awaitingGuestVerification maps to awaiting_guest."""
        res = _make_reservation(status="awaitingGuestVerification")
        assert _derive_state(res) == "awaiting_guest"

    def test_owner_stay_maps_to_owner_stay(self) -> None:
        """ownerStay maps to owner_stay."""
        res = _make_reservation(status="ownerStay")
        assert _derive_state(res) == "owner_stay"

    def test_declined_maps_to_cancelled(self) -> None:
        """declined maps to cancelled."""
        res = _make_reservation(status="declined")
        assert _derive_state(res) == "cancelled"

    def test_expired_maps_to_cancelled(self) -> None:
        """expired maps to cancelled."""
        res = _make_reservation(status="expired")
        assert _derive_state(res) == "cancelled"

    def test_inquiry_maps_to_inquiry(self) -> None:
        """inquiry maps to inquiry."""
        res = _make_reservation(status="inquiry")
        assert _derive_state(res) == "inquiry"

    def test_inquiry_preapproved_maps_to_inquiry(self) -> None:
        """inquiryPreapproved maps to inquiry."""
        res = _make_reservation(status="inquiryPreapproved")
        assert _derive_state(res) == "inquiry"

    def test_inquiry_denied_maps_to_inquiry(self) -> None:
        """inquiryDenied maps to inquiry."""
        res = _make_reservation(status="inquiryDenied")
        assert _derive_state(res) == "inquiry"

    def test_inquiry_timedout_maps_to_inquiry(self) -> None:
        """inquiryTimedout maps to inquiry."""
        res = _make_reservation(status="inquiryTimedout")
        assert _derive_state(res) == "inquiry"

    def test_inquiry_not_possible_maps_to_inquiry(self) -> None:
        """inquiryNotPossible maps to inquiry."""
        res = _make_reservation(status="inquiryNotPossible")
        assert _derive_state(res) == "inquiry"

    def test_unknown_api_status_maps_to_unknown(self) -> None:
        """unknown API status maps to unknown."""
        res = _make_reservation(status="unknown")
        assert _derive_state(res) == "unknown"

    def test_truly_unknown_status_maps_to_unknown(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unrecognised status maps to unknown with warning."""
        monkeypatch.setattr(
            "custom_components.hostaway.sensor.helpers._warned_statuses",
            set(),
        )
        res = _make_reservation(status="totally_new_status")
        with caplog.at_level(logging.WARNING):
            assert _derive_state(res) == "unknown"
        assert "totally_new_status" in caplog.text

    def test_unknown_warning_logged_once(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Warning for same unknown status only logged once."""
        monkeypatch.setattr(
            "custom_components.hostaway.sensor.helpers._warned_statuses",
            set(),
        )
        res = _make_reservation(status="repeat_status")
        with caplog.at_level(logging.WARNING):
            _derive_state(res)
            _derive_state(res)
        count = caplog.text.count("repeat_status")
        assert count == 1

    def test_all_status_to_derived_values_in_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Every derived state is a valid ENUM option."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api = AsyncMock()
        api.get_all_reservations = AsyncMock(return_value=[])
        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(
            return_value=[_make_listing(100)],
        )
        l_coord = HostawayListingsCoordinator(
            hass,
            entry,
            listings_api,
        )
        r_coord = HostawayReservationsCoordinator(
            hass,
            entry,
            api,
        )
        sensor = HostawayReservationStatusSensor(
            r_coord,
            l_coord,
            100,
            entry,
        )
        valid = set(sensor.options or [])
        for derived in _STATUS_TO_DERIVED.values():
            assert derived in valid, f"{derived} not in options"


class TestBuildReservationAttributes:
    """Tests for _build_reservation_attributes helper."""

    def test_none_reservation_returns_null_fields(self) -> None:
        """None reservation returns null attribute fields."""
        attrs = _build_reservation_attributes(None, [], 100)
        assert attrs["reservation_id"] is None
        assert attrs["guest_name"] is None
        assert attrs["listing_id"] == 100
        assert attrs["upcoming_reservations"] == []

    async def test_sensor_listens_for_definition_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Reservation sensor subscribes to definition metadata refreshes."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api = AsyncMock()
        api.get_all_reservations = AsyncMock(return_value=[])
        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        l_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        r_coord = HostawayReservationsCoordinator(hass, entry, api)
        listeners: list[object] = []

        def _async_add_listener(listener: object) -> object:
            """Capture a definitions coordinator listener."""
            listeners.append(listener)
            return lambda: None

        definitions_coord = SimpleNamespace(
            data=[],
            async_add_listener=_async_add_listener,
        )
        sensor = HostawayReservationStatusSensor(
            r_coord,
            l_coord,
            100,
            entry,
            cast(Any, definitions_coord),
        )
        sensor.hass = hass

        await sensor.async_added_to_hass()

        assert listeners == [sensor.async_write_ha_state]
        await l_coord.async_shutdown()
        await r_coord.async_shutdown()
        assert listeners == [sensor.async_write_ha_state]

    def test_attributes_include_all_fr_r04_fields(self) -> None:
        """Attributes include all FR-R04 required fields."""
        res = _make_reservation(
            res_id=1001,
            guest_name="Alice",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="checked_in",
            door_code="9999",
            door_code_vendor="yale",
            door_code_instruction="Front door",
            num_guests=2,
            confirmation_code="XYZ789",
        )
        attrs = _build_reservation_attributes(res, [res], 100)
        assert attrs["reservation_id"] == 1001
        assert attrs["guest_name"] == "Alice"
        assert attrs["check_in"] == "2025-08-01"
        assert attrs["check_out"] == "2025-08-05"
        assert attrs["status"] == "checked_in"
        assert attrs["door_code"] == "9999"
        assert attrs["door_code_vendor"] == "yale"
        assert attrs["door_code_instruction"] == "Front door"
        assert attrs["num_guests"] == 2
        assert attrs["confirmation_code"] == "XYZ789"
        assert attrs["listing_id"] == 100
        assert attrs["custom_fields"] == {}

    def test_attributes_include_reservation_custom_fields(self) -> None:
        """Reservation attributes include resolved and unresolved custom fields."""
        res = _make_reservation()
        parsed = type(res).from_api_response(
            {
                "id": res.id,
                "listingMapId": res.listing_id,
                "guestName": res.guest_name,
                "arrivalDate": res.check_in,
                "departureDate": res.check_out,
                "status": res.status,
                "customFieldValues": [
                    {"customFieldId": 8, "value": "Bring linen"},
                    {"customFieldId": 9, "value": "Unknown"},
                ],
            }
        )

        attrs = _build_reservation_attributes(
            parsed,
            [parsed],
            100,
            [_custom_definition(8)],
        )

        assert attrs["custom_fields"] == {
            "custom_field_8": {
                "customFieldId": 8,
                "varName": "cleaner_note",
                "name": "Cleaner Note",
                "type": "textarea",
                "possibleValues": [],
                "value": "Bring linen",
                "resolved": True,
            },
            "custom_field_9": {
                "customFieldId": 9,
                "value": "Unknown",
                "resolved": False,
            },
        }

    def test_attributes_reflect_patched_reservation_custom_field(self) -> None:
        """Reservation attributes reflect the locally patched write value."""
        res = _make_reservation()
        patched = type(res).from_api_response(
            {
                "id": res.id,
                "listingMapId": res.listing_id,
                "guestName": res.guest_name,
                "arrivalDate": res.check_in,
                "departureDate": res.check_out,
                "status": res.status,
                "customFieldValues": [
                    {"customFieldId": 8, "value": "Updated linen note"}
                ],
            }
        )

        attrs = _build_reservation_attributes(
            patched,
            [patched],
            100,
            [_custom_definition(8)],
        )

        assert attrs["custom_fields"]["custom_field_8"]["value"] == (
            "Updated linen note"
        )

    def test_upcoming_reservations_preserve_order(self) -> None:
        """upcoming_reservations preserves input order."""
        r1 = _make_reservation(1, check_in="2025-08-01")
        r2 = _make_reservation(2, check_in="2025-09-01", check_out="2025-09-05")
        r3 = _make_reservation(3, check_in="2025-10-01", check_out="2025-10-05")
        attrs = _build_reservation_attributes(r1, [r1, r2, r3], 100)
        upcoming = attrs["upcoming_reservations"]
        assert len(upcoming) == 3
        assert upcoming[0]["check_in"] == "2025-08-01"
        assert upcoming[1]["check_in"] == "2025-09-01"
        assert upcoming[2]["check_in"] == "2025-10-01"

    def test_upcoming_reservation_fields(self) -> None:
        """Each upcoming reservation has required fields."""
        res = _make_reservation(
            res_id=42,
            guest_name="Bob",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="confirmed",
        )
        attrs = _build_reservation_attributes(res, [res], 100)
        upcoming = attrs["upcoming_reservations"]
        assert len(upcoming) == 1
        entry = upcoming[0]
        assert entry["id"] == 42
        assert entry["guest_name"] == "Bob"
        assert entry["check_in"] == "2025-08-01"
        assert entry["check_out"] == "2025-08-05"
        assert entry["status"] == "confirmed"


class TestReservationStatusSensor:
    """Tests for HostawayReservationStatusSensor."""

    async def test_state_no_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """State is no_reservation when listing has none."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.native_value == "no_reservation"

    async def test_state_checked_in(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """State is checked_in when reservation is checked in."""
        _pin_now(monkeypatch, _FIXTURE_NOW)
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(1001, 100, status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.native_value == "checked_in"

    async def test_state_awaiting_checkin_from_confirmed(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """State is awaiting_checkin when confirmed."""
        _pin_now(monkeypatch, _FIXTURE_NOW)
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

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.native_value == "awaiting_checkin"

    async def test_priority_checked_in_wins(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """checked_in wins over confirmed in priority."""
        _pin_now(monkeypatch, _FIXTURE_NOW)
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        r1 = _make_reservation(1001, 100, status="confirmed")
        r2 = _make_reservation(1002, 100, status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[r1, r2])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.native_value == "checked_in"
        attrs = sensor.extra_state_attributes
        assert attrs["reservation_id"] == 1002

    async def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Attributes include guest_name, door_code, upcoming."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        reservation = _make_reservation(
            1001,
            100,
            guest_name="Jane Smith",
            check_in="2025-08-01",
            check_out="2025-08-05",
            status="checked_in",
            door_code="5678",
            door_code_vendor="yale",
            door_code_instruction="Side door",
            num_guests=2,
            confirmation_code="XYZ789",
        )
        api_client.get_all_reservations = AsyncMock(return_value=[reservation])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda: datetime(2025, 1, 1, tzinfo=UTC),
        )
        attrs = sensor.extra_state_attributes
        assert attrs["guest_name"] == "Jane Smith"
        assert attrs["door_code"] == "5678"
        assert attrs["door_code_vendor"] == "yale"
        assert attrs["door_code_instruction"] == "Side door"
        assert attrs["num_guests"] == 2
        assert attrs["confirmation_code"] == "XYZ789"
        assert attrs["listing_id"] == 100
        assert len(attrs["upcoming_reservations"]) == 1

    async def test_upcoming_reservations_sorted(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """upcoming_reservations sorted by check_in."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        r1 = _make_reservation(
            1001,
            100,
            check_in="2025-09-01",
            check_out="2025-09-05",
            status="confirmed",
        )
        r2 = _make_reservation(1002, 100, check_in="2025-08-01", status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[r1, r2])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda: datetime(2025, 1, 1, tzinfo=UTC),
        )
        upcoming = sensor.extra_state_attributes["upcoming_reservations"]
        assert upcoming[0]["check_in"] == "2025-08-01"
        assert upcoming[1]["check_in"] == "2025-09-01"

    async def test_available_with_coordinator_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Sensor available when coordinator has data."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.available is True

    async def test_unavailable_without_coordinator_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Sensor unavailable when coordinator data is None."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        res_coord.data = None  # type: ignore[assignment]
        assert sensor.available is False

    async def test_unavailable_when_listing_removed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Sensor unavailable when listing removed from listings."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.available is True

        listings_coord.data = {}
        assert sensor.available is False

    async def test_unique_id_format(
        self,
        hass: HomeAssistant,
    ) -> None:
        """unique_id: {uid}_{listing_id}_reservation_status."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        expected = f"{entry.unique_id}_100_reservation_status"
        assert sensor.unique_id == expected

    async def test_device_info_from_listings_coordinator(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Device info comes from listings coordinator."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(
            return_value=[_make_listing(100, "Beach House")]
        )
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        device_info = sensor.device_info
        assert device_info is not None
        assert (
            DOMAIN,
            "test-client-id_100",
        ) in device_info["identifiers"]
        assert device_info["name"] == "Beach House"

    async def test_status_change_updates_state(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Status change updates sensor state."""
        _pin_now(monkeypatch, _FIXTURE_NOW)
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

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.native_value == "awaiting_checkin"

        updated = _make_reservation(1001, 100, status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[updated])
        await res_coord.async_refresh()
        assert sensor.native_value == "checked_in"

    async def test_device_class_is_enum(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Sensor device class is ENUM (FR-R07)."""
        from homeassistant.components.sensor import SensorDeviceClass

        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_all_reservations = AsyncMock(return_value=[])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
        assert sensor.device_class == SensorDeviceClass.ENUM

    async def test_filter_cancelled_excludes_cancelled(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelled reservations excluded when filter enabled."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            entry, options={CONF_FILTER_CANCELLED: True}
        )
        api_client = AsyncMock()
        r1 = _make_reservation(1, 100, status="confirmed")
        r2 = _make_reservation(2, 100, status="cancelled")
        r3 = _make_reservation(3, 100, status="declined")
        r4 = _make_reservation(4, 100, status="expired")
        api_client.get_all_reservations = AsyncMock(
            return_value=[r1, r2, r3, r4],
        )

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(
            return_value=[_make_listing(100)],
        )
        listings_coord = HostawayListingsCoordinator(
            hass,
            entry,
            listings_api,
        )
        await listings_coord.async_refresh()
        res_coord = HostawayReservationsCoordinator(
            hass,
            entry,
            api_client,
        )
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(
            res_coord,
            listings_coord,
            100,
            entry,
        )
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda: datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert sensor.native_value == "awaiting_checkin"
        upcoming = sensor.extra_state_attributes["upcoming_reservations"]
        assert len(upcoming) == 1
        assert upcoming[0]["id"] == 1

    async def test_filter_cancelled_disabled_shows_all(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All reservations shown when filter disabled."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            entry, options={CONF_FILTER_CANCELLED: False}
        )
        api_client = AsyncMock()
        r1 = _make_reservation(1, 100, status="confirmed")
        r2 = _make_reservation(2, 100, status="cancelled")
        api_client.get_all_reservations = AsyncMock(
            return_value=[r1, r2],
        )

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(
            return_value=[_make_listing(100)],
        )
        listings_coord = HostawayListingsCoordinator(
            hass,
            entry,
            listings_api,
        )
        await listings_coord.async_refresh()
        res_coord = HostawayReservationsCoordinator(
            hass,
            entry,
            api_client,
        )
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(
            res_coord,
            listings_coord,
            100,
            entry,
        )
        monkeypatch.setattr(
            dt_util,
            "now",
            lambda: datetime(2025, 1, 1, tzinfo=UTC),
        )
        upcoming = sensor.extra_state_attributes["upcoming_reservations"]
        assert len(upcoming) == 2
