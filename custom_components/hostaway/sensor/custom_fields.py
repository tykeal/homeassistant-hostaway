# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listing custom-field key allocation and dynamic sensors."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import StateType
from homeassistant.util import slugify

from custom_components.hostaway.api.custom_fields import (
    LISTING_OBJECT_TYPE,
    HostawayCustomFieldDefinition,
    definitions_for_object_type,
    validate_identifier,
    var_name_counts,
    var_name_slug_counts,
)
from custom_components.hostaway.entity import HostawayEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.hostaway.coordinator import (
        HostawayCustomFieldsCoordinator,
        HostawayListingsCoordinator,
    )


def _custom_field_unique_id_prefix(entry_unique_id: str | None, listing_id: int) -> str:
    """Return the stable unique-id prefix for listing custom-field sensors."""
    return f"{entry_unique_id}_{listing_id}_custom_field_"


def _parse_persisted_custom_field_unique_id(
    unique_id: str | None,
    entry_unique_id: str | None,
    listing_id: int,
) -> tuple[int, str] | None:
    """Parse a persisted listing custom-field sensor unique id."""
    if unique_id is None:
        return None
    prefix = _custom_field_unique_id_prefix(entry_unique_id, listing_id)
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix) :]
    custom_field_id_text, separator, allocated_key = remainder.partition("_")
    if separator != "_" or not allocated_key:
        return None
    try:
        custom_field_id = int(custom_field_id_text)
    except ValueError:
        return None
    if custom_field_id <= 0:
        return None
    return custom_field_id, allocated_key


def _safe_var_slug(definition: HostawayCustomFieldDefinition | None) -> str | None:
    """Return a slugified varName or None when unavailable."""
    if definition is None:
        return None
    slug = slugify(definition.var_name)
    return slug or None


@dataclass
class ListingCustomFieldKeyAllocation:
    """Per-listing stable custom-field key allocation state."""

    listing_id: int
    field_to_key: dict[int, str] = field(default_factory=dict)
    reserved_keys: set[str] = field(default_factory=set)
    persisted_keys: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_entity_registry(
        cls,
        hass: HomeAssistant,
        entry: ConfigEntry,
        listing_id: int,
        *,
        reserved_keys: Iterable[str] = (),
    ) -> ListingCustomFieldKeyAllocation:
        """Build allocation state seeded from persisted entity unique IDs."""
        allocation = cls(
            listing_id=listing_id,
            reserved_keys=set(reserved_keys),
        )
        registry = er.async_get(hass)
        for entity in registry.entities.values():
            parsed = _parse_persisted_custom_field_unique_id(
                entity.unique_id,
                entry.unique_id,
                listing_id,
            )
            if parsed is None:
                continue
            custom_field_id, allocated_key = parsed
            allocation.persisted_keys[custom_field_id] = allocated_key
            allocation.field_to_key[custom_field_id] = allocated_key
            allocation.reserved_keys.add(allocated_key)
        return allocation

    def clone(self) -> ListingCustomFieldKeyAllocation:
        """Return a mutable copy for non-persistent allocation previews."""
        return ListingCustomFieldKeyAllocation(
            listing_id=self.listing_id,
            field_to_key=dict(self.field_to_key),
            reserved_keys=set(self.reserved_keys),
            persisted_keys=dict(self.persisted_keys),
        )

    def allocate(
        self,
        custom_field_id: int,
        definition: HostawayCustomFieldDefinition | None,
        definitions: Iterable[HostawayCustomFieldDefinition] = (),
    ) -> str:
        """Return a stable key for a field, allocating one when needed."""
        custom_field_id = validate_identifier(custom_field_id, "customFieldId")
        if custom_field_id in self.field_to_key:
            return self.field_to_key[custom_field_id]
        if custom_field_id in self.persisted_keys:
            key = self.persisted_keys[custom_field_id]
            self.field_to_key[custom_field_id] = key
            self.reserved_keys.add(key)
            return key
        key = self._candidate_key(custom_field_id, definition, definitions)
        key = self._reserve_available_key(key, custom_field_id)
        self.field_to_key[custom_field_id] = key
        return key

    def _candidate_key(
        self,
        custom_field_id: int,
        definition: HostawayCustomFieldDefinition | None,
        definitions: Iterable[HostawayCustomFieldDefinition],
    ) -> str:
        """Return the first candidate key for a field."""
        if definition is None:
            return f"custom_field_{custom_field_id}"
        slug = _safe_var_slug(definition)
        if slug is None:
            return f"custom_field_{custom_field_id}"
        listing_definitions = definitions_for_object_type(
            definitions,
            LISTING_OBJECT_TYPE,
        )
        names = var_name_counts(listing_definitions)
        slugs = var_name_slug_counts(listing_definitions, slugify)
        if names[definition.var_name] > 1 or slugs[slug] > 1:
            return f"custom_{slug}_{custom_field_id}"
        return f"custom_{slug}"

    def _reserve_available_key(self, candidate: str, custom_field_id: int) -> str:
        """Reserve and return a key, suffixing deterministic collisions."""
        owner_key = self.field_to_key.get(custom_field_id)
        if owner_key is not None:
            return owner_key
        if candidate not in self.reserved_keys:
            self.reserved_keys.add(candidate)
            return candidate
        base = f"{candidate}_{custom_field_id}"
        if base not in self.reserved_keys:
            self.reserved_keys.add(base)
            return base
        suffix = 2
        while True:
            key = f"{base}_{suffix}"
            if key not in self.reserved_keys:
                self.reserved_keys.add(key)
                return key
            suffix += 1


class HostawayListingCustomFieldSensor(HostawayEntity, SensorEntity):
    """Diagnostic sensor for one Hostaway listing custom-field value."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HostawayListingsCoordinator,
        definitions_coordinator: HostawayCustomFieldsCoordinator | None,
        listing_id: int,
        entry: ConfigEntry,
        custom_field_id: int,
        allocated_key: str,
    ) -> None:
        """Initialize a dynamic listing custom-field sensor."""
        super().__init__(coordinator, listing_id, entry)
        self._definitions_coordinator = definitions_coordinator
        self._custom_field_id = validate_identifier(custom_field_id, "customFieldId")
        self._allocated_key = allocated_key
        self._attr_unique_id = (
            f"{entry.unique_id}_{listing_id}_custom_field_"
            f"{custom_field_id}_{allocated_key}"
        )
        self._attr_name = allocated_key.replace("_", " ").title()
        self._suggested_object_id: str | None = None
        listing = coordinator.data.get(listing_id) if coordinator.data else None
        if listing is not None:
            listing_slug = slugify(listing.internal_name or listing.name)
            self._suggested_object_id = f"hostaway_{listing_slug}_{allocated_key}"

    @property
    def custom_field_id(self) -> int:
        """Return the Hostaway customFieldId represented by this sensor."""
        return self._custom_field_id

    @property
    def allocated_key(self) -> str:
        """Return the stable per-listing custom-field key."""
        return self._allocated_key

    async def async_added_to_hass(self) -> None:
        """Subscribe to definition refreshes for metadata-only updates."""
        await super().async_added_to_hass()
        if self._definitions_coordinator is not None:
            self.async_on_remove(
                self._definitions_coordinator.async_add_listener(
                    self.async_write_ha_state,
                )
            )

    @property
    def suggested_object_id(self) -> str | None:
        """Return the stable suggested object id for this custom-field sensor."""
        return self._suggested_object_id

    @property
    def _definition(self) -> HostawayCustomFieldDefinition | None:
        """Return current definition metadata for this field if available."""
        if self._definitions_coordinator is None:
            return None
        return self._definitions_coordinator.get_definition(
            self._custom_field_id,
            LISTING_OBJECT_TYPE,
        )

    @property
    def native_value(self) -> StateType:
        """Return the current custom-field value for this listing."""
        listing = self._listing
        if listing is None:
            return None
        collection = listing.custom_field_collection
        if collection is None:
            return None
        value = collection.values.get(self._custom_field_id)
        if value is None:
            return None
        result: StateType = value.value
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return resolved or unresolved custom-field metadata."""
        value = self.native_value
        definition = self._definition
        if definition is None:
            return {
                "customFieldId": self._custom_field_id,
                "value": value,
                "resolved": False,
            }
        return {
            "customFieldId": self._custom_field_id,
            "varName": definition.var_name,
            "name": definition.name,
            "type": definition.field_type,
            "possibleValues": list(definition.possible_values),
            "value": value,
            "resolved": True,
        }
