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
    LISTING_OBJECT_TYPE,
    RESERVATION_OBJECT_TYPE,
    CustomFieldWriteGenerationRegistry,
    HostawayCustomFieldDefinition,
    fetch_custom_field_definitions,
    lookup_definition_by_id,
    resolve_var_name,
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
        except Exception as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise UpdateFailed(
                f"Unexpected error fetching custom field definitions: {exc}",
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

    def require_definition(
        self, custom_field_id: int, object_type: str
    ) -> HostawayCustomFieldDefinition:
        """Return a cached definition or raise a definition error."""
        return lookup_definition_by_id(self.data or [], custom_field_id, object_type)

    def resolve_var_name(
        self, var_name: str, object_type: str
    ) -> HostawayCustomFieldDefinition:
        """Resolve a varName against cached definitions for one object type."""
        return resolve_var_name(self.data or [], var_name, object_type)


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
        self._hass = hass
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
        generations = _write_generations(self._hass, self.config_entry.entry_id)
        captured = _capture_generations(
            generations,
            {(LISTING_OBJECT_TYPE, listing_id) for listing_id in selected},
        )
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
        result = {listing.id: listing for listing in listings if listing.id in selected}
        current = self.data or {}
        for listing_id in selected:
            key = (LISTING_OBJECT_TYPE, listing_id)
            if (
                _generation_changed(generations, key, captured)
                and listing_id in current
            ):
                result[listing_id] = current[listing_id]
        return result


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
        self._hass = hass
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
        current_at_start = self.data or {}
        current_by_id = {
            reservation.id: reservation
            for reservations in current_at_start.values()
            for reservation in reservations
        }
        generations = _write_generations(self._hass, self.config_entry.entry_id)
        captured = _capture_generations(
            generations,
            {
                (RESERVATION_OBJECT_TYPE, reservation_id)
                for reservation_id in current_by_id
            },
        )
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
        latest = self.data or {}
        latest_by_id = {
            reservation.id: reservation
            for reservations_for_listing in latest.values()
            for reservation in reservations_for_listing
        }
        return {
            listing_id: _preserve_changed_reservations(
                listing_id,
                reservations,
                latest_by_id,
                generations,
                captured,
            )
            for listing_id, reservations in result.items()
        }


def _write_generations(
    hass: HomeAssistant,
    entry_id: str,
) -> CustomFieldWriteGenerationRegistry | None:
    """Return the write-generation registry for one config entry."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return None
    registry = entry_data.get("custom_field_write_generations")
    if isinstance(registry, CustomFieldWriteGenerationRegistry):
        return registry
    return None


def _capture_generations(
    registry: CustomFieldWriteGenerationRegistry | None,
    keys: set[tuple[str, int]],
) -> dict[tuple[str, int], int]:
    """Return generation values captured before a coordinator fetch."""
    if registry is None:
        return {}
    return {key: registry.current(*key) for key in keys}


def _generation_changed(
    registry: CustomFieldWriteGenerationRegistry | None,
    key: tuple[str, int],
    captured: dict[tuple[str, int], int],
) -> bool:
    """Return whether a target changed during an in-flight refresh."""
    if registry is None:
        return False
    return registry.current(*key) != captured.get(key, 0)


def _preserve_changed_reservations(
    listing_id: int,
    fetched: list[HostawayReservation],
    current_by_id: dict[int, HostawayReservation],
    generations: CustomFieldWriteGenerationRegistry | None,
    captured: dict[tuple[str, int], int],
) -> list[HostawayReservation]:
    """Preserve reservations patched by writes during an in-flight refresh."""
    result: list[HostawayReservation] = []
    seen: set[int] = set()
    for reservation in fetched:
        key = (RESERVATION_OBJECT_TYPE, reservation.id)
        if _generation_changed(generations, key, captured):
            result.append(current_by_id.get(reservation.id, reservation))
        else:
            result.append(reservation)
        seen.add(reservation.id)
    for reservation_id, reservation in current_by_id.items():
        key = (RESERVATION_OBJECT_TYPE, reservation_id)
        if (
            reservation.listing_id == listing_id
            and reservation_id not in seen
            and _generation_changed(generations, key, captured)
        ):
            result.append(reservation)
    return sorted(result, key=lambda reservation: reservation.check_in)
