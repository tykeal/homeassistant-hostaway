# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects for the Hostaway API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from custom_components.hostaway.api.const import TOKEN_READY_DELAY


@dataclass(frozen=True)
class AccessToken:
    """Immutable representation of a cached OAuth 2.0 access token.

    Attributes:
        access_token: The Bearer token value.
        token_type: Token type (always "Bearer").
        expires_in: Token lifetime in seconds.
        issued_at: UTC timestamp when the token was acquired.
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
        if self.expires_in <= 0:
            msg = "expires_in must be positive"
            raise ValueError(msg)
        if self.issued_at.tzinfo is None:
            msg = "issued_at must be timezone-aware"
            raise ValueError(msg)

    @property
    def expires_at(self) -> datetime:
        """Compute the expiration timestamp.

        Returns:
            UTC datetime when this token expires.
        """
        return self.issued_at + timedelta(seconds=self.expires_in)

    def is_expired(self, buffer_seconds: int = 0) -> bool:
        """Check whether the token is expired or within the buffer.

        Args:
            buffer_seconds: Safety margin in seconds before actual
                expiry to consider the token expired.

        Returns:
            True if the token is expired or within the buffer window.
        """
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
        status: Listing status ('active' or 'inactive').
        address: Full address string.
        city: City name.
        country_code: ISO country code.
        property_type: Type of property.
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms.
        max_guests: Maximum guest capacity.
        base_price: Default nightly price.
        currency: Currency code.
        check_in_time_start: Earliest check-in hour.
        check_in_time_end: Latest check-in hour.
        check_out_time: Check-out hour.
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
    bathrooms: int | None = None
    max_guests: int | None = None
    base_price: float | None = None
    currency: str | None = None
    check_in_time_start: int | None = None
    check_in_time_end: int | None = None
    check_out_time: int | None = None
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
        if "name" not in data:
            msg = "name is required"
            raise ValueError(msg)

        # Map isActive to status
        is_active = data.get("isActive")
        status: str | None = None
        if is_active is not None:
            status = "active" if is_active == 1 else "inactive"

        # Handle address (string or nested object)
        raw_address = data.get("address")
        address: str | None = None
        if isinstance(raw_address, dict):
            address = raw_address.get("full")
        elif isinstance(raw_address, str):
            address = raw_address

        # Map isListed
        raw_is_listed = data.get("isListed")
        is_listed: bool | None = None
        if raw_is_listed is not None:
            is_listed = bool(raw_is_listed)

        return cls(
            id=data["id"],
            name=data["name"],
            internal_name=data.get("internalName"),
            status=status,
            address=address,
            city=data.get("city"),
            country_code=data.get("countryCode"),
            property_type=data.get("propertyType"),
            bedrooms=data.get("bedroomsNumber"),
            bathrooms=data.get("bathroomsNumber"),
            max_guests=data.get("personCapacity"),
            base_price=data.get("price"),
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
            ValueError: If required fields are missing.
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

        return cls(
            id=data["id"],
            listing_id=data["listingMapId"],
            guest_name=data["guestName"],
            check_in=data["arrivalDate"],
            check_out=data["departureDate"],
            status=data["status"],
            channel=data.get("channelName"),
            num_guests=data.get("numberOfGuests"),
            total_price=data.get("totalPrice"),
            currency=data.get("currency"),
            door_code=data.get("doorCode"),
            door_code_vendor=data.get("doorCodeVendor"),
            door_code_instruction=data.get("doorCodeInstruction"),
            confirmation_code=data.get("confirmationCode"),
            nights=data.get("nights"),
        )
