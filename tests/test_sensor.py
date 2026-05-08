# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway sensor entities."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FILTER_CANCELLED,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
)
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.sensor import (
    _STATUS_TO_DERIVED,
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
    HostawayReservationStatusSensor,
    _build_reservation_attributes,
    _derive_state,
    _select_reservation,
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


def _make_listing(
    listing_id: int = 100,
    name: str = "Beach House",
    internal_name: str | None = None,
) -> HostawayListing:
    """Create a HostawayListing for testing.

    Args:
        listing_id: The listing ID.
        name: The listing name.
        internal_name: The internal reference name.

    Returns:
        A HostawayListing instance.
    """
    return HostawayListing(
        id=listing_id,
        name=name,
        internal_name=internal_name,
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
    confirmation_code: str | None = "ABC123",
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
        confirmation_code: Confirmation code.

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
        confirmation_code=confirmation_code,
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
        """unique_id format: {unique_id}_{listing_id}_{attribute_key}."""
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

    async def test_entity_ids_via_async_setup_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """async_setup_entry registers entities with FR-007 IDs."""
        from custom_components.hostaway.sensor import async_setup_entry

        entry = _make_entry(selected=[100])
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

        added: list = []
        await async_setup_entry(
            hass,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )

        # Verify suggested_object_id for all listing sensors
        for entity in added:
            if isinstance(entity, HostawayListingSensor):
                obj_id = entity.suggested_object_id
                assert obj_id is not None
                assert obj_id.startswith("hostaway_")

        # Cleanup to avoid lingering timers
        await listings_coord.async_shutdown()
        await res_coord.async_shutdown()


class TestSelectReservation:
    """Tests for _select_reservation helper."""

    def test_empty_returns_none(self) -> None:
        """Empty list returns None."""
        assert _select_reservation([]) is None

    def test_checked_in_wins_over_confirmed(self) -> None:
        """checked_in has higher priority than confirmed."""
        confirmed = _make_reservation(1, status="confirmed")
        checked_in = _make_reservation(2, status="checked_in")
        result = _select_reservation([confirmed, checked_in])
        assert result is not None
        assert result.id == 2

    def test_confirmed_wins_over_checked_out(self) -> None:
        """confirmed has higher priority than checked_out."""
        checked_out = _make_reservation(1, status="checked_out")
        confirmed = _make_reservation(2, status="confirmed")
        result = _select_reservation([checked_out, confirmed])
        assert result is not None
        assert result.id == 2

    def test_checked_out_wins_over_cancelled(self) -> None:
        """checked_out has higher priority than cancelled."""
        cancelled = _make_reservation(1, status="cancelled")
        checked_out = _make_reservation(2, status="checked_out")
        result = _select_reservation([cancelled, checked_out])
        assert result is not None
        assert result.id == 2

    def test_unknown_status_sorts_last(self) -> None:
        """Unknown status sorts after all known statuses."""
        unknown = _make_reservation(1, status="totally_unknown")
        cancelled = _make_reservation(2, status="cancelled")
        result = _select_reservation([unknown, cancelled])
        assert result is not None
        assert result.id == 2

    def test_new_status_wins_over_checked_out(self) -> None:
        """new has higher priority than checked_out."""
        checked_out = _make_reservation(1, status="checked_out")
        new = _make_reservation(2, status="new")
        result = _select_reservation([checked_out, new])
        assert result is not None
        assert result.id == 2

    def test_pending_wins_over_owner_stay(self) -> None:
        """pending has higher priority than ownerStay."""
        owner = _make_reservation(1, status="ownerStay")
        pending = _make_reservation(2, status="pending")
        result = _select_reservation([owner, pending])
        assert result is not None
        assert result.id == 2

    def test_single_reservation(self) -> None:
        """Single reservation is selected."""
        res = _make_reservation(42, status="confirmed")
        result = _select_reservation([res])
        assert result is not None
        assert result.id == 42


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
            "custom_components.hostaway.sensor._warned_statuses",
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
            "custom_components.hostaway.sensor._warned_statuses",
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

    def test_upcoming_reservations_preserve_order(self) -> None:
        """upcoming_reservations preserves input order."""
        r1 = _make_reservation(1, check_in="2025-08-01")
        r2 = _make_reservation(2, check_in="2025-09-01")
        r3 = _make_reservation(3, check_in="2025-10-01")
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
    ) -> None:
        """State is checked_in when reservation is checked in."""
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
    ) -> None:
        """State is awaiting_checkin when confirmed."""
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
    ) -> None:
        """checked_in wins over confirmed in priority."""
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
    ) -> None:
        """upcoming_reservations sorted by check_in."""
        entry = _make_entry(selected=[100])
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        r1 = _make_reservation(1001, 100, check_in="2025-09-01", status="confirmed")
        r2 = _make_reservation(1002, 100, check_in="2025-08-01", status="checked_in")
        api_client.get_all_reservations = AsyncMock(return_value=[r1, r2])

        listings_api = AsyncMock()
        listings_api.get_all_listings = AsyncMock(return_value=[_make_listing(100)])
        listings_coord = HostawayListingsCoordinator(hass, entry, listings_api)
        await listings_coord.async_refresh()

        res_coord = HostawayReservationsCoordinator(hass, entry, api_client)
        await res_coord.async_refresh()

        sensor = HostawayReservationStatusSensor(res_coord, listings_coord, 100, entry)
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

        # Remove listing from listings coordinator
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
    ) -> None:
        """Status change updates sensor state."""
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

        # Update reservation status
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
        assert sensor.native_value == "awaiting_checkin"
        upcoming = sensor.extra_state_attributes["upcoming_reservations"]
        assert len(upcoming) == 1
        assert upcoming[0]["id"] == 1

    async def test_filter_cancelled_disabled_shows_all(
        self,
        hass: HomeAssistant,
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
        upcoming = sensor.extra_state_attributes["upcoming_reservations"]
        assert len(upcoming) == 2
