# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Hostaway listings and reservations."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.hostaway.api.custom_fields import (
    HostawayCustomFieldDefinition,
    fetch_custom_field_definitions,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
)
from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import (
    CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.hostaway.api.client import HostawayApiClient

_LOGGER = logging.getLogger(__name__)


class HostawayCustomFieldsCoordinator(
    DataUpdateCoordinator[list[HostawayCustomFieldDefinition]],
):
    """Coordinator that fetches Hostaway custom-field definitions."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the custom-field definitions coordinator."""
        self.api_client = api_client
        self.last_refresh_succeeded = False
        self.last_refresh_error: Exception | None = None
        interval_minutes = entry.options.get(
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
            DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_custom_fields_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )
        self.data = []

    async def _async_update_data(self) -> list[HostawayCustomFieldDefinition]:
        """Fetch and cache custom-field definitions."""
        try:
            definitions = await fetch_custom_field_definitions(self.api_client._request)
        except HostawayAuthError as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise UpdateFailed(
                f"Failed to fetch custom field definitions: {exc}",
            ) from exc
        self.last_refresh_succeeded = True
        self.last_refresh_error = None
        return definitions

    async def async_refresh_retaining_stale(self) -> None:
        """Refresh definitions without clearing stale cached data on failure."""
        stale = self.data
        await self.async_refresh()
        if not self.last_refresh_succeeded and stale is not None:
            self.data = stale

    @property
    def by_id(self) -> dict[int, HostawayCustomFieldDefinition]:
        """Return cached definitions keyed by id."""
        return {
            definition.custom_field_id: definition for definition in self.data or []
        }

    def get_definitions_for_object_type(
        self, object_type: str
    ) -> list[HostawayCustomFieldDefinition]:
        """Return cached definitions for one Hostaway object type."""
        return [
            definition
            for definition in self.data or []
            if definition.object_type == object_type
        ]

    def get_definition(
        self, custom_field_id: int, object_type: str
    ) -> HostawayCustomFieldDefinition | None:
        """Return a cached definition by id and object type."""
        for definition in self.get_definitions_for_object_type(object_type):
            if definition.custom_field_id == custom_field_id:
                return definition
        return None


class HostawayListingsCoordinator(
    DataUpdateCoordinator[dict[int, HostawayListing]],
):
    """Coordinator that fetches Hostaway listings periodically.

    Wraps the API client's ``get_all_listings()`` call inside a
    ``DataUpdateCoordinator`` so sensor entities receive automatic
    updates. Only listings in ``CONF_SELECTED_LISTINGS`` are kept.

    Attributes:
        api_client: The Hostaway API client instance.
        config_entry: The integration config entry.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the listings coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Hostaway API client for fetching listings.
        """
        self.api_client = api_client
        interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_listings_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[int, HostawayListing]:
        """Fetch listings filtered by selected IDs.

        Returns:
            Dictionary mapping listing ID to HostawayListing.

        Raises:
            UpdateFailed: On any Hostaway API error.
            ConfigEntryAuthFailed: On authentication failure.
        """
        selected = set(self.config_entry.data.get(CONF_SELECTED_LISTINGS, []))
        try:
            listings = await self.api_client.get_all_listings()
        except HostawayAuthError as exc:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise UpdateFailed(
                f"Failed to fetch listings: {exc}",
            ) from exc
        return {listing.id: listing for listing in listings if listing.id in selected}


class HostawayReservationsCoordinator(
    DataUpdateCoordinator[dict[int, list[HostawayReservation]]],
):
    """Coordinator that fetches Hostaway reservations periodically.

    Fetches reservations per selected listing and sorts each
    listing's reservations by check-in date.

    Attributes:
        api_client: The Hostaway API client instance.
        config_entry: The integration config entry.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the reservations coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Hostaway API client for fetching reservations.
        """
        self.api_client = api_client
        interval_minutes = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_reservations_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[int, list[HostawayReservation]]:
        """Fetch reservations for each selected listing sequentially.

        Sequential fetching respects Hostaway API rate limits
        (FR-005) as each call may involve pagination.

        Returns:
            Dictionary mapping listing ID to sorted reservation list.

        Raises:
            UpdateFailed: On any Hostaway API error.
            ConfigEntryAuthFailed: On authentication failure.
        """
        selected = self.config_entry.data.get(CONF_SELECTED_LISTINGS, [])
        result: dict[int, list[HostawayReservation]] = {}
        try:
            for listing_id in selected:
                reservations = await self.api_client.get_all_reservations(listing_id)
                result[listing_id] = sorted(reservations, key=lambda r: r.check_in)
        except HostawayAuthError as exc:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise UpdateFailed(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        return result
