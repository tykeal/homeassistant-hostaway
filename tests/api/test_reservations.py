# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway reservation pagination helpers."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from custom_components.hostaway.api import reservations as reservations_module
from custom_components.hostaway.api.const import DEFAULT_PAGE_LIMIT
from custom_components.hostaway.api.reservations import (
    fetch_all_reservations,
    parse_reservations,
    reservation_page_cursor,
)
from tests.helpers import make_reservation_response

_LOGGER_NAME = "custom_components.hostaway.api.reservations"


def _levels_for(caplog: pytest.LogCaptureFixture, text: str) -> list[int]:
    """Return the log levels of captured records containing ``text``."""
    return [record.levelno for record in caplog.records if text in record.getMessage()]


async def test_fetch_all_reservations_does_not_parse_stale_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-advancing page stops before parsing discarded items."""
    listing_id = 12345
    page1 = [
        make_reservation_response(id=item_id, listingMapId=listing_id)
        for item_id in range(1, DEFAULT_PAGE_LIMIT + 1)
    ]
    page2 = [
        make_reservation_response(
            id=item_id,
            listingMapId=listing_id,
            nights=-1 if item_id == 1 else 4,
        )
        for item_id in range(1, DEFAULT_PAGE_LIMIT + 1)
    ]
    pages = iter([page1, page2])

    async def fetch_page(_after_id: int | None, _limit: int) -> list[dict[str, Any]]:
        """Return the next synthetic reservation page."""
        return next(pages)

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        reservations = await fetch_all_reservations(fetch_page, listing_id)

    assert [reservation.id for reservation in reservations] == list(
        range(1, DEFAULT_PAGE_LIMIT + 1)
    )
    assert "did not advance" in caplog.text
    assert "Skipping malformed" not in caplog.text


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


@pytest.mark.parametrize(
    ("arrival", "departure"),
    [
        ("2025-13-01", "2025-08-05"),
        ("2025-08-01", "08/05/2025"),
        ("2025-08-06", "2025-08-05"),
        ("", "2025-08-05"),
    ],
)
def test_parse_reservations_drops_invalid_dates_logging_only_ids(
    caplog: pytest.LogCaptureFixture, arrival: str, departure: str
) -> None:
    """Invalid-date records are dropped and only identifiers are logged."""
    listing_id = 12345
    valid = make_reservation_response(id=1, listingMapId=listing_id)
    invalid = make_reservation_response(
        id=2,
        listingMapId=listing_id,
        guestName="Secret Guest",
        arrivalDate=arrival,
        departureDate=departure,
    )

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        reservations = parse_reservations([valid, invalid, valid], listing_id)

    assert [reservation.id for reservation in reservations] == [1, 1]
    assert f"reservation 2 for listing {listing_id}" in caplog.text
    assert "Secret Guest" not in caplog.text
    for value in (arrival, departure):
        if value and value != "2025-08-05":
            assert value not in caplog.text


@pytest.mark.parametrize("raw_id", ["guest-secret", True, -5, 1.5, None])
def test_parse_reservations_omits_unusable_ids_from_log(
    caplog: pytest.LogCaptureFixture, raw_id: Any
) -> None:
    """Skipped records without a positive integer ID log no raw ID value."""
    listing_id = 12345
    item = make_reservation_response(
        id=raw_id, listingMapId=listing_id, arrivalDate="bad"
    )

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        reservations = parse_reservations([item], listing_id)

    assert reservations == []
    assert (
        f"Skipping malformed Hostaway reservation for listing {listing_id}"
        in caplog.text
    )
    assert str(raw_id) not in caplog.text.replace(str(listing_id), "")


async def test_fetch_all_reservations_invalid_last_id_logs_no_raw_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A full page ending in an unusable ID never logs that raw value."""
    listing_id = 12345
    page = [
        make_reservation_response(id=item_id, listingMapId=listing_id)
        for item_id in range(1, DEFAULT_PAGE_LIMIT)
    ]
    page.append(make_reservation_response(id="guest-secret", listingMapId=listing_id))
    pages = iter([page, []])

    async def fetch_page(_after_id: int | None, _limit: int) -> list[dict[str, Any]]:
        """Return the next synthetic reservation page."""
        return next(pages)

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        reservations = await fetch_all_reservations(fetch_page, listing_id)

    assert len(reservations) == DEFAULT_PAGE_LIMIT - 1
    assert "guest-secret" not in caplog.text


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
    assert "last raw reservation has an invalid id" in caplog.text
    assert "True" not in caplog.text


def test_reservation_page_cursor_warns_for_bool_last_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A boolean final raw ID still emits the fallback warning."""
    listing_id = 12345
    items = [
        make_reservation_response(id=1, listingMapId=listing_id),
        make_reservation_response(id=True, listingMapId=listing_id),
    ]

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        cursor = reservation_page_cursor(items, listing_id)

    assert cursor == 1
    assert "Using reservation 1 as pagination cursor" in caplog.text
    assert "last raw reservation has an invalid id" in caplog.text
    assert "True" not in caplog.text


def test_reservation_page_cursor_stops_on_empty_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cursor returns None when a raw page is empty."""
    listing_id = 12345

    with caplog.at_level(
        "WARNING", logger="custom_components.hostaway.api.reservations"
    ):
        cursor = reservation_page_cursor([], listing_id)

    assert cursor is None
    assert "raw reservation page is empty" in caplog.text


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


def test_skipped_reservation_warns_once_then_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A permanently bad record warns on first sight and logs DEBUG after."""
    listing_id = 12345
    invalid = make_reservation_response(
        id=2,
        listingMapId=listing_id,
        guestName="Secret Guest",
        arrivalDate="2025-13-01",
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(3):
            assert parse_reservations([invalid], listing_id) == []

    assert _levels_for(caplog, f"reservation 2 for listing {listing_id}") == [
        logging.WARNING,
        logging.DEBUG,
        logging.DEBUG,
    ]
    assert "check_in must be a YYYY-MM-DD date" in caplog.text
    assert "Secret Guest" not in caplog.text
    assert "2025-13-01" not in caplog.text


def test_distinct_skipped_reservations_each_warn_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each distinct listing and reservation pair warns exactly once."""
    items_a = [
        make_reservation_response(id=2, listingMapId=1, arrivalDate="bad"),
        make_reservation_response(id=3, listingMapId=1, arrivalDate="bad"),
    ]
    items_b = [make_reservation_response(id=2, listingMapId=7, arrivalDate="bad")]

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(2):
            parse_reservations(items_a, 1)
            parse_reservations(items_b, 7)

    for text in (
        "reservation 2 for listing 1:",
        "reservation 3 for listing 1:",
        "reservation 2 for listing 7:",
    ):
        assert _levels_for(caplog, text) == [logging.WARNING, logging.DEBUG]


def test_fixed_then_broken_reservation_warns_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record that parses after a skip warns again if it later breaks."""
    listing_id = 12345
    invalid = make_reservation_response(
        id=2, listingMapId=listing_id, arrivalDate="bad"
    )
    fixed = make_reservation_response(id=2, listingMapId=listing_id)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        parse_reservations([invalid], listing_id)
        assert [r.id for r in parse_reservations([fixed], listing_id)] == [2]
        parse_reservations([invalid], listing_id)

    assert _levels_for(caplog, f"reservation 2 for listing {listing_id}") == [
        logging.WARNING,
        logging.WARNING,
    ]


def test_skipped_reservations_without_id_dedupe_by_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ID-less records warn once per listing and generic reason."""
    listing_id = 12345
    bad_date = make_reservation_response(
        id="guest-secret", listingMapId=listing_id, arrivalDate="bad"
    )
    other_bad_date = make_reservation_response(
        id=None, listingMapId=listing_id, arrivalDate="bad"
    )
    no_id = make_reservation_response(listingMapId=listing_id)
    del no_id["id"]

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(2):
            parse_reservations([bad_date, other_bad_date, no_id], listing_id)
        parse_reservations([bad_date], 99)

    prefix = f"reservation for listing {listing_id}: "
    assert _levels_for(caplog, prefix + "id must be a positive integer") == [
        logging.WARNING,
        logging.DEBUG,
        logging.DEBUG,
        logging.DEBUG,
    ]
    assert _levels_for(caplog, prefix + "id is required") == [
        logging.WARNING,
        logging.DEBUG,
    ]
    assert _levels_for(caplog, "reservation for listing 99: ") == [logging.WARNING]
    assert "guest-secret" not in caplog.text


def test_warned_reservations_cap_logs_new_records_at_debug(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the warn-once set is full, new records log DEBUG only."""
    monkeypatch.setattr(reservations_module, "_MAX_WARNED_RESERVATIONS", 2)
    listing_id = 12345
    items = [
        make_reservation_response(id=item_id, listingMapId=listing_id, nights=-1)
        for item_id in (1, 2, 3)
    ]

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        parse_reservations(items, listing_id)

    assert _levels_for(caplog, "reservation 1 for") == [logging.WARNING]
    assert _levels_for(caplog, "reservation 2 for") == [logging.WARNING]
    assert _levels_for(caplog, "reservation 3 for") == [logging.DEBUG]
    assert len(reservations_module._warned_reservations) == 2


async def test_pagination_cursor_fallback_warns_once_per_listing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cursor fallback for a bad last record does not repeat each refresh."""
    listing_id = 12345
    page = [
        make_reservation_response(id=item_id, listingMapId=listing_id)
        for item_id in range(1, DEFAULT_PAGE_LIMIT)
    ]
    page.append(make_reservation_response(id="guest-secret", listingMapId=listing_id))

    async def fetch_page(after_id: int | None, _limit: int) -> list[dict[str, Any]]:
        """Return the full page first and an empty page after the cursor."""
        return page if after_id is None else []

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(2):
            await fetch_all_reservations(fetch_page, listing_id)

    assert _levels_for(caplog, "as pagination cursor") == [
        logging.WARNING,
        logging.DEBUG,
    ]
    assert _levels_for(caplog, "Skipping malformed") == [
        logging.WARNING,
        logging.DEBUG,
    ]
    assert "guest-secret" not in caplog.text
