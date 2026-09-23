# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway integration for Home Assistant.

Provides OAuth 2.0 authenticated access to the Hostaway API
for property management automation. Manages the API client
lifecycle and token persistence across HA restarts.
"""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.custom_fields import (
    CustomFieldWriteGenerationRegistry,
    CustomFieldWriteLockRegistry,
    CustomFieldWriteSafetyGates,
)
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
    HostawayCustomFieldsCoordinator,
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

    listings_coordinator = HostawayListingsCoordinator(hass, entry, api_client)
    reservations_coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
    custom_fields_coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)

    # Perform initial data fetch
    await listings_coordinator.async_config_entry_first_refresh()
    await reservations_coordinator.async_config_entry_first_refresh()

    def _custom_fields_listener() -> None:
        """Keep the definitions coordinator interval scheduled."""

    custom_fields_update_unsub = custom_fields_coordinator.async_add_listener(
        _custom_fields_listener,
    )

    def _initial_custom_fields_refresh(_now: object) -> None:
        """Start the first definitions refresh without blocking setup."""
        hass.loop.call_soon_threadsafe(
            lambda: hass.async_create_task(
                custom_fields_coordinator.async_refresh_retaining_stale()
            )
        )

    custom_fields_initial_refresh_unsub = async_call_later(
        hass,
        1,
        _initial_custom_fields_refresh,
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "token_manager": token_manager,
        "api_client": api_client,
        "listings_coordinator": listings_coordinator,
        "reservations_coordinator": reservations_coordinator,
        "custom_fields_coordinator": custom_fields_coordinator,
        "custom_fields_update_unsub": custom_fields_update_unsub,
        "custom_fields_initial_refresh_unsub": custom_fields_initial_refresh_unsub,
        "custom_field_write_safety": CustomFieldWriteSafetyGates(),
        "custom_field_write_locks": CustomFieldWriteLockRegistry(),
        "custom_field_write_generations": CustomFieldWriteGenerationRegistry(),
    }

    # Register services (idempotent, safe for multi-entry)
    from custom_components.hostaway.services import async_setup_services

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
            custom_fields_update_unsub = data.get("custom_fields_update_unsub")
            if callable(custom_fields_update_unsub):
                custom_fields_update_unsub()
            custom_fields_initial_refresh_unsub = data.get(
                "custom_fields_initial_refresh_unsub"
            )
            if callable(custom_fields_initial_refresh_unsub):
                custom_fields_initial_refresh_unsub()
            await data["listings_coordinator"].async_shutdown()
            await data["reservations_coordinator"].async_shutdown()
            await data["custom_fields_coordinator"].async_shutdown()

        # Remove services when no entries remain
        if not hass.data.get(DOMAIN):
            from custom_components.hostaway.services import async_unregister_services

            async_unregister_services(hass)

    return unload_ok
