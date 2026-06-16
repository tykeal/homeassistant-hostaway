# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listing sensor entities for the Hostaway integration."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.helpers.typing import StateType
from homeassistant.util import slugify

from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.entity import HostawayEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.hostaway.coordinator import HostawayListingsCoordinator


@dataclass(frozen=True, kw_only=True)
class HostawayListingSensorDescription(SensorEntityDescription):
    """Describe a Hostaway listing sensor with a value function.

    Attributes:
        value_fn: Callable extracting the sensor value from a listing.
    """

    value_fn: Callable[[HostawayListing], StateType]


LISTING_SENSOR_DESCRIPTIONS: tuple[HostawayListingSensorDescription, ...] = (
    HostawayListingSensorDescription(
        key="listing_id",
        name="Listing ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.id,
    ),
    HostawayListingSensorDescription(
        key="external_name",
        name="External name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.name,
    ),
    HostawayListingSensorDescription(
        key="status",
        name="Status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.status,
    ),
    HostawayListingSensorDescription(
        key="base_price",
        name="Base price",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.base_price,
    ),
    HostawayListingSensorDescription(
        key="bedrooms",
        name="Bedrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bedrooms,
    ),
    HostawayListingSensorDescription(
        key="bathrooms",
        name="Bathrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bathrooms,
    ),
    HostawayListingSensorDescription(
        key="max_guests",
        name="Max guests",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.max_guests,
    ),
)


class HostawayListingSensor(HostawayEntity, SensorEntity):
    """Sensor entity for a Hostaway listing attribute.

    Uses the description's ``value_fn`` to extract the native
    value from the listing in the coordinator's data dict.

    Attributes:
        entity_description: The sensor entity description.
    """

    entity_description: HostawayListingSensorDescription

    def __init__(
        self,
        coordinator: HostawayListingsCoordinator,
        listing_id: int,
        entry: ConfigEntry,
        description: HostawayListingSensorDescription,
    ) -> None:
        """Initialize the listing sensor.

        Args:
            coordinator: The listings coordinator.
            listing_id: The Hostaway listing ID.
            entry: The config entry.
            description: The sensor entity description.
        """
        super().__init__(coordinator, listing_id, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_{description.key}"
        # FR-007: entity_id = sensor.hostaway_<listing>_<attribute>
        listing = coordinator.data.get(listing_id) if coordinator.data else None
        self._suggested_object_id: str | None = None
        if listing:
            slug = slugify(listing.internal_name or listing.name)
            self._suggested_object_id = f"hostaway_{slug}_{description.key}"

    @property
    def suggested_object_id(self) -> str | None:
        """Return FR-007 compliant object id.

        Returns:
            Object ID in hostaway_<listing>_<attribute> format.
        """
        return self._suggested_object_id

    @property
    def native_value(self) -> StateType:
        """Return the sensor value from the listing.

        Returns:
            The sensor state value, or None if listing unavailable.
        """
        listing = self._listing
        if listing is None:
            return None
        return self.entity_description.value_fn(listing)
