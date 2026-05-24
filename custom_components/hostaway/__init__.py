# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway integration for Home Assistant.

Provides OAuth 2.0 authenticated access to the Hostaway API
for property management automation. Manages the API client
lifecycle and token persistence across HA restarts.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
)
from custom_components.hostaway.api.models import AccessToken
from custom_components.hostaway.const import (
    CONF_CACHED_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
    PLATFORMS,
)
from custom_components.hostaway.coordinator import (
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Hostaway from a config entry.

    Creates the HTTP client, token manager, API client, and
    coordinators. Seeds persisted token if available and validates
    connectivity.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to set up.

    Returns:
        True if setup succeeded.

    Raises:
        ConfigEntryNotReady: If the API is unreachable.
    """
    http_client = get_async_client(hass)

    token_manager = HostawayTokenManager(
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        http_client=http_client,
    )

    # Restore persisted token if available
    cached = entry.data.get(CONF_CACHED_TOKEN)
    if cached is not None:
        try:
            token = AccessToken.from_dict(cached)
            token_manager.seed_token(token)
        except (KeyError, ValueError, TypeError) as exc:
            _LOGGER.warning("Failed to restore cached token: %s", exc)

    api_client = HostawayApiClient(
        token_manager=token_manager,
        http_client=http_client,
    )

    try:
        await api_client.test_connection()
    except HostawayAuthError as exc:
        raise ConfigEntryAuthFailed(
            f"Invalid Hostaway credentials: {exc}",
        ) from exc
    except (HostawayConnectionError, HostawayRateLimitError) as exc:
        raise ConfigEntryNotReady(
            f"Unable to connect to Hostaway: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise ConfigEntryNotReady(
            f"Hostaway API error during setup: {exc}",
        ) from exc

    # Create coordinators
    listings_coordinator = HostawayListingsCoordinator(hass, entry, api_client)
    reservations_coordinator = HostawayReservationsCoordinator(hass, entry, api_client)

    # Perform initial data fetch
    await listings_coordinator.async_config_entry_first_refresh()
    await reservations_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "token_manager": token_manager,
        "api_client": api_client,
        "listings_coordinator": listings_coordinator,
        "reservations_coordinator": reservations_coordinator,
    }

    # Register services (idempotent, safe for multi-entry)
    from custom_components.hostaway.services import (
        async_setup_services,
    )

    async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a Hostaway config entry.

    Removes runtime data and unloads platforms.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to unload.

    Returns:
        True if unload succeeded.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and DOMAIN in hass.data:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            await data["listings_coordinator"].async_shutdown()
            await data["reservations_coordinator"].async_shutdown()

        # Remove services when no entries remain
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, "set_door_code")
            hass.services.async_remove(DOMAIN, "get_reservations")
            hass.services.async_remove(DOMAIN, "find_reservation")
            hass.services.async_remove(DOMAIN, "create_task")
            hass.services.async_remove(DOMAIN, "update_task")
            hass.services.async_remove(DOMAIN, "delete_task")
            hass.services.async_remove(DOMAIN, "get_tasks")
            hass.services.async_remove(DOMAIN, "get_users")
            hass.services.async_remove(DOMAIN, "get_groups")

    return unload_ok
