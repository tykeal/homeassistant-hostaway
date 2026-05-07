# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects for the Hostaway API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from custom_components.hostaway.api.const import TOKEN_READY_DELAY


def _validate_non_negative(
    value: object, field: str, *, types: tuple[type, ...] = (int,)
) -> None:
    """Validate that a value is a non-negative number of given type.

    Args:
        value: The value to validate (skip if None).
        field: Field name for the error message.
        types: Acceptable numeric types.

    Raises:
        ValueError: If value is wrong type or negative.
    """
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, types):
        msg = f"{field} must be a valid number"
        raise ValueError(msg)
    if value < 0:  # type: ignore[operator]
        msg = f"{field} must be non-negative"
        raise ValueError(msg)


def _validate_positive(
    value: object, field: str, *, types: tuple[type, ...] = (int,)
) -> None:
    """Validate that a value is a positive number of given type.

    Args:
        value: The value to validate (skip if None).
        field: Field name for the error message.
        types: Acceptable numeric types.

    Raises:
        ValueError: If value is wrong type or not positive.
    """
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, types):
        msg = f"{field} must be a valid number"
        raise ValueError(msg)
    if value <= 0:  # type: ignore[operator]
        msg = f"{field} must be positive"
        raise ValueError(msg)


@dataclass(frozen=True)
class AccessToken:
    """Immutable representation of a cached OAuth 2.0 access token.

    Attributes:
        access_token: The Bearer token value.
        token_type: Token type (e.g. "Bearer"), must be non-empty.
        expires_in: Token lifetime in seconds.
        issued_at: Timezone-aware timestamp when the token was acquired.
    """

    access_token: str
    token_type: str
    expires_in: int
    issued_at: datetime

    def __post_init__(self) -> None:
        """Validate token fields after initialization.

        Raises:
            ValueError: If access_token is empty, expires_in is not
                positive, or issued_at is naive (no timezone).
        """
        if not self.access_token:
            msg = "access_token must be non-empty"
            raise ValueError(msg)
        if not self.token_type:
            msg = "token_type must be non-empty"
            raise ValueError(msg)
        if not isinstance(self.expires_in, int):
            msg = "expires_in must be an integer"
            raise ValueError(msg)
        if self.expires_in <= 0:
            msg = "expires_in must be positive"
            raise ValueError(msg)
        if (
            self.issued_at.tzinfo is None
            or self.issued_at.tzinfo.utcoffset(self.issued_at) is None
        ):
            msg = "issued_at must be timezone-aware"
            raise ValueError(msg)

    @property
    def expires_at(self) -> datetime:
        """Compute the expiration timestamp.

        Returns:
            Datetime when this token expires (same tz as issued_at).
        """
        return self.issued_at + timedelta(seconds=self.expires_in)

    def is_expired(self, buffer_seconds: int = 0) -> bool:
        """Check whether the token is expired or within the buffer.

        Args:
            buffer_seconds: Safety margin in seconds before actual
                expiry to consider the token expired. Must be >= 0.

        Returns:
            True if the token is expired or within the buffer window.

        Raises:
            ValueError: If buffer_seconds is negative.
        """
        if buffer_seconds < 0:
            msg = "buffer_seconds must be non-negative"
            raise ValueError(msg)
        return datetime.now(UTC) >= self.expires_at - timedelta(
            seconds=buffer_seconds,
        )

    @property
    def seconds_until_ready(self) -> float:
        """Seconds remaining until the 1s post-generation delay passes.

        Hostaway requires a minimum delay after token generation
        before the token can be used.

        Returns:
            Seconds to wait (0.0 if already ready).
        """
        elapsed = (datetime.now(UTC) - self.issued_at).total_seconds()
        return max(0.0, TOKEN_READY_DELAY - elapsed)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            Dictionary with all token fields, issued_at as ISO string.
        """
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "issued_at": self.issued_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessToken:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with token fields.

        Returns:
            An AccessToken instance.
        """
        return cls(
            access_token=data["access_token"],
            token_type=data["token_type"],
            expires_in=data["expires_in"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
        )


@dataclass(frozen=True)
class HostawayListing:
    """Immutable representation of a Hostaway property listing.

    Attributes:
        id: Unique listing identifier.
        name: Public listing name.
        internal_name: Internal reference name.
        status: Listing status ('active', 'inactive', or None).
        address: Full address string.
        city: City name.
        country_code: ISO country code.
        property_type: Type of property.
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms (may be fractional).
        max_guests: Maximum guest capacity.
        base_price: Default nightly price.
        currency: Currency code.
        check_in_time_start: Earliest check-in time (HH:MM).
        check_in_time_end: Latest check-in time (HH:MM).
        check_out_time: Check-out time (HH:MM).
        is_listed: Whether listing is publicly visible.
    """

    id: int
    name: str
    internal_name: str | None = None
    status: str | None = None
    address: str | None = None
    city: str | None = None
    country_code: str | None = None
    property_type: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    max_guests: int | None = None
    base_price: float | None = None
    currency: str | None = None
    check_in_time_start: str | None = None
    check_in_time_end: str | None = None
    check_out_time: str | None = None
    is_listed: bool | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> HostawayListing:
        """Create a HostawayListing from a Hostaway API response dict.

        Args:
            data: Dictionary with camelCase keys from the API.

        Returns:
            A HostawayListing instance.

        Raises:
            ValueError: If required fields (id, name) are missing.
        """
        if "id" not in data:
            msg = "id is required"
            raise ValueError(msg)
        if not isinstance(data["id"], int) or data["id"] <= 0:
            msg = "id must be a positive integer"
            raise ValueError(msg)
        if "name" not in data:
            msg = "name is required"
            raise ValueError(msg)
        if not data["name"]:
            msg = "name must be non-empty"
            raise ValueError(msg)

        # Map isActive (0/1) to status
        is_active = data.get("isActive")
        status: str | None = None
        if is_active == 1:
            status = "active"
        elif is_active == 0:
            status = "inactive"

        # Handle address (string or nested object)
        raw_address = data.get("address")
        address: str | None = None
        if isinstance(raw_address, dict):
            address = raw_address.get("full")
        elif isinstance(raw_address, str):
            address = raw_address

        # Map isListed (0/1) to boolean
        raw_is_listed = data.get("isListed")
        is_listed: bool | None = None
        if raw_is_listed == 1:
            is_listed = True
        elif raw_is_listed == 0:
            is_listed = False

        # Validate optional numeric fields when present
        bedrooms = data.get("bedroomsNumber")
        _validate_non_negative(bedrooms, "bedrooms")
        bathrooms = data.get("bathroomsNumber")
        _validate_non_negative(bathrooms, "bathrooms", types=(int, float))
        max_guests = data.get("personCapacity")
        _validate_positive(max_guests, "max_guests")
        base_price = data.get("price")
        _validate_non_negative(base_price, "base_price", types=(int, float))

        return cls(
            id=data["id"],
            name=data["name"],
            internal_name=data.get("internalName"),
            status=status,
            address=address,
            city=data.get("city"),
            country_code=data.get("countryCode"),
            property_type=data.get("propertyType"),
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            max_guests=max_guests,
            base_price=base_price,
            currency=data.get("currencyCode"),
            check_in_time_start=data.get("checkInTimeStart"),
            check_in_time_end=data.get("checkInTimeEnd"),
            check_out_time=data.get("checkOutTime"),
            is_listed=is_listed,
        )


@dataclass(frozen=True)
class HostawayReservation:
    """Immutable representation of a Hostaway reservation.

    Attributes:
        id: Unique reservation identifier.
        listing_id: Associated listing ID.
        guest_name: Primary guest name.
        check_in: Arrival date string.
        check_out: Departure date string.
        status: Reservation status.
        channel: Booking channel name.
        num_guests: Number of guests.
        total_price: Total reservation price.
        currency: Currency code.
        door_code: Access door code.
        door_code_vendor: Door code system vendor.
        door_code_instruction: Instructions for door access.
        confirmation_code: External confirmation code.
        nights: Number of nights.
    """

    id: int
    listing_id: int
    guest_name: str
    check_in: str
    check_out: str
    status: str
    channel: str | None = None
    num_guests: int | None = None
    total_price: float | None = None
    currency: str | None = None
    door_code: str | None = None
    door_code_vendor: str | None = None
    door_code_instruction: str | None = None
    confirmation_code: str | None = None
    nights: int | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> HostawayReservation:
        """Create a HostawayReservation from an API response dict.

        Args:
            data: Dictionary with camelCase keys from the API.

        Returns:
            A HostawayReservation instance.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        required_mappings = {
            "id": "id",
            "listingMapId": "listing_id",
            "guestName": "guest_name",
            "arrivalDate": "check_in",
            "departureDate": "check_out",
            "status": "status",
        }
        for api_key, field_name in required_mappings.items():
            if api_key not in data:
                msg = f"{field_name} is required"
                raise ValueError(msg)

        if not isinstance(data["id"], int) or data["id"] <= 0:
            msg = "id must be a positive integer"
            raise ValueError(msg)
        listing_map_id = data["listingMapId"]
        if not isinstance(listing_map_id, int) or listing_map_id <= 0:
            msg = "listing_id must be a positive integer"
            raise ValueError(msg)
        if not data["guestName"]:
            msg = "guest_name must be non-empty"
            raise ValueError(msg)
        if not data["arrivalDate"]:
            msg = "check_in must be non-empty"
            raise ValueError(msg)
        if not data["departureDate"]:
            msg = "check_out must be non-empty"
            raise ValueError(msg)
        if not data["status"]:
            msg = "status must be non-empty"
            raise ValueError(msg)

        # Validate optional numeric fields when present
        num_guests = data.get("numberOfGuests")
        _validate_positive(num_guests, "num_guests")
        total_price = data.get("totalPrice")
        _validate_non_negative(total_price, "total_price", types=(int, float))
        nights = data.get("nights")
        _validate_positive(nights, "nights")

        return cls(
            id=data["id"],
            listing_id=data["listingMapId"],
            guest_name=data["guestName"],
            check_in=data["arrivalDate"],
            check_out=data["departureDate"],
            status=data["status"],
            channel=data.get("channelName"),
            num_guests=num_guests,
            total_price=total_price,
            currency=data.get("currency"),
            door_code=data.get("doorCode"),
            door_code_vendor=data.get("doorCodeVendor"),
            door_code_instruction=data.get("doorCodeInstruction"),
            confirmation_code=data.get("confirmationCode"),
            nights=nights,
        )
