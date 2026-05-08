# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Hostaway listings and reservations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
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
from custom_components.hostaway.entity import HostawayEntity, build_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
        HostawayReservationsCoordinator,
    )

_LOGGER = logging.getLogger(__name__)

# Statuses already warned about to avoid log spam.
_warned_statuses: set[str] = set()

# Priority for selecting the most relevant reservation.
# Lower number = higher priority.
_STATUS_PRIORITY: dict[str, int] = {
    "checked_in": 0,
    "confirmed": 1,
    "new": 1,
    "modified": 1,
    "pending": 2,
    "unconfirmed": 2,
    "awaitingPayment": 3,
    "awaitingGuestVerification": 3,
    "ownerStay": 4,
    "checked_out": 5,
    "cancelled": 6,
    "declined": 7,
    "expired": 7,
    "inquiry": 8,
    "inquiryPreapproved": 8,
    "inquiryDenied": 9,
    "inquiryTimedout": 9,
    "inquiryNotPossible": 9,
    "unknown": 10,
}

# Map raw API statuses to user-friendly derived states.
_STATUS_TO_DERIVED: dict[str, str] = {
    "checked_in": "checked_in",
    "confirmed": "awaiting_checkin",
    "new": "awaiting_checkin",
    "modified": "awaiting_checkin",
    "pending": "pending_approval",
    "unconfirmed": "pending_approval",
    "awaitingPayment": "awaiting_guest",
    "awaitingGuestVerification": "awaiting_guest",
    "ownerStay": "owner_stay",
    "checked_out": "checked_out",
    "cancelled": "cancelled",
    "declined": "cancelled",
    "expired": "cancelled",
    "inquiry": "inquiry",
    "inquiryPreapproved": "inquiry",
    "inquiryDenied": "inquiry",
    "inquiryTimedout": "inquiry",
    "inquiryNotPossible": "inquiry",
    "unknown": "unknown",
}


def _select_reservation(
    reservations: list[HostawayReservation],
) -> HostawayReservation | None:
    """Select the highest-priority reservation.

    Uses ``_STATUS_PRIORITY`` to rank reservations. Unknown
    statuses sort after all known statuses.

    Args:
        reservations: List of reservations for a listing.

    Returns:
        The highest-priority reservation, or None if empty.
    """
    if not reservations:
        return None
    fallback = max(_STATUS_PRIORITY.values()) + 1
    return min(
        reservations,
        key=lambda r: _STATUS_PRIORITY.get(r.status, fallback),
    )


def _derive_state(
    reservation: HostawayReservation | None,
) -> str:
    """Derive the sensor state from a reservation.

    Maps raw API statuses to user-friendly derived states
    using ``_STATUS_TO_DERIVED``. Returns ``no_reservation``
    when no reservation is selected, and ``unknown`` for
    unrecognised statuses (with a warning log).

    Args:
        reservation: The selected reservation, or None.

    Returns:
        The derived state string.
    """
    if reservation is None:
        return "no_reservation"
    derived = _STATUS_TO_DERIVED.get(reservation.status)
    if derived is not None:
        return derived
    if reservation.status not in _warned_statuses:
        _warned_statuses.add(reservation.status)
        _LOGGER.warning(
            "Unknown Hostaway reservation status '%s'; reporting as 'unknown'",
            reservation.status,
        )
    return "unknown"


def _build_reservation_attributes(
    reservation: HostawayReservation | None,
    all_reservations: list[HostawayReservation],
    listing_id: int,
) -> dict[str, Any]:
    """Build extra_state_attributes for the reservation sensor.

    Includes the selected reservation's details and an
    ``upcoming_reservations`` list. The coordinator already
    sorts reservations by check_in, so order is preserved.

    Args:
        reservation: The selected reservation, or None.
        all_reservations: All reservations for the listing
            (pre-sorted by check_in from coordinator).
        listing_id: The listing ID.

    Returns:
        Dictionary of extra state attributes per FR-R04.
    """
    upcoming = [
        {
            "id": r.id,
            "guest_name": r.guest_name,
            "check_in": r.check_in,
            "check_out": r.check_out,
            "status": r.status,
        }
        for r in all_reservations
    ]

    if reservation is None:
        return {
            "reservation_id": None,
            "guest_name": None,
            "check_in": None,
            "check_out": None,
            "status": None,
            "door_code": None,
            "door_code_vendor": None,
            "door_code_instruction": None,
            "num_guests": None,
            "confirmation_code": None,
            "listing_id": listing_id,
            "upcoming_reservations": upcoming,
        }

    return {
        "reservation_id": reservation.id,
        "guest_name": reservation.guest_name,
        "check_in": reservation.check_in,
        "check_out": reservation.check_out,
        "status": reservation.status,
        "door_code": reservation.door_code,
        "door_code_vendor": reservation.door_code_vendor,
        "door_code_instruction": reservation.door_code_instruction,
        "num_guests": reservation.num_guests,
        "confirmation_code": reservation.confirmation_code,
        "listing_id": listing_id,
        "upcoming_reservations": upcoming,
    }


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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return listing_id so users can discover it in the UI.

        Returns:
            Dictionary with the listing_id attribute.
        """
        return {"listing_id": self._listing_id}


class HostawayReservationStatusSensor(
    CoordinatorEntity["HostawayReservationsCoordinator"],
    SensorEntity,
):
    """Per-listing reservation status sensor (FR-R01).

    Selects the highest-priority reservation for a listing
    and exposes its status as the sensor state. Attributes
    include reservation details and an upcoming list.

    Attributes:
        _listing_id: The listing ID this sensor monitors.
        _listings_coordinator: Listings coordinator for device info.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [  # noqa: RUF012
        "checked_in",
        "awaiting_checkin",
        "pending_approval",
        "awaiting_guest",
        "owner_stay",
        "checked_out",
        "cancelled",
        "inquiry",
        "unknown",
        "no_reservation",
    ]

    def __init__(
        self,
        coordinator: HostawayReservationsCoordinator,
        listings_coordinator: HostawayListingsCoordinator,
        listing_id: int,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the reservation status sensor.

        Args:
            coordinator: The reservations coordinator.
            listings_coordinator: Listings coordinator for device info.
            listing_id: The listing ID to monitor.
            entry: The config entry.
        """
        super().__init__(coordinator)
        self._listing_id = listing_id
        self._listings_coordinator = listings_coordinator
        self._entry_unique_id = entry.unique_id
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_reservation_status"
        self._attr_name = "Reservation status"

    @property
    def _reservations(self) -> list[HostawayReservation]:
        """Return reservations for this listing.

        Returns:
            List of reservations, empty if data unavailable.
        """
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get(self._listing_id, [])

    @property
    def available(self) -> bool:
        """Return True when both coordinators have data.

        Checks that the listing still exists in the listings
        coordinator and that the reservations coordinator has
        data. Not gated on a specific reservation existing.

        Returns:
            True when listing and reservation data present.
        """
        if self.coordinator.data is None:
            return False
        if self._listings_coordinator.data is None:
            return False
        return self._listing_id in self._listings_coordinator.data

    @property
    def native_value(self) -> StateType:
        """Return the derived reservation state.

        Returns:
            The reservation status string (FR-R02).
        """
        selected = _select_reservation(self._reservations)
        return _derive_state(selected)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return reservation details and upcoming list.

        Returns:
            Dictionary of attributes per FR-R04.
        """
        reservations = self._reservations
        selected = _select_reservation(reservations)
        return _build_reservation_attributes(selected, reservations, self._listing_id)

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
        return build_device_info(listing, self._entry_unique_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hostaway sensor entities from a config entry.

    Creates listing sensors per listing per description and
    one reservation status sensor per listing. Registers a
    listener to add new entities when listings appear.

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
    known_listing_ids: set[int] = set()

    # Create listing sensors and reservation status sensors
    if listings_coordinator.data:
        for listing_id in listings_coordinator.data:
            known_listing_ids.add(listing_id)
            for description in LISTING_SENSOR_DESCRIPTIONS:
                entities.append(
                    HostawayListingSensor(
                        listings_coordinator,
                        listing_id,
                        entry,
                        description,
                    )
                )
            entities.append(
                HostawayReservationStatusSensor(
                    reservations_coordinator,
                    listings_coordinator,
                    listing_id,
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
            if listing_id not in known_listing_ids:
                known_listing_ids.add(listing_id)
                for description in LISTING_SENSOR_DESCRIPTIONS:
                    new_entities.append(
                        HostawayListingSensor(
                            listings_coordinator,
                            listing_id,
                            entry,
                            description,
                        )
                    )
                new_entities.append(
                    HostawayReservationStatusSensor(
                        reservations_coordinator,
                        listings_coordinator,
                        listing_id,
                        entry,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        listings_coordinator.async_add_listener(_async_add_new_listings)
    )
