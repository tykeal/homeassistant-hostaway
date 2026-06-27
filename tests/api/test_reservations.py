# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway reservation pagination helpers."""

from __future__ import annotations

import pytest

from custom_components.hostaway.api.reservations import (
    parse_reservations,
    reservation_page_cursor,
)
from tests.helpers import make_reservation_response


def test_parse_reservations_skips_malformed_item(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed reservation items are skipped with a warning."""
    listing_id = 12345
    valid = make_reservation_response(id=1, listingMapId=listing_id)
    malformed = make_reservation_response(id=2, listingMapId=listing_id, nights=-1)

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        reservations = parse_reservations([valid, malformed], listing_id)

    assert [reservation.id for reservation in reservations] == [1]
    assert f"reservation 2 for listing {listing_id}" in caplog.text
    assert "nights must be non-negative" in caplog.text


def test_reservation_page_cursor_uses_last_valid_raw_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cursor falls back to the last valid raw reservation ID."""
    listing_id = 12345
    items = [
        make_reservation_response(id=1, listingMapId=listing_id),
        make_reservation_response(id=2, listingMapId=listing_id),
        make_reservation_response(id=True, listingMapId=listing_id),
    ]

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        cursor = reservation_page_cursor(items, listing_id)

    assert cursor == 2
    assert "Using reservation 2 as pagination cursor" in caplog.text
    assert "invalid id True" in caplog.text


def test_reservation_page_cursor_stops_without_valid_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cursor returns None when a raw page has no valid IDs."""
    listing_id = 12345
    items = [
        make_reservation_response(id=0, listingMapId=listing_id),
        make_reservation_response(id="bad", listingMapId=listing_id),
    ]

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        cursor = reservation_page_cursor(items, listing_id)

    assert cursor is None
    assert "no valid cursor id" in caplog.text
