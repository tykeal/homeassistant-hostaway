# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway custom-field sensor foundation."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from custom_components.hostaway.sensor.custom_fields import (
    HostawayListingCustomFieldSensor,
    ListingCustomFieldKeyAllocation,
)


def test_custom_field_sensor_placeholders_exist() -> None:
    """Foundation exposes allocation and sensor extension points."""
    allocation = ListingCustomFieldKeyAllocation(listing_id=123)

    assert allocation.listing_id == 123
    assert allocation.field_to_key == {}
    assert HostawayListingCustomFieldSensor is not None
