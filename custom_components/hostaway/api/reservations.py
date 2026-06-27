# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation pagination helpers for the Hostaway API client."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeGuard

from custom_components.hostaway.api.const import DEFAULT_PAGE_LIMIT
from custom_components.hostaway.api.models import HostawayReservation

_LOGGER = logging.getLogger(__name__)


class RequestResults(Protocol):
    """Callable interface for fetching validated Hostaway result lists."""

    def __call__(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        error_prefix: str = "API error",
    ) -> Awaitable[list[dict[str, Any]]]:
        """Return a validated result list for an API endpoint."""


FetchReservationPage = Callable[[int | None, int], Awaitable[list[dict[str, Any]]]]


async def fetch_reservation_items(
    request_results: RequestResults,
    listing_id: int,
    after_id: int | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """Return one raw page of reservation payloads for a listing."""
    params: dict[str, Any] = {"listingId": listing_id, "limit": limit}
    if after_id is not None:
        params["afterId"] = after_id
    return await request_results("/v1/reservations", params=params)


def parse_reservations(
    items: list[dict[str, Any]], listing_id: int
) -> list[HostawayReservation]:
    """Parse reservation records, skipping malformed API records."""
    reservations: list[HostawayReservation] = []
    for item in items:
        try:
            reservations.append(HostawayReservation.from_api_response(item))
        except ValueError as exc:
            _log_skipped_reservation(item.get("id"), listing_id, exc)
    return reservations


def reservation_page_cursor(items: list[dict[str, Any]], listing_id: int) -> int | None:
    """Return the raw cursor ID for a full reservation page."""
    if not items:
        _LOGGER.warning(
            "Stopping reservation pagination for listing %s because "
            "the raw reservation page is empty",
            listing_id,
        )
        return None
    last_raw_id = items[-1].get("id")
    for item in reversed(items):
        cursor = item.get("id")
        if not _is_valid_cursor(cursor):
            continue
        if not _is_valid_cursor(last_raw_id) or cursor != last_raw_id:
            _LOGGER.warning(
                "Using reservation %s as pagination cursor for listing %s "
                "because the last raw reservation has invalid id %r",
                cursor,
                listing_id,
                last_raw_id,
            )
        return cursor
    _LOGGER.warning(
        "Stopping reservation pagination for listing %s because "
        "the raw reservation page has no valid cursor id",
        listing_id,
    )
    return None


async def fetch_all_reservations(
    fetch_page: FetchReservationPage, listing_id: int
) -> list[HostawayReservation]:
    """Return all parsed reservations for a listing via cursor pagination."""
    reservations: list[HostawayReservation] = []
    after_id: int | None = None
    while True:
        items = await fetch_page(after_id, DEFAULT_PAGE_LIMIT)
        if len(items) >= DEFAULT_PAGE_LIMIT:
            next_after_id = reservation_page_cursor(items, listing_id)
            if next_after_id is None:
                reservations.extend(parse_reservations(items, listing_id))
                return reservations
            if after_id is not None and next_after_id <= after_id:
                _LOGGER.warning(
                    "Stopping reservation pagination for listing %s because "
                    "cursor %s did not advance beyond afterId %s",
                    listing_id,
                    next_after_id,
                    after_id,
                )
                return reservations
            reservations.extend(parse_reservations(items, listing_id))
            after_id = next_after_id
            continue
        reservations.extend(parse_reservations(items, listing_id))
        return reservations


def _log_skipped_reservation(
    reservation_id: Any, listing_id: int, exc: ValueError
) -> None:
    """Log a skipped malformed reservation payload."""
    if reservation_id is None:
        _LOGGER.warning(
            "Skipping malformed Hostaway reservation for listing %s: %s",
            listing_id,
            exc,
        )
        return
    _LOGGER.warning(
        "Skipping malformed Hostaway reservation %r for listing %s: %s",
        reservation_id,
        listing_id,
        exc,
    )


def _is_valid_cursor(value: object) -> TypeGuard[int]:
    """Return whether a raw reservation ID is usable as a cursor."""
    return not isinstance(value, bool) and isinstance(value, int) and value > 0
