# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Hostaway listings and reservations."""

from __future__ import annotations

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
from homeassistant.util import slugify

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
        name="Status",
        value_fn=lambda listing: listing.status,
    ),
    HostawayListingSensorDescription(
        key="base_price",
        name="Base price",
        value_fn=lambda listing: listing.base_price,
    ),
    HostawayListingSensorDescription(
        key="bedrooms",
        name="Bedrooms",
        value_fn=lambda listing: listing.bedrooms,
    ),
    HostawayListingSensorDescription(
        key="bathrooms",
        name="Bathrooms",
        value_fn=lambda listing: listing.bathrooms,
    ),
    HostawayListingSensorDescription(
        key="max_guests",
        name="Max guests",
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
            self._suggested_object_id = (
                f"hostaway_{slugify(listing.name)}_{description.key}"
            )

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


class HostawayReservationSensor(
    CoordinatorEntity["HostawayReservationsCoordinator"],
    SensorEntity,
):
    """Sensor entity for a Hostaway reservation.

    State is the guest name. Extra state attributes provide
    check-in/out, status, door code info, and guest count.

    Attributes:
        _reservation_id: The reservation ID this sensor tracks.
        _listing_id: The listing ID for device info lookup.
        _listings_coordinator: Listings coordinator for device info.
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
        self._reservation_id = reservation.id
        self._listing_id = reservation.listing_id
        self._listings_coordinator = listings_coordinator
        self._entry_unique_id = entry.unique_id
        self._attr_unique_id = f"{entry.unique_id}_{reservation.id}"
        self._attr_name = f"Reservation {reservation.id}"

    @property
    def _current_reservation(self) -> HostawayReservation | None:
        """Find current reservation data from coordinator.

        Uses listing_id for efficient lookup rather than scanning
        all listings.

        Returns:
            The updated reservation, or None if not found.
        """
        if self.coordinator.data is None:
            return None
        reservations = self.coordinator.data.get(self._listing_id, [])
        for res in reservations:
            if res.id == self._reservation_id:
                return res
        return None

    @property
    def available(self) -> bool:
        """Return True only when reservation exists in coordinator.

        Does not gate on last_update_success so stale-but-valid
        data remains available during transient API failures (FR-016).

        Returns:
            True when the reservation is present, False otherwise.
        """
        return self._current_reservation is not None

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
        if self._listings_coordinator.data is None:
            return None
        listing = self._listings_coordinator.data.get(self._listing_id)
        if listing is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_unique_id}_{listing.id}")},
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
    reservation sensors per reservation. Registers listeners
    to add new entities when coordinator data updates.

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
    known_listing_sensor_ids: set[str] = set()
    known_reservation_ids: set[int] = set()

    # Create listing sensors for current data
    if listings_coordinator.data:
        for listing_id in listings_coordinator.data:
            for description in LISTING_SENSOR_DESCRIPTIONS:
                uid = f"{entry.unique_id}_{listing_id}_{description.key}"
                known_listing_sensor_ids.add(uid)
                entities.append(
                    HostawayListingSensor(
                        listings_coordinator,
                        listing_id,
                        entry,
                        description,
                    )
                )

    # Create reservation sensors for current data
    if reservations_coordinator.data:
        for _listing_id, reservations in reservations_coordinator.data.items():
            for reservation in reservations:
                known_reservation_ids.add(reservation.id)
                entities.append(
                    HostawayReservationSensor(
                        reservations_coordinator,
                        listings_coordinator,
                        reservation,
                        entry,
                    )
                )

    async_add_entities(entities)

    def _async_add_new_listings() -> None:
        """Add sensors for newly discovered listings."""
        if not listings_coordinator.data:
            return
        new_entities: list[SensorEntity] = []
        for listing_id in listings_coordinator.data:
            for description in LISTING_SENSOR_DESCRIPTIONS:
                uid = f"{entry.unique_id}_{listing_id}_{description.key}"
                if uid not in known_listing_sensor_ids:
                    known_listing_sensor_ids.add(uid)
                    new_entities.append(
                        HostawayListingSensor(
                            listings_coordinator,
                            listing_id,
                            entry,
                            description,
                        )
                    )
        if new_entities:
            async_add_entities(new_entities)

    def _async_add_new_reservations() -> None:
        """Add sensors for newly discovered reservations."""
        if not reservations_coordinator.data:
            return
        new_entities: list[SensorEntity] = []
        for _lid, reservations in reservations_coordinator.data.items():
            for reservation in reservations:
                if reservation.id not in known_reservation_ids:
                    known_reservation_ids.add(reservation.id)
                    new_entities.append(
                        HostawayReservationSensor(
                            reservations_coordinator,
                            listings_coordinator,
                            reservation,
                            entry,
                        )
                    )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        listings_coordinator.async_add_listener(_async_add_new_listings)
    )
    entry.async_on_unload(
        reservations_coordinator.async_add_listener(_async_add_new_reservations)
    )
