# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lookup-oriented Hostaway service handlers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayResponseError,
)

from . import helpers


async def async_handle_get_users(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.get_users service call.

    Resolves the correct config entry and returns the Hostaway
    account users list for lookup workflows.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Dict containing the users list: {"users": [...]}.

    Raises:
        HomeAssistantError: On API failure.
    """
    entry_data = helpers._resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        users = await api_client.get_users()
    except HostawayResponseError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve users: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve users: {exc}",
        ) from exc

    return {"users": users}


async def async_handle_get_groups(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.get_groups service call.

    Resolves the correct config entry and returns the Hostaway
    account groups list for lookup workflows.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Dict containing the groups list: {"groups": [...]}.

    Raises:
        HomeAssistantError: On API failure.
    """
    entry_data = helpers._resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        groups = await api_client.get_groups()
    except HostawayResponseError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve groups: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve groups: {exc}",
        ) from exc

    return {"groups": groups}
