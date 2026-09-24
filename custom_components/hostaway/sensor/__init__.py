# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform setup for Hostaway listings and reservations."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity

from custom_components.hostaway.const import DOMAIN

from .custom_fields import (
    HostawayListingCustomFieldSensor,
    ListingCustomFieldKeyAllocation,
)
from .listing import LISTING_SENSOR_DESCRIPTIONS, HostawayListingSensor
from .reservation import HostawayReservationStatusSensor

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.hostaway.coordinator import (
        HostawayCustomFieldsCoordinator,
        HostawayListingsCoordinator,
        HostawayReservationsCoordinator,
    )


@dataclass(frozen=True)
class _CustomFieldDiscoveryState:
    """Shared state for dynamic listing custom-field discovery."""

    hass: HomeAssistant
    entry: ConfigEntry
    listings_coordinator: HostawayListingsCoordinator
    custom_fields_coordinator: HostawayCustomFieldsCoordinator | None
    allocations: dict[int, ListingCustomFieldKeyAllocation]
    known_custom_fields: set[tuple[int, int]]


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
    custom_field_state = _CustomFieldDiscoveryState(
        hass=hass,
        entry=entry,
        listings_coordinator=listings_coordinator,
        custom_fields_coordinator=custom_fields_coordinator,
        allocations=allocations,
        known_custom_fields=known_custom_fields,
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
            _extend_custom_field_entities(
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
            _extend_custom_field_entities(
                custom_field_state,
                new_entities,
                listing_id,
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        listings_coordinator.async_add_listener(_async_add_new_listings)
    )


def _extend_custom_field_entities(
    state: _CustomFieldDiscoveryState,
    entities: list[SensorEntity],
    listing_id: int,
) -> None:
    """Append missing dynamic custom-field sensors for one listing."""
    listing = (
        state.listings_coordinator.data.get(listing_id)
        if state.listings_coordinator.data
        else None
    )
    if listing is None or listing.custom_field_collection is None:
        return
    allocation = state.allocations.get(listing_id)
    if allocation is None:
        allocation = ListingCustomFieldKeyAllocation.from_entity_registry(
            state.hass,
            state.entry,
            listing_id,
            reserved_keys=(
                description.key for description in LISTING_SENSOR_DESCRIPTIONS
            ),
        )
        state.allocations[listing_id] = allocation
    definitions = getattr(state.custom_fields_coordinator, "data", None) or []
    for custom_field_id in listing.custom_field_collection.values:
        key = (listing_id, custom_field_id)
        if key in state.known_custom_fields:
            continue
        definition = None
        if state.custom_fields_coordinator is not None:
            definition = state.custom_fields_coordinator.get_definition(
                custom_field_id,
                "listing",
            )
        allocated_key = allocation.allocate(custom_field_id, definition, definitions)
        state.known_custom_fields.add(key)
        entities.append(
            HostawayListingCustomFieldSensor(
                state.listings_coordinator,
                state.custom_fields_coordinator,
                listing_id,
                state.entry,
                custom_field_id,
                allocated_key,
            )
        )
