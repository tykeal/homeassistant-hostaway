<!-- markdownlint-disable MD013 MD040 MD060 -->

# Quickstart: Sensor Package Refactor

**Feature**: 006-sensor-package-refactor
**Date**: 2026-06-16

## Overview

This refactor converts the monolithic `custom_components/hostaway/sensor.py`
(555 lines) into a `sensor/` package with focused sub-modules. The change is
purely structural — no behavioral changes. It follows the identical pattern
established by spec 005 (services package refactor).

## Quick Verification

After implementation, run the sensor test suite first:

```bash
uv run pytest tests/sensor/ -v
```

All 63 sensor tests must pass. The only test changes are import path updates
(pointing to sub-modules instead of the monolithic module) and monkeypatch
target updates for `_warned_statuses`.

Run the full repository test suite during final validation:

```bash
uv run pytest --tb=short
```

## Package Layout

```
custom_components/hostaway/sensor/
├── __init__.py      # Platform setup: async_setup_entry, _async_add_new_listings
├── listing.py       # HostawayListingSensorDescription, LISTING_SENSOR_DESCRIPTIONS, HostawayListingSensor
├── reservation.py   # HostawayReservationStatusSensor
└── helpers.py       # Status maps, _warned_statuses, _select_reservation, _derive_state, _build_reservation_attributes
```

## Test Layout

```
tests/sensor/
├── __init__.py          # Empty package marker
├── conftest.py          # Shared helper functions (_make_entry, _make_listing, _make_reservation)
├── test_listing.py      # TestListingSensor (12 tests)
├── test_reservation.py  # TestSelectReservation + TestDeriveState + TestBuildReservationAttributes + TestReservationStatusSensor (50 tests)
└── test_setup.py        # async_setup_entry integration test (1 test)
```

## Adding a New Sensor Type (Post-Refactor)

1. **Create the entity class** in a new or existing sub-module:

   ```python
   class HostawayNewSensor(HostawayEntity, SensorEntity):
       """New sensor entity for Hostaway."""
       # ... implementation
   ```

2. **Import and register** in `sensor/__init__.py`:

   ```python
   from custom_components.hostaway.sensor.new_module import HostawayNewSensor

   # Add entity creation in async_setup_entry
   entities.append(HostawayNewSensor(coordinator, listing_id, entry))
   ```

3. **Add tests** in the appropriate `tests/sensor/test_*.py` module.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `helpers.py` for pure functions | Keeps entity classes focused; helpers are independently testable |
| `_warned_statuses` in `helpers.py` | Same module as its only mutator (`_derive_state`); single instance guaranteed |
| `__init__.py` ≤ 100 lines | Platform wiring only — no business logic |
| No re-exports from `__init__.py` | Tests import from actual sub-modules; cleaner than facade pattern |
| Test helpers in `conftest.py` | Shared helper functions (`_make_entry`, `_make_listing`, `_make_reservation`) avoid duplication without changing test signatures |

## File Size Targets

| File | Target | Limit |
|------|--------|-------|
| `sensor/__init__.py` | ~75 lines | ≤ 100 |
| `sensor/listing.py` | ~140 lines | ≤ 400 |
| `sensor/reservation.py` | ~135 lines | ≤ 400 |
| `sensor/helpers.py` | ~160 lines | ≤ 400 |

## Test Import Migration

Tests that import from `custom_components.hostaway.sensor` must update to
reference the sub-module where each symbol actually lives:

| Symbol | New Import Path |
|--------|----------------|
| `LISTING_SENSOR_DESCRIPTIONS` | `custom_components.hostaway.sensor.listing` |
| `HostawayListingSensor` | `custom_components.hostaway.sensor.listing` |
| `HostawayReservationStatusSensor` | `custom_components.hostaway.sensor.reservation` |
| `_STATUS_TO_DERIVED` | `custom_components.hostaway.sensor.helpers` |
| `_select_reservation` | `custom_components.hostaway.sensor.helpers` |
| `_derive_state` | `custom_components.hostaway.sensor.helpers` |
| `_build_reservation_attributes` | `custom_components.hostaway.sensor.helpers` |
| `async_setup_entry` | `custom_components.hostaway.sensor` (unchanged) |

## Patch Target Update

The `_warned_statuses` module-level set moves from `sensor.py` to
`helpers.py`, so tests that monkeypatch it must update the target path:

- **Old**: `custom_components.hostaway.sensor._warned_statuses`
- **New**: `custom_components.hostaway.sensor.helpers._warned_statuses`

The helper module also uses `logging.getLogger(__name__)`, so any future tests
that assert on logger names should expect
`custom_components.hostaway.sensor.helpers`.

## Shell Commands

```bash
# Run sensor tests only
uv run pytest tests/sensor/ -v

# Run full test suite
uv run pytest --tb=short

# Check file line counts
wc -l custom_components/hostaway/sensor/*.py

# Verify old sensor.py is gone
test ! -f custom_components/hostaway/sensor.py && echo "OK: removed"

# Run full pre-commit
pre-commit run --all-files

# Type check sensor package
uv run mypy custom_components/hostaway/sensor/
```
