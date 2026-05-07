# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Hostaway listings and reservations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.const import DOMAIN
from custom_components.hostaway.entity import HostawayEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
        HostawayReservationsCoordinator,
    )

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HostawayListingSensorDescription(SensorEntityDescription):
    """Describe a Hostaway listing sensor with a value function.

    Attributes:
        value_fn: Callable extracting the sensor value from a listing.
    """

    value_fn: Callable[[HostawayListing], StateType]


LISTING_SENSOR_DESCRIPTIONS: tuple[HostawayListingSensorDescription, ...] = (
    HostawayListingSensorDescription(
        key="status",
        translation_key="listing_status",
        value_fn=lambda listing: listing.status,
    ),
    HostawayListingSensorDescription(
        key="base_price",
        translation_key="listing_base_price",
        value_fn=lambda listing: listing.base_price,
    ),
    HostawayListingSensorDescription(
        key="bedrooms",
        translation_key="listing_bedrooms",
        value_fn=lambda listing: listing.bedrooms,
    ),
    HostawayListingSensorDescription(
        key="bathrooms",
        translation_key="listing_bathrooms",
        value_fn=lambda listing: listing.bathrooms,
    ),
    HostawayListingSensorDescription(
        key="max_guests",
        translation_key="listing_max_guests",
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
        self._attr_unique_id = f"{entry.entry_id}_{listing_id}_{description.key}"

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


class HostawayReservationSensor(
    CoordinatorEntity["HostawayReservationsCoordinator"],
    SensorEntity,
):
    """Sensor entity for a Hostaway reservation.

    State is the guest name. Extra state attributes provide
    check-in/out, status, door code info, and guest count.

    Attributes:
        _reservation: The reservation this sensor represents.
        _listings_coordinator: Listings coordinator for device info.
        _entry: The config entry.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HostawayReservationsCoordinator,
        listings_coordinator: HostawayListingsCoordinator,
        reservation: HostawayReservation,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the reservation sensor.

        Args:
            coordinator: The reservations coordinator.
            listings_coordinator: Listings coordinator for device info.
            reservation: The reservation data.
            entry: The config entry.
        """
        super().__init__(coordinator)
        self._reservation = reservation
        self._listings_coordinator = listings_coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{reservation.id}"

    @property
    def _current_reservation(self) -> HostawayReservation | None:
        """Find current reservation data from coordinator.

        Returns:
            The updated reservation, or the initial one.
        """
        if self.coordinator.data is None:
            return self._reservation
        for reservations in self.coordinator.data.values():
            for res in reservations:
                if res.id == self._reservation.id:
                    return res
        return self._reservation

    @property
    def native_value(self) -> StateType:
        """Return the guest name as sensor state.

        Returns:
            The guest name string.
        """
        res = self._current_reservation
        return res.guest_name if res else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return reservation details as extra attributes.

        Returns:
            Dictionary with reservation attributes.
        """
        res = self._current_reservation
        if res is None:
            return {}
        return {
            "check_in": res.check_in,
            "check_out": res.check_out,
            "status": res.status,
            "door_code": res.door_code,
            "door_code_vendor": res.door_code_vendor,
            "door_code_instruction": res.door_code_instruction,
            "num_guests": res.num_guests,
        }

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info from the listings coordinator.

        Returns:
            DeviceInfo for the associated listing.
        """
        listing_id = self._reservation.listing_id
        if self._listings_coordinator.data is None:
            return None
        listing = self._listings_coordinator.data.get(listing_id)
        if listing is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, str(listing.id))},
            name=listing.name,
            manufacturer="Hostaway",
            model=listing.property_type or "Listing",
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hostaway sensor entities from a config entry.

    Creates listing sensors per listing per description and
    reservation sensors per reservation.

    Args:
        hass: Home Assistant instance.
        entry: The config entry.
        async_add_entities: Callback to add entities.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    listings_coordinator: HostawayListingsCoordinator = data["listings_coordinator"]
    reservations_coordinator: HostawayReservationsCoordinator = data[
        "reservations_coordinator"
    ]

    entities: list[SensorEntity] = []

    # Create listing sensors
    if listings_coordinator.data:
        for listing_id in listings_coordinator.data:
            for description in LISTING_SENSOR_DESCRIPTIONS:
                entities.append(
                    HostawayListingSensor(
                        listings_coordinator,
                        listing_id,
                        entry,
                        description,
                    )
                )

    # Create reservation sensors
    if reservations_coordinator.data:
        for _listing_id, reservations in reservations_coordinator.data.items():
            for reservation in reservations:
                entities.append(
                    HostawayReservationSensor(
                        reservations_coordinator,
                        listings_coordinator,
                        reservation,
                        entry,
                    )
                )

    async_add_entities(entities)
