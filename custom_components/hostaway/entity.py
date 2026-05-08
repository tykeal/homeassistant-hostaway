# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Base entity for Hostaway integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.const import DOMAIN

if TYPE_CHECKING:
    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
    )


def build_device_info(
    listing: HostawayListing,
    entry_unique_id: str | None,
) -> DeviceInfo:
    """Build DeviceInfo for a Hostaway listing.

    Args:
        listing: The listing to build device info for.
        entry_unique_id: The config entry unique_id for scoping.

    Returns:
        DeviceInfo with identifiers, name, manufacturer, model.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_unique_id}_{listing.id}")},
        name=listing.internal_name or listing.name,
        manufacturer="Hostaway",
        model=listing.property_type or "Listing",
    )


class HostawayEntity(
    CoordinatorEntity["HostawayListingsCoordinator"],
):
    """Base class for Hostaway entities tied to a listing.

    Provides ``device_info`` derived from the listing in the
    coordinator's data dict and sets ``_attr_has_entity_name``
    so entity names derive from the device name.

    Attributes:
        _listing_id: The Hostaway listing ID this entity belongs to.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HostawayListingsCoordinator,
        listing_id: int,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the base entity.

        Args:
            coordinator: The listings coordinator.
            listing_id: The Hostaway listing ID.
            entry: The config entry for unique device identifiers.
        """
        super().__init__(coordinator)
        self._listing_id = listing_id
        self._entry_unique_id = entry.unique_id

    @property
    def _listing(self) -> HostawayListing | None:
        """Return the current listing from coordinator data.

        Returns:
            The HostawayListing, or None if not found.
        """
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._listing_id)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for the listing.

        Returns:
            DeviceInfo with identifiers, name, manufacturer, model.
        """
        listing = self._listing
        if listing is None:
            return None
        return build_device_info(listing, self._entry_unique_id)

    @property
    def available(self) -> bool:
        """Return True only when listing is present in coordinator.

        Does not gate on last_update_success so stale-but-valid
        data remains available during transient API failures (FR-016).

        Returns:
            True when the listing is available, False otherwise.
        """
        if self.coordinator.data is None:
            return False
        return self._listing_id in self.coordinator.data
