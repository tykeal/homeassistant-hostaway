# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for Hostaway sensor tests."""

from __future__ import annotations

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
    status: str = "active",
) -> HostawayListing:
    """Create a HostawayListing for testing.

    Args:
        listing_id: The listing ID.
        name: The listing name.
        internal_name: The internal reference name.
        status: The listing status.

    Returns:
        A HostawayListing instance.
    """
    return HostawayListing(
        id=listing_id,
        name=name,
        internal_name=internal_name,
        status=status,
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
