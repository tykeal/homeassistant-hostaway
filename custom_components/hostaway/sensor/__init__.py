# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform setup for Hostaway listings and reservations."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity

from custom_components.hostaway.const import DOMAIN

from .custom_fields import (
    CustomFieldDiscoveryState,
    ListingCustomFieldKeyAllocation,
    extend_custom_field_entities,
)
from .listing import LISTING_SENSOR_DESCRIPTIONS, HostawayListingSensor
from .reservation import HostawayReservationStatusSensor

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.hostaway.coordinator import (
        HostawayListingsCoordinator,
        HostawayReservationsCoordinator,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hostaway sensor entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    listings_coordinator: HostawayListingsCoordinator = data["listings_coordinator"]
    reservations_coordinator: HostawayReservationsCoordinator = data[
        "reservations_coordinator"
    ]
    custom_fields_coordinator = data.get("custom_fields_coordinator")
    entities: list[SensorEntity] = []
    known_listing_ids: set[int] = set()
    known_custom_fields: set[tuple[int, int]] = set()
    allocations: dict[int, ListingCustomFieldKeyAllocation] = data.setdefault(
        "custom_field_key_allocations",
        {},
    )
    custom_field_state = CustomFieldDiscoveryState(
        hass=hass,
        entry=entry,
        listings_coordinator=listings_coordinator,
        custom_fields_coordinator=custom_fields_coordinator,
        allocations=allocations,
        known_custom_fields=known_custom_fields,
        reserved_keys=tuple(
            description.key for description in LISTING_SENSOR_DESCRIPTIONS
        ),
    )

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
                    custom_fields_coordinator,
                )
            )
            extend_custom_field_entities(
                custom_field_state,
                entities,
                listing_id,
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
                        custom_fields_coordinator,
                    )
                )
        for listing_id in listings_coordinator.data:
            extend_custom_field_entities(
                custom_field_state,
                new_entities,
                listing_id,
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        listings_coordinator.async_add_listener(_async_add_new_listings)
    )
