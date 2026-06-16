# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation status sensor entities for the Hostaway integration."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.hostaway.api.models import HostawayReservation
from custom_components.hostaway.const import (
    CONF_FILTER_CANCELLED,
    DEFAULT_FILTER_CANCELLED,
)
from custom_components.hostaway.entity import build_device_info

from .helpers import (
    _CANCELLED_STATUSES,
    _build_reservation_attributes,
    _derive_state,
    _select_reservation,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
        HostawayReservationsCoordinator,
    )


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
        self._entry = entry
        self._entry_unique_id = entry.unique_id
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_reservation_status"
        self._attr_translation_key = "reservation_status"

    @property
    def _filter_cancelled(self) -> bool:
        """Read filter_cancelled from current entry options.

        Returns:
            True when cancelled reservations should be hidden.
        """
        result: bool = self._entry.options.get(
            CONF_FILTER_CANCELLED,
            DEFAULT_FILTER_CANCELLED,
        )
        return result

    @property
    def _reservations(self) -> list[HostawayReservation]:
        """Return reservations for this listing.

        Excludes cancelled/declined/expired reservations when
        ``_filter_cancelled`` is enabled.

        Returns:
            List of reservations, empty if data unavailable.
        """
        if self.coordinator.data is None:
            return []
        all_reservations = self.coordinator.data.get(self._listing_id, [])
        if self._filter_cancelled:
            return [
                reservation
                for reservation in all_reservations
                if reservation.status not in _CANCELLED_STATUSES
            ]
        return all_reservations

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
        return _build_reservation_attributes(
            selected,
            reservations,
            self._listing_id,
        )

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
