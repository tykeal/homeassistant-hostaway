# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Helper functions and constants for Hostaway reservation sensors."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from custom_components.hostaway.api.custom_fields import (
    RESERVATION_OBJECT_TYPE,
    HostawayCustomFieldDefinition,
)
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

_warned_invalid_check_out_date = False
_warned_undated_reservation = False

# Transient date groups used only for reservation selection.
_GROUP_PAST = "past"
_GROUP_CURRENT = "current"
_GROUP_UPCOMING = "upcoming"
_GROUP_UNDATED = "undated"


def _select_reservation(
    reservations: list[HostawayReservation],
    today: date,
) -> HostawayReservation | None:
    """Select the reservation that should drive the sensor.

    Past reservations (a valid check-out date before ``today``) are never
    selected. Remaining reservations rank by date group first (current,
    then upcoming, then undated), then by ``_STATUS_PRIORITY`` with unknown
    statuses after all known statuses. Current stays then prefer the later
    check-in, upcoming stays the earlier check-in, and dated ties fall back to
    the lowest reservation ID. Undated ties keep input order.

    Args:
        reservations: List of reservations for a listing.
        today: Current Home Assistant local calendar date.

    Returns:
        The selected reservation, or None if no reservation is eligible.
    """
    global _warned_undated_reservation

    fallback = max(_STATUS_PRIORITY.values()) + 1
    selected: HostawayReservation | None = None
    selected_rank: tuple[int, int, int, int] | None = None
    for index, reservation in enumerate(reservations):
        group, check_in = _classify_reservation(reservation, today)
        if group == _GROUP_PAST:
            continue
        priority = _STATUS_PRIORITY.get(reservation.status, fallback)
        if group == _GROUP_CURRENT and check_in is not None:
            rank = (0, priority, -check_in.toordinal(), reservation.id)
        elif group == _GROUP_UPCOMING and check_in is not None:
            rank = (1, priority, check_in.toordinal(), reservation.id)
        else:
            if not _warned_undated_reservation:
                _LOGGER.warning(
                    "Hostaway reservation without usable stay dates is ranked "
                    "after dated reservations for sensor selection",
                )
                _warned_undated_reservation = True
            rank = (2, priority, 0, index)
        if selected_rank is None or rank < selected_rank:
            selected = reservation
            selected_rank = rank
    return selected


def _classify_reservation(
    reservation: HostawayReservation,
    today: date,
) -> tuple[str, date | None]:
    """Classify a reservation into a date group relative to ``today``.

    A reservation is past when its check-out date is valid and before
    ``today``, whatever its check-in value. Otherwise it is undated when either
    date is invalid or check-out precedes check-in, current when check-in is on
    or before ``today``, and upcoming otherwise.

    Args:
        reservation: Reservation to classify.
        today: Current Home Assistant local calendar date.

    Returns:
        The date group and, for current or upcoming reservations, the parsed
        check-in date.
    """
    check_out = _parse_calendar_date(reservation.check_out)
    if check_out is not None and check_out < today:
        return _GROUP_PAST, None
    check_in = _parse_calendar_date(reservation.check_in)
    if check_in is None or check_out is None or check_out < check_in:
        return _GROUP_UNDATED, None
    if check_in <= today:
        return _GROUP_CURRENT, check_in
    return _GROUP_UPCOMING, check_in


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


def _parse_calendar_date(value: object) -> date | None:
    """Parse a canonical Hostaway ``YYYY-MM-DD`` calendar date.

    Args:
        value: Candidate date value from a reservation.

    Returns:
        Parsed date, or None if the value is not a canonical ISO date.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _filter_upcoming_reservations(
    reservations: list[HostawayReservation],
    today: date,
) -> list[HostawayReservation]:
    """Keep reservations that are not known to be completely in the past.

    Only canonical ``YYYY-MM-DD`` check-out dates are used for comparison.
    Entries with an invalid date are retained because their past status cannot
    be established from the available data.

    Args:
        reservations: Reservations in their existing sorted order.
        today: Current Home Assistant local calendar date.

    Returns:
        Reservations checking out today or later, plus entries whose check-out
        date cannot be parsed.
    """
    global _warned_invalid_check_out_date

    upcoming: list[HostawayReservation] = []
    for reservation in reservations:
        check_out = _parse_calendar_date(reservation.check_out)
        if check_out is None:
            if not _warned_invalid_check_out_date:
                _LOGGER.warning(
                    "Hostaway reservation with an invalid check-out date is "
                    "retained in upcoming_reservations",
                )
                _warned_invalid_check_out_date = True
            upcoming.append(reservation)
            continue

        if check_out >= today:
            upcoming.append(reservation)

    return upcoming


def _build_reservation_attributes(
    reservation: HostawayReservation | None,
    upcoming_reservations: list[HostawayReservation],
    listing_id: int,
    definitions: list[HostawayCustomFieldDefinition] | None = None,
) -> dict[str, Any]:
    """Build extra_state_attributes for the reservation sensor.

    Includes the selected reservation's details and an
    ``upcoming_reservations`` list. The coordinator already
    sorts reservations by check_in, so order is preserved.

    Args:
        reservation: The selected reservation, or None.
        upcoming_reservations: Reservations to include in the upcoming list,
            pre-sorted by check_in from coordinator.
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
        for current in upcoming_reservations
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
            "custom_fields": {},
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
        "custom_fields": _reservation_custom_fields(reservation, definitions or []),
    }


def _reservation_custom_fields(
    reservation: HostawayReservation,
    definitions: list[HostawayCustomFieldDefinition],
) -> dict[str, dict[str, Any]]:
    """Return reservation custom-field attributes keyed by customFieldId."""
    definition_by_id = {
        definition.custom_field_id: definition
        for definition in definitions
        if definition.object_type == RESERVATION_OBJECT_TYPE
    }
    collection = reservation.custom_field_collection
    if collection is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for custom_field_id, field_value in collection.values.items():
        key = f"custom_field_{custom_field_id}"
        definition = definition_by_id.get(custom_field_id)
        if definition is None:
            result[key] = {
                "customFieldId": custom_field_id,
                "value": field_value.value,
                "resolved": False,
            }
            continue
        result[key] = {
            "customFieldId": custom_field_id,
            "varName": definition.var_name,
            "name": definition.name,
            "type": definition.field_type,
            "possibleValues": list(definition.possible_values),
            "value": field_value.value,
            "resolved": True,
        }
    return result
