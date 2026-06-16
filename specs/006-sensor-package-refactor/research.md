<!-- markdownlint-disable MD013 MD040 MD060 -->

# Research: Sensor Package Refactor

**Feature**: 006-sensor-package-refactor
**Date**: 2026-06-16
**Status**: Complete

## Research Questions

### RQ-1: Module-to-Package Refactoring (Import Compatibility)

**Decision**: Replace `sensor.py` with `sensor/__init__.py`. The import
statement `from custom_components.hostaway.sensor import async_setup_entry`
works identically for both, as does the HA platform loader resolution of
`custom_components.hostaway.sensor`.

**Rationale**: Python treats `package/__init__.py` as the module-level
interface. The Home Assistant platform loader uses
`importlib.import_module(f"custom_components.{domain}.{platform}")` which
resolves to the `__init__.py` of a package. This is confirmed by the
successful spec 005 services refactor which used the identical approach.

**Key Constraints**:

- `sensor.py` and `sensor/` can coexist temporarily on disk during the
  refactor, but imports continue to resolve to `sensor.py` until it is
  removed.
- `sensor/__init__.py` and the new sub-modules should be created first, then
  `sensor.py` should be replaced so the package path becomes active before
  validating updated imports or patch targets that reference
  `custom_components.hostaway.sensor.helpers`,
  `custom_components.hostaway.sensor.listing`, or
  `custom_components.hostaway.sensor.reservation`.
- Git tracks this as a delete + create (not a rename).

**Alternatives considered**:

- **Keep `sensor.py` as a facade**: Rejected — unnecessarily complex;
  Python's package mechanism handles this natively.
- **Re-export all symbols from `__init__.py`**: Rejected — works but
  defeats the purpose of the split. Tests should reference actual locations.

---

### RQ-2: Optimal Module Split for sensor.py

**Decision**: Split into 4 files based on responsibility:

| File | Content | Est. Lines |
|------|---------|-----------|
| `__init__.py` | `async_setup_entry`, `_async_add_new_listings`, imports | ~80 |
| `listing.py` | `HostawayListingSensorDescription`, `LISTING_SENSOR_DESCRIPTIONS`, `HostawayListingSensor` | ~145 |
| `reservation.py` | `HostawayReservationStatusSensor` | ~140 |
| `helpers.py` | Status maps, `_warned_statuses`, `_select_reservation`, `_derive_state`, `_build_reservation_attributes` | ~165 |

**Rationale**: This split follows the same organizational principle as the
services refactor (spec 005):

- `__init__.py` is the platform entry point (wiring only).
- `listing.py` groups the listing description dataclass and listing sensor
  entity class together — they are tightly coupled and always used together.
- `reservation.py` isolates the reservation sensor which has different
  dependencies (CoordinatorEntity vs HostawayEntity base, references both
  coordinators).
- `helpers.py` contains pure-logic functions and data maps that are shared
  by the reservation sensor. These are independently testable and have no
  entity/HA dependencies.

**Alternatives considered**:

- **Merge helpers into reservation.py**: Rejected — would push
  `reservation.py` over 300 lines and mix data configuration with entity
  class logic. Separate helpers keeps functions independently testable.
- **Split helpers further (maps.py + functions.py)**: Rejected —
  over-engineering for ~165 lines. The maps and functions are tightly coupled
  (functions reference the maps directly).

---

### RQ-3: Test Patch Path Impact

**Decision**: Tests that import from `custom_components.hostaway.sensor` will
need import path updates to reference sub-modules for internal symbols.

**Analysis**:

The current test file imports:

```python
from custom_components.hostaway.sensor import (
    _STATUS_TO_DERIVED,
    LISTING_SENSOR_DESCRIPTIONS,
    HostawayListingSensor,
    HostawayReservationStatusSensor,
    _build_reservation_attributes,
    _derive_state,
    _select_reservation,
)
```

After refactor, these live in different sub-modules:

| Symbol | New Location |
|--------|-------------|
| `_STATUS_TO_DERIVED` | `custom_components.hostaway.sensor.helpers` |
| `LISTING_SENSOR_DESCRIPTIONS` | `custom_components.hostaway.sensor.listing` |
| `HostawayListingSensor` | `custom_components.hostaway.sensor.listing` |
| `HostawayReservationStatusSensor` | `custom_components.hostaway.sensor.reservation` |
| `_build_reservation_attributes` | `custom_components.hostaway.sensor.helpers` |
| `_derive_state` | `custom_components.hostaway.sensor.helpers` |
| `_select_reservation` | `custom_components.hostaway.sensor.helpers` |

The `async_setup_entry` import:

```python
from custom_components.hostaway.sensor import async_setup_entry
```

This continues to work unchanged because `async_setup_entry` is defined in
`sensor/__init__.py`.

**Spec Compliance**: FR-014 explicitly allows "import paths and patch
targets may be updated to reflect the new module structure."

**Alternatives considered**:

- **Re-export all symbols from `sensor/__init__.py`**: Rejected — creates a
  maintenance burden and hides the actual module structure from tests.

---

### RQ-4: Test File Split Strategy

**Decision**: Split `tests/test_sensor.py` (1145 lines, 63 tests) into 3
test modules per FR-013.

**Allocation**:

| Test File | Classes/Tests | Count |
|-----------|--------------|-------|
| `test_listing.py` | `TestListingSensor` (minus setup test) | 12 |
| `test_reservation.py` | `TestSelectReservation` + `TestDeriveState` + `TestBuildReservationAttributes` + `TestReservationStatusSensor` | 50 |
| `test_setup.py` | `test_entity_ids_via_async_setup_entry` (extracted from TestListingSensor) | 1 |
| **Total** | | **63** |

**Rationale**: The test split mirrors the source split:

- `test_listing.py` tests the `HostawayListingSensor` entity behavior.
- `test_reservation.py` tests all reservation-related code — both helpers
  (pure functions) and the `HostawayReservationStatusSensor` entity.
- `test_setup.py` tests the platform setup wiring (`async_setup_entry`
  and entity registration).

The shared helper functions (`_make_entry`, `_make_listing`,
`_make_reservation`) should be placed in `tests/sensor/conftest.py` and
explicitly imported by each split test module that uses them.

**Alternatives considered**:

- **Keep helpers tests in a separate `test_helpers.py`**: Rejected — the spec
  explicitly lists only `test_listing.py`, `test_reservation.py`, and
  `test_setup.py` in FR-013.
- **Put all helper test classes in `test_listing.py`**: Rejected — the helper
  functions are used exclusively by the reservation sensor; they belong with
  reservation tests.

---

### RQ-5: Shared Mutable State (`_warned_statuses`)

**Decision**: The module-level `_warned_statuses` set moves to `helpers.py`
and continues to function correctly.

**Rationale**: Python module-level state is shared within a single process
(single Home Assistant instance). The `_warned_statuses` set is:

1. Defined at module scope in `helpers.py`.
2. Mutated only by `_derive_state()` which is also in `helpers.py`.
3. Read only by `_derive_state()`.

Since the set and its only mutator live in the same module, there is no
cross-module sharing concern. The behavior is identical to the current
`sensor.py` module-level state.

The `_MAX_WARNED_STATUSES` cap (50) prevents unbounded growth, which is
preserved as-is.

**Alternatives considered**:

- **Move to a class variable**: Rejected — changes the API surface and is
  unnecessary for module-scoped state in a single-process runtime.

---

### RQ-6: Dependency Direction Between Sub-Modules

**Decision**: Strict one-way dependency: `__init__.py` → entity modules →
`helpers.py`. No circular imports.

**Import Graph**:

```
sensor/__init__.py
  ├── imports from listing (HostawayListingSensor, HostawayListingSensorDescription, LISTING_SENSOR_DESCRIPTIONS)
  └── imports from reservation (HostawayReservationStatusSensor)

sensor/listing.py
  └── imports from external packages only (HA, api.models, const, entity)

sensor/reservation.py
  └── imports from helpers (_select_reservation, _derive_state, _build_reservation_attributes, _CANCELLED_STATUSES)

sensor/helpers.py
  └── imports from external packages only (logging, api.models, const)
```

No module imports from `__init__.py`, eliminating any circular import risk.

---

### RQ-7: Logger Naming and Patch Targets After Split

**Decision**: Each sub-module uses `logging.getLogger(__name__)` producing
per-module logger names, and tests that monkeypatch `_warned_statuses` must
target `helpers.py`.

**Impact**:

- `custom_components.hostaway.sensor` (current `_LOGGER` in sensor.py) →
  `custom_components.hostaway.sensor.helpers` (where `_derive_state` logs
  unknown statuses)
- `custom_components.hostaway.sensor._warned_statuses` →
  `custom_components.hostaway.sensor.helpers._warned_statuses`

The current tests do not assert on the logger name string. They continue to
use `caplog` unchanged, but monkeypatch targets for `_warned_statuses` must
move to the helper module path.

**Rationale**: Using `__name__` is the Python standard and provides better log
filtering granularity. Updating the monkeypatch path preserves the existing
test behavior without changing assertions.

---

### RQ-8: File Size Feasibility Analysis

**Decision**: All target file sizes are achievable and well within limits.

**Analysis** (based on line counting of current sensor.py sections):

| Target File | Content Lines | Headers/Imports | Total Est. | Limit |
|-------------|--------------|-----------------|-----------|-------|
| `__init__.py` | ~45 (setup logic) | ~30 (imports, headers) | ~75 | ≤ 100 |
| `listing.py` | ~100 (class + descriptions) | ~40 (imports, headers) | ~140 | ≤ 400 |
| `reservation.py` | ~95 (class) | ~40 (imports, headers) | ~135 | ≤ 400 |
| `helpers.py` | ~125 (maps + functions) | ~35 (imports, headers) | ~160 | ≤ 400 |

**Total current**: 555 lines → Estimated total after split: ~510 lines
(slight increase from repeated imports, offset by removing the monolithic
file's combined import block).
