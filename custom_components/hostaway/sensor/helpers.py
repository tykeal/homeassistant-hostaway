# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Helper functions and constants for Hostaway reservation sensors."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from typing import Any

from custom_components.hostaway.api.models import HostawayReservation

_LOGGER = logging.getLogger(__name__)

# Statuses already warned about to avoid log spam.
# Capped to prevent unbounded growth from pathological API data.
_MAX_WARNED_STATUSES = 50
_warned_statuses: set[str] = set()

# Priority for selecting the most relevant reservation.
# Lower number = higher priority.
_STATUS_PRIORITY: dict[str, int] = {
    "checked_in": 0,
    "confirmed": 1,
    "new": 1,
    "modified": 1,
    "pending": 2,
    "unconfirmed": 2,
    "awaitingPayment": 3,
    "awaitingGuestVerification": 3,
    "ownerStay": 4,
    "checked_out": 5,
    "cancelled": 6,
    "declined": 7,
    "expired": 7,
    "inquiry": 8,
    "inquiryPreapproved": 8,
    "inquiryDenied": 9,
    "inquiryTimedout": 9,
    "inquiryNotPossible": 9,
    "unknown": 10,
}

# Map raw API statuses to user-friendly derived states.
_STATUS_TO_DERIVED: dict[str, str] = {
    "checked_in": "checked_in",
    "confirmed": "awaiting_checkin",
    "new": "awaiting_checkin",
    "modified": "awaiting_checkin",
    "pending": "pending_approval",
    "unconfirmed": "pending_approval",
    "awaitingPayment": "awaiting_guest",
    "awaitingGuestVerification": "awaiting_guest",
    "ownerStay": "owner_stay",
    "checked_out": "checked_out",
    "cancelled": "cancelled",
    "declined": "cancelled",
    "expired": "cancelled",
    "inquiry": "inquiry",
    "inquiryPreapproved": "inquiry",
    "inquiryDenied": "inquiry",
    "inquiryTimedout": "inquiry",
    "inquiryNotPossible": "inquiry",
    "unknown": "unknown",
}

_CANCELLED_STATUSES: frozenset[str] = frozenset(
    {"cancelled", "declined", "expired"},
)


def _select_reservation(
    reservations: list[HostawayReservation],
) -> HostawayReservation | None:
    """Select the highest-priority reservation.

    Uses ``_STATUS_PRIORITY`` to rank reservations. Unknown
    statuses sort after all known statuses.

    Args:
        reservations: List of reservations for a listing.

    Returns:
        The highest-priority reservation, or None if empty.
    """
    if not reservations:
        return None
    fallback = max(_STATUS_PRIORITY.values()) + 1
    return min(
        reservations,
        key=lambda reservation: _STATUS_PRIORITY.get(reservation.status, fallback),
    )


def _derive_state(
    reservation: HostawayReservation | None,
) -> str:
    """Derive the sensor state from a reservation.

    Maps raw API statuses to user-friendly derived states
    using ``_STATUS_TO_DERIVED``. Returns ``no_reservation``
    when no reservation is selected, and ``unknown`` for
    unrecognised statuses (with a warning log).

    Args:
        reservation: The selected reservation, or None.

    Returns:
        The derived state string.
    """
    if reservation is None:
        return "no_reservation"
    derived = _STATUS_TO_DERIVED.get(reservation.status)
    if derived is not None:
        return derived
    if (
        reservation.status not in _warned_statuses
        and len(_warned_statuses) < _MAX_WARNED_STATUSES
    ):
        _warned_statuses.add(reservation.status)
        _LOGGER.warning(
            "Unknown Hostaway reservation status '%s'; reporting as 'unknown'",
            reservation.status,
        )
    return "unknown"


def _build_reservation_attributes(
    reservation: HostawayReservation | None,
    all_reservations: list[HostawayReservation],
    listing_id: int,
) -> dict[str, Any]:
    """Build extra_state_attributes for the reservation sensor.

    Includes the selected reservation's details and an
    ``upcoming_reservations`` list. The coordinator already
    sorts reservations by check_in, so order is preserved.

    Args:
        reservation: The selected reservation, or None.
        all_reservations: All reservations for the listing
            (pre-sorted by check_in from coordinator).
        listing_id: The listing ID.

    Returns:
        Dictionary of extra state attributes per FR-R04.
    """
    upcoming = [
        {
            "id": current.id,
            "guest_name": current.guest_name,
            "check_in": current.check_in,
            "check_out": current.check_out,
            "status": current.status,
        }
        for current in all_reservations
    ]

    if reservation is None:
        return {
            "reservation_id": None,
            "guest_name": None,
            "check_in": None,
            "check_out": None,
            "status": None,
            "door_code": None,
            "door_code_vendor": None,
            "door_code_instruction": None,
            "num_guests": None,
            "confirmation_code": None,
            "listing_id": listing_id,
            "upcoming_reservations": upcoming,
        }

    return {
        "reservation_id": reservation.id,
        "guest_name": reservation.guest_name,
        "check_in": reservation.check_in,
        "check_out": reservation.check_out,
        "status": reservation.status,
        "door_code": reservation.door_code,
        "door_code_vendor": reservation.door_code_vendor,
        "door_code_instruction": reservation.door_code_instruction,
        "num_guests": reservation.num_guests,
        "confirmation_code": reservation.confirmation_code,
        "listing_id": listing_id,
        "upcoming_reservations": upcoming,
    }
