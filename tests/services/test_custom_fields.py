# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway custom-field service foundation."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hostaway.api.custom_fields import CustomFieldWriteSafetyGates
from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.services.custom_fields import (
    async_handle_set_custom_field,
)


async def test_listing_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Listing writes reject while live safety gate is false."""
    api_client = SimpleNamespace(get_listing=AsyncMock(), update_listing=AsyncMock())
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError, match="listing custom-field writes"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )

    api_client.get_listing.assert_not_called()
    api_client.update_listing.assert_not_called()


async def test_reservation_write_rejects_before_reads(hass: HomeAssistant) -> None:
    """Reservation writes reject while live safety gate is false."""
    api_client = SimpleNamespace(
        get_reservation=AsyncMock(),
        update_reservation=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "api_client": api_client,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(ServiceValidationError, match="reservation custom-field writes"):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "reservation",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )

    api_client.get_reservation.assert_not_called()
    api_client.update_reservation.assert_not_called()


async def test_failed_definitions_refresh_rejects_before_gate(
    hass: HomeAssistant,
) -> None:
    """Latest definition refresh failure disables writes before gates."""
    coordinator = SimpleNamespace(last_refresh_succeeded=False)
    hass.data.setdefault(DOMAIN, {})["entry-1"] = {
        "custom_fields_coordinator": coordinator,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
    }

    with pytest.raises(
        ServiceValidationError,
        match="custom field definitions refresh failed",
    ):
        await async_handle_set_custom_field(
            hass,
            {
                "target_type": "listing",
                "target_id": 1,
                "customFieldId": 2,
                "value": "x",
            },
        )


async def test_bool_target_id_rejected_before_resolution(
    hass: HomeAssistant,
) -> None:
    """Boolean target identifiers are rejected as invalid."""
    with pytest.raises(ValueError, match="target_id"):
        await async_handle_set_custom_field(
            hass,
            {"target_type": "listing", "target_id": True, "value": "x"},
        )
