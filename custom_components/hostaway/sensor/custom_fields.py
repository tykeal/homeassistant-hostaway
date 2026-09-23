# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listing custom-field sensor allocation placeholders."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListingCustomFieldKeyAllocation:
    """Minimal per-listing custom-field key allocation state."""

    listing_id: int
    field_to_key: dict[int, str] = field(default_factory=dict)
    reserved_keys: set[str] = field(default_factory=set)
    persisted_keys: dict[int, str] = field(default_factory=dict)


class HostawayListingCustomFieldSensor:
    """Placeholder for dynamic listing custom-field sensors."""
