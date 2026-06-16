<!-- markdownlint-disable MD013 MD040 MD060 -->

# Data Model: Sensor Package Refactor

**Feature**: 006-sensor-package-refactor
**Date**: 2026-06-16

## Entities

### HostawayListingSensorDescription (dataclass)

A frozen dataclass extending `SensorEntityDescription` with a value extraction
callable. Used to declaratively define each listing attribute sensor.

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `key` | `str` | Sensor key identifier (inherited) | Non-empty, unique in tuple |
| `name` | `str` | Human-readable name (inherited) | Non-empty |
| `entity_category` | `EntityCategory` | HA entity category (inherited) | All are `DIAGNOSTIC` |
| `value_fn` | `Callable[[HostawayListing], StateType]` | Extracts sensor value from listing | Must be callable |

**Location**: `sensor/listing.py`

**Usage**:

```python
LISTING_SENSOR_DESCRIPTIONS: tuple[HostawayListingSensorDescription, ...] = (
    HostawayListingSensorDescription(
        key="listing_id",
        name="Listing ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.id,
    ),
    # ... 6 more entries
)
```

---

### LISTING_SENSOR_DESCRIPTIONS (tuple)

The complete sensor description table containing all 7 listing attribute sensors:

| Key | Name | Value Source |
|-----|------|-------------|
| `listing_id` | Listing ID | `listing.id` |
| `external_name` | External name | `listing.name` |
| `status` | Status | `listing.status` |
| `base_price` | Base price | `listing.base_price` |
| `bedrooms` | Bedrooms | `listing.bedrooms` |
| `bathrooms` | Bathrooms | `listing.bathrooms` |
| `max_guests` | Max guests | `listing.max_guests` |

**Location**: `sensor/listing.py`

---

### HostawayListingSensor (entity class)

Sensor entity for a Hostaway listing attribute. Extends `HostawayEntity` and
`SensorEntity`. Uses the description's `value_fn` to extract the native value
from the listing in the coordinator's data dict.

| Attribute | Type | Description |
|-----------|------|-------------|
| `entity_description` | `HostawayListingSensorDescription` | The sensor description |
| `_attr_unique_id` | `str` | `{entry_unique_id}_{listing_id}_{description_key}` |
| `_suggested_object_id` | `str \| None` | Suggested object ID format: `hostaway_{slug}_{key}` |

**Location**: `sensor/listing.py`

---

### HostawayReservationStatusSensor (entity class)

Per-listing reservation status sensor. Extends
`CoordinatorEntity[HostawayReservationsCoordinator]` and `SensorEntity`.
Selects the highest-priority reservation for a listing and exposes its derived
status as the sensor state.

| Attribute | Type | Description |
|-----------|------|-------------|
| `_listing_id` | `int` | The listing ID this sensor monitors |
| `_listings_coordinator` | `HostawayListingsCoordinator` | For device info lookups |
| `_entry` | `ConfigEntry` | Config entry reference |
| `_entry_unique_id` | `str \| None` | Cached entry unique_id |
| `_attr_unique_id` | `str` | `{entry_unique_id}_{listing_id}_reservation_status` |
| `_attr_translation_key` | `str` | `"reservation_status"` |
| `_attr_has_entity_name` | `bool` | `True` |
| `_attr_device_class` | `SensorDeviceClass` | `SensorDeviceClass.ENUM` |
| `_attr_options` | `list[str]` | 10 derived state values |

**Location**: `sensor/reservation.py`

**Enum Options** (device_class=ENUM):

- `checked_in`, `awaiting_checkin`, `pending_approval`, `awaiting_guest`,
  `owner_stay`, `checked_out`, `cancelled`, `inquiry`, `unknown`,
  `no_reservation`

---

### Status Maps (module-level constants in helpers.py)

#### _STATUS_PRIORITY

Maps raw API status strings to integer priority values. Lower = higher priority.

| Status | Priority | Status | Priority |
|--------|----------|--------|----------|
| `checked_in` | 0 | `ownerStay` | 4 |
| `confirmed` | 1 | `checked_out` | 5 |
| `new` | 1 | `cancelled` | 6 |
| `modified` | 1 | `declined` | 7 |
| `pending` | 2 | `expired` | 7 |
| `unconfirmed` | 2 | `inquiry` | 8 |
| `awaitingPayment` | 3 | `inquiryPreapproved` | 8 |
| `awaitingGuestVerification` | 3 | `inquiryDenied` | 9 |
| | | `inquiryTimedout` | 9 |
| | | `inquiryNotPossible` | 9 |
| | | `unknown` | 10 |

#### _STATUS_TO_DERIVED

Maps raw API statuses to user-friendly derived states (22 entries → 10
distinct derived values).

#### _CANCELLED_STATUSES

`frozenset({"cancelled", "declined", "expired"})` — used for filtering.

---

### Module-Level State (helpers.py)

| Variable | Type | Description |
|----------|------|-------------|
| `_MAX_WARNED_STATUSES` | `int` | Cap at 50 to prevent unbounded growth |
| `_warned_statuses` | `set[str]` | Tracks statuses already warned about |

**Invariant**: Set size never exceeds `_MAX_WARNED_STATUSES`. Only mutated by
`_derive_state()` in the same module.

---

### Helper Functions (helpers.py)

| Function | Signature | Description |
|----------|-----------|-------------|
| `_select_reservation` | `(reservations: list[HostawayReservation]) → HostawayReservation \| None` | Selects highest-priority reservation using `_STATUS_PRIORITY` |
| `_derive_state` | `(reservation: HostawayReservation \| None) → str` | Maps reservation to derived state string; logs unknown statuses |
| `_build_reservation_attributes` | `(reservation, all_reservations, listing_id) → dict[str, Any]` | Builds extra_state_attributes for reservation sensor |

---

## Relationships

```
sensor/__init__.py
  │
  ├── imports entity classes from:
  │     ├── listing.py (HostawayListingSensor, LISTING_SENSOR_DESCRIPTIONS)
  │     └── reservation.py (HostawayReservationStatusSensor)
  │
  └── imports from external (coordinator, const, DOMAIN)

sensor/listing.py
  └── depends on: entity.py (HostawayEntity, build_device_info), api.models, const

sensor/reservation.py
  ├── depends on: helpers.py (_select_reservation, _derive_state,
  │                           _build_reservation_attributes, _CANCELLED_STATUSES)
  └── depends on: entity.py (build_device_info), api.models, const

sensor/helpers.py
  └── depends on: api.models (HostawayReservation), const (CONF_FILTER_CANCELLED)
```

---

## State Transitions

No new state machines. The existing `_warned_statuses` module-level set
behavior is preserved identically — it simply moves from `sensor.py` module
scope to `helpers.py` module scope. Python's module import system ensures a
single instance regardless of how many files import from `helpers`.
