# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for API data models including tokens, listings, and reservations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.hostaway.api.models import (
    AccessToken,
    HostawayListing,
    HostawayReservation,
)
from tests.conftest import make_listing_response, make_reservation_response


def _make_token(**overrides: Any) -> AccessToken:
    """Create an AccessToken with sensible defaults.

    Args:
        **overrides: Fields to override on the default token.

    Returns:
        An AccessToken instance.
    """
    defaults: dict[str, Any] = {
        "access_token": "test-access-token",
        "token_type": "Bearer",
        "expires_in": 86400,
        "issued_at": datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return AccessToken(**defaults)


class TestAccessTokenCreation:
    """Tests for AccessToken creation and validation."""

    def test_create_with_valid_data(self) -> None:
        """AccessToken can be created with valid parameters."""
        token = _make_token()
        assert token.access_token == "test-access-token"
        assert token.token_type == "Bearer"
        assert token.expires_in == 86400

    def test_empty_access_token_raises(self) -> None:
        """AccessToken raises ValueError for empty access_token."""
        with pytest.raises(ValueError, match="non-empty"):
            _make_token(access_token="")

    def test_empty_token_type_raises(self) -> None:
        """AccessToken raises ValueError for empty token_type."""
        with pytest.raises(ValueError, match="non-empty"):
            _make_token(token_type="")

    def test_negative_expires_in_raises(self) -> None:
        """AccessToken raises ValueError for non-positive expires_in."""
        with pytest.raises(ValueError, match="positive"):
            _make_token(expires_in=-1)

    def test_zero_expires_in_raises(self) -> None:
        """AccessToken raises ValueError for zero expires_in."""
        with pytest.raises(ValueError, match="positive"):
            _make_token(expires_in=0)

    def test_naive_datetime_raises(self) -> None:
        """AccessToken raises ValueError for naive issued_at."""
        with pytest.raises(ValueError, match="timezone-aware"):
            _make_token(issued_at=datetime(2025, 7, 18, 12, 0, 0))


class TestAccessTokenFrozen:
    """Tests for AccessToken immutability."""

    def test_frozen_immutability(self) -> None:
        """AccessToken fields cannot be modified after creation."""
        token = _make_token()
        with pytest.raises(AttributeError):
            token.access_token = "new-token"  # type: ignore[misc]

    def test_frozen_expires_in(self) -> None:
        """AccessToken expires_in cannot be modified."""
        token = _make_token()
        with pytest.raises(AttributeError):
            token.expires_in = 999  # type: ignore[misc]


class TestAccessTokenExpiresAt:
    """Tests for AccessToken.expires_at computed property."""

    def test_expires_at_computation(self) -> None:
        """expires_at equals issued_at + expires_in seconds."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued, expires_in=86400)
        expected = issued + timedelta(seconds=86400)
        assert token.expires_at == expected

    def test_expires_at_short_lifetime(self) -> None:
        """expires_at works for short token lifetimes."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued, expires_in=60)
        expected = issued + timedelta(seconds=60)
        assert token.expires_at == expected


class TestAccessTokenIsExpired:
    """Tests for AccessToken.is_expired method."""

    def test_not_expired_fresh_token(self) -> None:
        """A freshly issued token is not expired."""
        token = _make_token(
            issued_at=datetime.now(UTC),
            expires_in=86400,
        )
        assert token.is_expired() is False

    def test_expired_old_token(self) -> None:
        """A token issued more than expires_in ago is expired."""
        issued = datetime.now(UTC) - timedelta(seconds=86401)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired() is True

    def test_expired_with_buffer(self) -> None:
        """A token within the buffer window is considered expired."""
        issued = datetime.now(UTC) - timedelta(seconds=86200)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired(buffer_seconds=300) is True

    def test_not_expired_outside_buffer(self) -> None:
        """A token outside the buffer window is not expired."""
        issued = datetime.now(UTC) - timedelta(seconds=85800)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired(buffer_seconds=300) is False


class TestAccessTokenSecondsUntilReady:
    """Tests for AccessToken.seconds_until_ready property."""

    def test_ready_after_one_second(self) -> None:
        """Token is ready (0.0) after 1 second has elapsed."""
        issued = datetime.now(UTC) - timedelta(seconds=2.0)
        token = _make_token(issued_at=issued)
        assert token.seconds_until_ready == 0.0

    def test_not_ready_immediately(self) -> None:
        """Token needs delay when just issued."""
        issued = datetime.now(UTC)
        token = _make_token(issued_at=issued)
        assert token.seconds_until_ready > 0.0
        assert token.seconds_until_ready <= 1.0

    def test_returns_remaining_delay(self) -> None:
        """Returns remaining time until 1s post-generation."""
        now = datetime(2025, 7, 18, 12, 0, 0, 500000, tzinfo=UTC)
        issued = datetime(2025, 7, 18, 12, 0, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued)
        with patch(
            "custom_components.hostaway.api.models.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = token.seconds_until_ready
            assert 0.0 < result <= 1.0


class TestAccessTokenSerialization:
    """Tests for AccessToken to_dict/from_dict round-trip."""

    def test_to_dict(self) -> None:
        """to_dict produces expected dictionary structure."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued)
        result = token.to_dict()
        assert result == {
            "access_token": "test-access-token",
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_at": "2025-07-18T12:00:00+00:00",
        }

    def test_from_dict(self) -> None:
        """from_dict reconstructs an AccessToken from dictionary."""
        data = {
            "access_token": "restored-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "issued_at": "2025-07-18T12:00:00+00:00",
        }
        token = AccessToken.from_dict(data)
        assert token.access_token == "restored-token"
        assert token.expires_in == 3600
        assert token.issued_at == datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)

    def test_round_trip(self) -> None:
        """to_dict followed by from_dict produces equal token."""
        original = _make_token()
        restored = AccessToken.from_dict(original.to_dict())
        assert restored == original


class TestHostawayListingFromApiResponse:
    """Tests for HostawayListing.from_api_response parsing."""

    def test_basic_field_mapping(self) -> None:
        """Fields are mapped from camelCase to snake_case."""
        data = make_listing_response()
        listing = HostawayListing.from_api_response(data)
        assert listing.id == 12345
        assert listing.name == "Oceanview Suite"
        assert listing.internal_name == "ocean-suite-1"
        assert listing.city == "Miami"
        assert listing.country_code == "US"
        assert listing.property_type == "apartment"
        assert listing.bedrooms == 2
        assert listing.bathrooms == 1.5
        assert listing.max_guests == 4
        assert listing.base_price == 150.00
        assert listing.currency == "USD"

    def test_is_active_maps_to_status_active(self) -> None:
        """isActive=1 maps to status='active'."""
        data = make_listing_response(isActive=1)
        listing = HostawayListing.from_api_response(data)
        assert listing.status == "active"

    def test_is_active_maps_to_status_inactive(self) -> None:
        """isActive=0 maps to status='inactive'."""
        data = make_listing_response(isActive=0)
        listing = HostawayListing.from_api_response(data)
        assert listing.status == "inactive"

    def test_address_as_string(self) -> None:
        """String address is stored directly."""
        data = make_listing_response(address="456 Main St")
        listing = HostawayListing.from_api_response(data)
        assert listing.address == "456 Main St"

    def test_address_as_nested_object(self) -> None:
        """Nested address object uses 'full' field."""
        data = make_listing_response(
            address={"full": "789 Oak Ave, Suite 2"},
        )
        listing = HostawayListing.from_api_response(data)
        assert listing.address == "789 Oak Ave, Suite 2"

    def test_check_in_out_times(self) -> None:
        """Check-in/out times are correctly mapped as strings."""
        data = make_listing_response(
            checkInTimeStart="14:00",
            checkInTimeEnd="22:00",
            checkOutTime="10:00",
        )
        listing = HostawayListing.from_api_response(data)
        assert listing.check_in_time_start == "14:00"
        assert listing.check_in_time_end == "22:00"
        assert listing.check_out_time == "10:00"

    def test_is_listed_field(self) -> None:
        """isListed field is correctly mapped."""
        data = make_listing_response(isListed=1)
        listing = HostawayListing.from_api_response(data)
        assert listing.is_listed is True

    def test_is_listed_false(self) -> None:
        """isListed=0 maps to False."""
        data = make_listing_response(isListed=0)
        listing = HostawayListing.from_api_response(data)
        assert listing.is_listed is False

    def test_optional_fields_default_to_none(self) -> None:
        """Optional fields default to None when missing."""
        data = {"id": 1, "name": "Minimal"}
        listing = HostawayListing.from_api_response(data)
        assert listing.internal_name is None
        assert listing.address is None
        assert listing.city is None
        assert listing.country_code is None
        assert listing.property_type is None
        assert listing.bedrooms is None
        assert listing.bathrooms is None
        assert listing.max_guests is None
        assert listing.base_price is None
        assert listing.currency is None
        assert listing.check_in_time_start is None
        assert listing.check_in_time_end is None
        assert listing.check_out_time is None
        assert listing.is_listed is None

    def test_missing_id_raises(self) -> None:
        """Missing id raises ValueError."""
        data = {"name": "No ID"}
        with pytest.raises(ValueError, match="id"):
            HostawayListing.from_api_response(data)

    def test_missing_name_raises(self) -> None:
        """Missing name raises ValueError."""
        data = {"id": 1}
        with pytest.raises(ValueError, match="name"):
            HostawayListing.from_api_response(data)


class TestHostawayReservationFromApiResponse:
    """Tests for HostawayReservation.from_api_response parsing."""

    def test_basic_field_mapping(self) -> None:
        """Fields are mapped from camelCase to snake_case."""
        data = make_reservation_response()
        res = HostawayReservation.from_api_response(data)
        assert res.id == 99001
        assert res.listing_id == 12345
        assert res.guest_name == "John Doe"
        assert res.check_in == "2025-08-01"
        assert res.check_out == "2025-08-05"
        assert res.status == "confirmed"
        assert res.channel == "airbnb"
        assert res.num_guests == 3
        assert res.total_price == 600.00
        assert res.currency == "USD"

    def test_door_code_fields(self) -> None:
        """Door code fields are correctly mapped."""
        data = make_reservation_response()
        res = HostawayReservation.from_api_response(data)
        assert res.door_code == "1234"
        assert res.door_code_vendor == "smartlock"
        assert res.door_code_instruction == "Use keypad on front door"

    def test_confirmation_and_nights(self) -> None:
        """Confirmation code and nights are correctly mapped."""
        data = make_reservation_response()
        res = HostawayReservation.from_api_response(data)
        assert res.confirmation_code == "ABC123"
        assert res.nights == 4

    def test_optional_fields_default_to_none(self) -> None:
        """Optional fields default to None when missing."""
        data = {
            "id": 1,
            "listingMapId": 2,
            "guestName": "Jane",
            "arrivalDate": "2025-08-01",
            "departureDate": "2025-08-02",
            "status": "new",
        }
        res = HostawayReservation.from_api_response(data)
        assert res.channel is None
        assert res.num_guests is None
        assert res.total_price is None
        assert res.currency is None
        assert res.door_code is None
        assert res.door_code_vendor is None
        assert res.door_code_instruction is None
        assert res.confirmation_code is None
        assert res.nights is None

    def test_missing_id_raises(self) -> None:
        """Missing id raises ValueError."""
        data = make_reservation_response()
        del data["id"]
        with pytest.raises(ValueError, match="id"):
            HostawayReservation.from_api_response(data)

    def test_missing_listing_id_raises(self) -> None:
        """Missing listingMapId raises ValueError."""
        data = make_reservation_response()
        del data["listingMapId"]
        with pytest.raises(ValueError, match="listing_id"):
            HostawayReservation.from_api_response(data)

    def test_missing_guest_name_raises(self) -> None:
        """Missing guestName raises ValueError."""
        data = make_reservation_response()
        del data["guestName"]
        with pytest.raises(ValueError, match="guest_name"):
            HostawayReservation.from_api_response(data)

    def test_missing_check_in_raises(self) -> None:
        """Missing arrivalDate raises ValueError."""
        data = make_reservation_response()
        del data["arrivalDate"]
        with pytest.raises(ValueError, match="check_in"):
            HostawayReservation.from_api_response(data)

    def test_missing_check_out_raises(self) -> None:
        """Missing departureDate raises ValueError."""
        data = make_reservation_response()
        del data["departureDate"]
        with pytest.raises(ValueError, match="check_out"):
            HostawayReservation.from_api_response(data)

    def test_missing_status_raises(self) -> None:
        """Missing status raises ValueError."""
        data = make_reservation_response()
        del data["status"]
        with pytest.raises(ValueError, match="status"):
            HostawayReservation.from_api_response(data)
