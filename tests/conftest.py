# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Hostaway integration test suite."""

from __future__ import annotations

from typing import Any

# Common test constants
FAKE_CLIENT_ID = "test-client-id-12345"
FAKE_CLIENT_SECRET = "test-client-secret-abcdef"
FAKE_TOKEN = "test-access-token-jwt"
FAKE_TOKEN_URL = "https://api.hostaway.com/v1/accessTokens"
FAKE_BASE_URL = "https://api.hostaway.com"


def make_token_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway token endpoint response.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching the Hostaway token endpoint response format.
    """
    defaults: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": FAKE_TOKEN,
        "expires_in": 86400,
    }
    defaults.update(overrides)
    return defaults


def make_listing_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway listing API response dict.

    Uses camelCase keys matching the Hostaway API format.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching Hostaway listing API response format.
    """
    defaults: dict[str, Any] = {
        "id": 12345,
        "name": "Oceanview Suite",
        "internalName": "ocean-suite-1",
        "isActive": 1,
        "address": "123 Beach Road",
        "city": "Miami",
        "countryCode": "US",
        "propertyType": "apartment",
        "bedroomsNumber": 2,
        "bathroomsNumber": 1.5,
        "personCapacity": 4,
        "price": 150.00,
        "currencyCode": "USD",
        "checkInTimeStart": "15:00",
        "checkInTimeEnd": "20:00",
        "checkOutTime": "11:00",
        "isListed": 1,
    }
    defaults.update(overrides)
    return defaults


def make_reservation_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway reservation API response dict.

    Uses camelCase keys matching the Hostaway API format.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching Hostaway reservation API response format.
    """
    defaults: dict[str, Any] = {
        "id": 99001,
        "listingMapId": 12345,
        "guestName": "John Doe",
        "arrivalDate": "2025-08-01",
        "departureDate": "2025-08-05",
        "status": "confirmed",
        "channelName": "airbnb",
        "numberOfGuests": 3,
        "totalPrice": 600.00,
        "currency": "USD",
        "doorCode": "1234",
        "doorCodeVendor": "smartlock",
        "doorCodeInstruction": "Use keypad on front door",
        "confirmationCode": "ABC123",
        "nights": 4,
    }
    defaults.update(overrides)
    return defaults
