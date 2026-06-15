<!-- markdownlint-disable MD013 MD040 MD060 -->

# Implementation Plan: Services Package Refactor

**Branch**: `005-services-package-refactor` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/005-services-package-refactor/spec.md`

## Summary

Refactor the monolithic `custom_components/hostaway/services.py` (1129 lines) into a `services/` package with focused sub-modules organized by responsibility. The package will use a table-driven registration approach to reduce `async_setup_services()` from ~136 lines to under 30 lines. The refactor must be transparent — all 66 existing service tests must pass without behavioral changes.

## Technical Context

**Language/Version**: Python 3.14.2+ (per `requires-python` in pyproject.toml)
**Primary Dependencies**: homeassistant ≥ 2026.5.4, voluptuous, httpx 0.28.1
**Storage**: N/A (Home Assistant config entries; no direct storage)
**Testing**: pytest + pytest-asyncio + pytest-homeassistant-custom-component
**Target Platform**: Home Assistant custom component (Linux/any HA host)
**Project Type**: Home Assistant custom integration (plugin)
**Performance Goals**: No event loop blocking; zero overhead vs. current implementation
**Constraints**: File size limits per FR-015; `async_setup_services()` ≤ 30 lines (FR-016)
**Scale/Scope**: 9 services, 1129 LOC source → 6 files in package, ~66 test methods unchanged

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality & Testing | ✅ PASS | Refactor only — existing tests validate equivalence. TDD cycle: existing tests serve as the "red" suite for the new structure. |
| II. API Client Design | ✅ N/A | No changes to API client layer. |
| III. Atomic Commit Discipline | ✅ PLAN | Implementation will use atomic commits per module extraction. |
| IV. Licensing & Attribution | ✅ PLAN | Every new file gets SPDX header + aislop comment (FR-014). |
| V. Pre-Commit Integrity | ✅ PLAN | All hooks must pass; ruff, mypy, interrogate, reuse-tool. |
| VI. Agent Co-Authorship & DCO | ✅ PLAN | Commits include Co-authored-by and DCO sign-off. |
| VII. User Experience Consistency | ✅ N/A | No user-facing changes (transparent refactor). |
| VIII. Performance Requirements | ✅ N/A | No async/performance changes; import-time only. |
| IX. Phased Development | ✅ PLAN | Single phase — pure structural refactor with no new functionality. |
| X. Security & Credential Management | ✅ N/A | No credential handling changes. |

**Gate Result**: ✅ PASS — No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/005-services-package-refactor/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── __init__.py                    # Updated: import path unchanged (package __init__)
├── services/                      # NEW: package directory
│   ├── __init__.py                # Table-driven registration (< 80 lines)
│   ├── schemas.py                 # All schemas + validators (< 200 lines)
│   ├── helpers.py                 # Shared utilities (< 200 lines)
│   ├── reservation_handlers.py   # set_door_code, get_reservations, find_reservation (< 400 lines)
│   ├── task_handlers.py          # create/update/delete/get tasks (< 400 lines)
│   └── lookup_handlers.py        # get_users, get_groups (< 150 lines)
├── services.yaml                  # UNCHANGED
└── ... (other existing files)

tests/
└── test_services.py               # UNCHANGED (or minimal import path updates)
```

**Structure Decision**: The existing flat `services.py` becomes a `services/` package directory. This is the natural Python mechanism for splitting a large module while preserving its import interface. The integration's `__init__.py` already uses `from custom_components.hostaway.services import async_setup_services` which works identically for both `services.py` and `services/__init__.py`.

## Complexity Tracking

> No violations to justify. This is a pure structural refactor with no new abstractions.
