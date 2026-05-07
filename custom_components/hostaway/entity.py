# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Base entity for Hostaway integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.const import DOMAIN

if TYPE_CHECKING:
    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
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
    ) -> None:
        """Initialize the base entity.

        Args:
            coordinator: The listings coordinator.
            listing_id: The Hostaway listing ID.
        """
        super().__init__(coordinator)
        self._listing_id = listing_id

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
        return DeviceInfo(
            identifiers={(DOMAIN, str(listing.id))},
            name=listing.name,
            manufacturer="Hostaway",
            model=listing.property_type or "Listing",
        )

    @property
    def available(self) -> bool:
        """Return True only when listing is present in coordinator.

        Returns:
            True when the listing is available, False otherwise.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        return self._listing_id in self.coordinator.data
