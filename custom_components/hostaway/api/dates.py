# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Calendar date parsing helpers for Hostaway API payloads."""

from __future__ import annotations

from datetime import date


def parse_calendar_date(value: object) -> date | None:
    """Parse a canonical Hostaway ``YYYY-MM-DD`` calendar date.

    Args:
        value: Candidate date value from an API payload.

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
