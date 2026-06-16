<!-- markdownlint-disable MD013 MD040 MD060 -->

# Implementation Plan: Sensor Package Refactor

**Branch**: `006-sensor-package-refactor` | **Date**: 2026-06-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/006-sensor-package-refactor/spec.md`

## Summary

Refactor the monolithic `custom_components/hostaway/sensor.py` (555 lines) into
a `sensor/` package with focused sub-modules organized by responsibility. The
package will expose `async_setup_entry` from its `__init__.py` so the Home
Assistant platform loader resolves `custom_components.hostaway.sensor` unchanged.
The refactor must be transparent — all 63 sensor tests and the full test suite
must pass without behavioral changes. This follows the identical pattern
established by spec 005 (services package refactor).

## Technical Context

**Language/Version**: Python 3.14.2+ (per `requires-python` in pyproject.toml)
**Primary Dependencies**: homeassistant ≥ 2026.5.4, httpx 0.28.1
**Storage**: N/A (Home Assistant config entries; no direct storage)
**Testing**: pytest + pytest-asyncio + pytest-homeassistant-custom-component
**Target Platform**: Home Assistant custom component (Linux/any HA host)
**Project Type**: Home Assistant custom integration (plugin)
**Performance Goals**: No event loop blocking; zero overhead vs. current implementation
**Constraints**: Each file in `sensor/` package ≤ 400 lines; `__init__.py` ≤ 100 lines (SC-004)
**Scale/Scope**: 555 LOC source → 4 files in package, 63 test methods → 3 test modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality & Testing | ✅ PLAN | Expand the existing setup-flow characterization test first to cover listener registration, new-listing handling, and unload cleanup, then use the full suite for regression validation. |
| II. API Client Design | ✅ N/A | No changes to API client layer. |
| III. Atomic Commit Discipline | ✅ PLAN | Implementation will use atomic commits per module extraction. |
| IV. Licensing & Attribution | ✅ PLAN | Every new file gets SPDX header + aislop comment using `ai-slop/hallucinated-import` (FR-010). |
| V. Pre-Commit Integrity | ✅ PLAN | All hooks must pass; ruff, mypy, interrogate, reuse-tool. |
| VI. Agent Co-Authorship & DCO | ✅ PLAN | Commits include Co-authored-by and DCO sign-off. |
| VII. User Experience Consistency | ✅ N/A | No user-facing changes (transparent refactor). |
| VIII. Performance Requirements | ✅ N/A | No async/performance changes; import-time only. |
| IX. Phased Development | ✅ PLAN | Multi-phase implementation across Setup, Foundational, User Story, and Polish phases in `tasks.md`, with a green checkpoint at the end of each phase before proceeding. |
| X. Security & Credential Management | ✅ N/A | No credential handling changes. |

**Gate Result**: ✅ PASS — No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/006-sensor-package-refactor/
├── spec.md                       # Feature specification
├── plan.md                       # This file
├── research.md                   # Phase 0 output
├── data-model.md                 # Phase 1 output
├── quickstart.md                 # Phase 1 output
├── tasks.md                      # Phase 2 output (/speckit.tasks command)
└── checklists/
    └── requirements.md           # Specification quality checklist
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── __init__.py                    # UNCHANGED (imports from sensor unchanged)
├── sensor/                        # NEW: package directory
│   ├── __init__.py                # Platform setup: async_setup_entry, _async_add_new_listings (< 100 lines)
│   ├── listing.py                 # HostawayListingSensorDescription, LISTING_SENSOR_DESCRIPTIONS, HostawayListingSensor (< 200 lines)
│   ├── reservation.py             # HostawayReservationStatusSensor (< 200 lines)
│   └── helpers.py                 # Status maps, _warned_statuses, helper functions (< 200 lines)
├── services/                      # UNCHANGED
├── services.yaml                  # UNCHANGED
└── ... (other existing files)

tests/
├── sensor/                        # NEW: test package
│   ├── __init__.py                # Empty package marker
│   ├── conftest.py                # Shared fixtures and test helpers
│   ├── test_listing.py            # TestListingSensor (12 tests)
│   ├── test_reservation.py        # TestSelectReservation + TestDeriveState + TestBuildReservationAttributes + TestReservationStatusSensor (50 tests)
│   └── test_setup.py              # async_setup_entry integration test (1 test)
└── ... (other existing test files)
```

**Structure Decision**: The existing flat `sensor.py` becomes a `sensor/` package
directory. This is the natural Python mechanism for splitting a large module while
preserving its import interface. The Home Assistant platform loader uses
`custom_components.hostaway.sensor` to find `async_setup_entry` which works
identically for both `sensor.py` and `sensor/__init__.py`. This follows the
identical pattern used in spec 005 (services package refactor).

## Complexity Tracking

No violations — table intentionally omitted.
