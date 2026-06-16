<!-- markdownlint-disable MD013 MD040 MD060 -->

# Tasks: Sensor Package Refactor

**Input**: Design documents from `/specs/006-sensor-package-refactor/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, quickstart.md

**Tests**: Expand the existing setup-flow characterization coverage first,
then use the current sensor suite for regression validation. Test file
restructuring is part of User Story 4.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/hostaway/sensor/` (new package)
- **Tests**: `tests/sensor/` (new test package)
- **Old files**: `custom_components/hostaway/sensor.py`, `tests/test_sensor.py`

---

## Phase 1: Setup (Package Scaffolding)

**Purpose**: Create the sensor/ package directory structure and empty modules with proper headers

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

- [x] T001 Create sensor package directory at
  `custom_components/hostaway/sensor/` with a minimal `__init__.py` package
  marker including the required SPDX header, aislop comment, and module
  docstring
- [x] T002 [P] Create minimal module
  `custom_components/hostaway/sensor/helpers.py` with SPDX header, aislop
  comment, and docstring
- [x] T003 [P] Create minimal module
  `custom_components/hostaway/sensor/listing.py` with SPDX header, aislop
  comment, and docstring
- [x] T004 [P] Create minimal module
  `custom_components/hostaway/sensor/reservation.py` with SPDX header,
  aislop comment, and docstring

---

## Phase 2: Foundational (Source Package Population)

**Purpose**: Extract code from monolithic sensor.py into the new sub-modules. MUST complete before test restructuring.

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

**⚠️ CRITICAL**: All user story validation depends on this phase being complete.

- [x] T005 Extract status maps (`_STATUS_PRIORITY`,
  `_STATUS_TO_DERIVED`, `_CANCELLED_STATUSES`), module-level state
  (`_MAX_WARNED_STATUSES`, `_warned_statuses`), and helper functions
  (`_select_reservation`, `_derive_state`,
  `_build_reservation_attributes`) into
  `custom_components/hostaway/sensor/helpers.py` with all required imports
  (`logging`, `api.models`, `const`)
- [x] T006 Extract `HostawayListingSensorDescription`,
  `LISTING_SENSOR_DESCRIPTIONS`, and `HostawayListingSensor` into
  `custom_components/hostaway/sensor/listing.py` with all required imports
  (HA types, entity base, `api.models`, `const`)
- [x] T007 Extract `HostawayReservationStatusSensor` into
  `custom_components/hostaway/sensor/reservation.py` with imports from
  helpers (`_select_reservation`, `_derive_state`,
  `_build_reservation_attributes`, `_CANCELLED_STATUSES`) and external
  dependencies (HA types, entity base, `api.models`, `const`)
- [x] T008 Populate `custom_components/hostaway/sensor/__init__.py` with `async_setup_entry`, `_async_add_new_listings`, and imports from `listing.py` and `reservation.py` (must stay ≤ 100 lines)
- [x] T009 Delete the old monolithic `custom_components/hostaway/sensor.py`
  after T008 so the package layout becomes active, then perform T015 and T016
  in the same atomic change set before running validation

**Checkpoint**: Source package complete —
`custom_components.hostaway.sensor.async_setup_entry` resolves correctly,
the old `sensor.py` has been removed, and Phase 4 can retarget tests to the
new sub-module paths

---

## Phase 3: User Story 1 - Codebase Maintainability (Priority: P1) 🎯 MVP

**Goal**: The monolithic sensor.py is fully replaced by the sensor/ package with all logic distributed across focused sub-modules, each under 400 lines.

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

**Independent Test**: Run `wc -l custom_components/hostaway/sensor/*.py` and
confirm all files are under 400 lines with `__init__.py` under 100 lines.
Verify `custom_components/hostaway/sensor.py` no longer exists and
`from custom_components.hostaway.sensor import async_setup_entry` resolves.

### Implementation for User Story 1

- [x] T010 [US1] Verify `custom_components/hostaway/sensor/__init__.py` is under 100 lines (SC-004)
- [x] T011 [P] [US1] Verify custom_components/hostaway/sensor/helpers.py is under 400 lines (FR-008)
- [x] T012 [P] [US1] Verify custom_components/hostaway/sensor/listing.py is under 400 lines (FR-008)
- [x] T013 [P] [US1] Verify custom_components/hostaway/sensor/reservation.py is under 400 lines (FR-008)
- [x] T014 [US1] Verify old custom_components/hostaway/sensor.py no longer exists (SC-003, FR-009)

**Checkpoint**: Package structure validated — all files within size limits, old file removed

---

## Phase 4: User Story 2 - Transparent Refactor (Priority: P1)

**Goal**: All 317 existing tests pass with zero behavioral changes. The integration loads identically to pre-refactor behavior.

**Independent Test**: Run `uv run pytest --tb=short` — all 317 tests pass. Import `from custom_components.hostaway.sensor import async_setup_entry` succeeds.

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

### Implementation for User Story 2

- [x] T015 [US2] Expand `test_entity_ids_via_async_setup_entry` in
  `tests/test_sensor.py` into a characterization test that covers listener
  registration via `entry.async_on_unload(...)`, new-listing entity creation
  in `_async_add_new_listings`, and unload cleanup behavior before validating
  the refactor
- [x] T016 [US2] Update import paths and patch targets in
  `tests/test_sensor.py` to reference the new sub-module locations and moved
  `_warned_statuses` module state per research.md RQ-3 and RQ-7
- [x] T017 [US2] Run the targeted setup-flow characterization test first, then
  run `uv run pytest --tb=short` and confirm the full suite passes with zero
  failures (SC-001)

**Checkpoint**: Behavioral equivalence proven — all tests pass without logic or assertion changes

---

## Phase 5: User Story 3 - Code Standards Compliance (Priority: P2)

**Goal**: All new files include proper SPDX license headers, aislop ignore comments using `ai-slop/hallucinated-import`, and module docstrings.

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

**Independent Test**: Inspect first lines of each file in sensor/ package for SPDX header (Apache-2.0, 2026 Andrew Grimberg), aislop comment, and docstring.

### Implementation for User Story 3

- [x] T018 [P] [US3] Verify SPDX header, aislop ignore comment with `ai-slop/hallucinated-import` token, and module docstring in `custom_components/hostaway/sensor/__init__.py`
- [x] T019 [P] [US3] Verify SPDX header, aislop ignore comment with ai-slop/hallucinated-import token, and module docstring in custom_components/hostaway/sensor/helpers.py
- [x] T020 [P] [US3] Verify SPDX header, aislop ignore comment with ai-slop/hallucinated-import token, and module docstring in custom_components/hostaway/sensor/listing.py
- [x] T021 [P] [US3] Verify SPDX header, aislop ignore comment with ai-slop/hallucinated-import token, and module docstring in custom_components/hostaway/sensor/reservation.py
- [x] T022 [US3] Verify existing aislop inline suppressions (noqa: RUF012 on _attr_options in HostawayReservationStatusSensor) are preserved in custom_components/hostaway/sensor/reservation.py (FR-011)

**Checkpoint**: All compliance requirements met — headers, comments, and suppressions in place

---

## Phase 6: User Story 4 - Test Organization (Priority: P2)

**Goal**: The sensor tests are split into focused test modules mirroring the source package structure, enabling targeted test execution.

**Phase Exit Rule**: End the phase with its checkpoint green before moving to
the next phase.

**Independent Test**: Run `uv run pytest tests/sensor/ -v` — all 63 sensor tests pass. Verify `tests/test_sensor.py` no longer exists. Verify test count matches: 12 + 50 + 1 = 63.

### Implementation for User Story 4

- [x] T023 [US4] Create test package directory `tests/sensor/` with
  `__init__.py`, including the required SPDX header, aislop comment using
  `ai-slop/hallucinated-import`, and module docstring
- [x] T024 [US4] Create `tests/sensor/conftest.py` with shared helper
  functions (`_make_entry`, `_make_listing`, `_make_reservation`) plus the
  required SPDX header, aislop comment using `ai-slop/hallucinated-import`,
  and module docstring
- [x] T025 [P] [US4] Extract `TestListingSensor` class (12 tests) into
  `tests/sensor/test_listing.py` with updated imports from
  `custom_components.hostaway.sensor.listing`, explicitly importing shared
  helper functions from `tests.sensor.conftest`, and the required SPDX
  header, aislop comment using `ai-slop/hallucinated-import`, and module
  docstring
- [x] T026 [P] [US4] Extract `TestSelectReservation`,
  `TestDeriveState`, `TestBuildReservationAttributes`, and
  `TestReservationStatusSensor` classes (50 tests) into
  `tests/sensor/test_reservation.py` with updated imports from
  `custom_components.hostaway.sensor.helpers`,
  `custom_components.hostaway.sensor.reservation`, and shared helper
  functions from `tests.sensor.conftest`, plus the required SPDX header,
  aislop comment using `ai-slop/hallucinated-import`, and module docstring
- [x] T027 [P] [US4] Extract `test_entity_ids_via_async_setup_entry` (1 test)
  into `tests/sensor/test_setup.py` with import from
  `custom_components.hostaway.sensor` and any needed shared helper functions
  from `tests.sensor.conftest`, plus the required SPDX header, aislop
  comment using `ai-slop/hallucinated-import`, and module docstring
- [x] T028 [US4] Delete old tests/test_sensor.py file
- [x] T029 [US4] Run `uv run pytest tests/sensor/ -v` and confirm 63 tests pass across the new test modules (SC-005)

**Checkpoint**: Test reorganization complete — targeted test execution works, total count preserved

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all user stories and pre-commit compliance

**Phase Exit Rule**: End the phase with its checkpoint green before declaring
the work complete.

- [x] T030 [P] Run `wc -l custom_components/hostaway/sensor/*.py` and confirm all file sizes (SC-002, SC-004)
- [x] T031 [P] Run `pre-commit run --all-files` and fix any issues (ruff, mypy, interrogate, reuse-tool)
- [x] T032 Run full test suite with `uv run pytest --tb=short` confirming all 317 tests pass (final SC-001 validation)
- [x] T033 Verify no new external dependencies introduced (FR-012) by checking pyproject.toml is unchanged

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (package directory must exist) — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Phase 2 (source extraction must be complete)
- **User Story 2 (Phase 4)**: Depends on Phase 2 (package must be functional for tests to reference)
- **User Story 3 (Phase 5)**: Depends on Phase 2 (files must exist to verify headers)
- **User Story 4 (Phase 6)**: Depends on Phase 4 (import paths must be validated first)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 2 — validates package structure
- **User Story 2 (P1)**: Can start after Phase 2 — validates behavioral equivalence
- **User Story 3 (P2)**: Can start after Phase 2 — validates compliance (parallel with US1/US2)
- **User Story 4 (P2)**: Depends on US2 completion — test restructuring requires validated import paths

### Within Each User Story

- Verification tasks can run in parallel (when marked [P])
- File creation before content extraction
- Source extraction before test migration
- Test suite must pass before declaring story complete

### Parallel Opportunities

- Phase 1: T002, T003, T004 can all run in parallel (independent empty files)
- Phase 3: T011, T012, T013 can run in parallel (independent size checks)
- Phase 5: T018, T019, T020, T021 can run in parallel (independent header checks)
- Phase 6: T025, T026, T027 can run in parallel (independent test file extractions)
- Phase 7: T030, T031 can run in parallel (independent validation checks)
- User Stories 1, 2, 3 can begin in parallel after Phase 2 completes

---

## Parallel Example: Phase 2 (Foundational)

```bash
# These must be sequential due to cross-file dependencies:
Task T005: "Extract helpers into custom_components/hostaway/sensor/helpers.py"
Task T006: "Extract listing entities into custom_components/hostaway/sensor/listing.py"
Task T007: "Extract reservation entity into custom_components/hostaway/sensor/reservation.py"
# T005 must complete before T007 (reservation imports from helpers)
# T008 depends on T006 + T007 (__init__.py imports from both)
Task T008: "Populate sensor/__init__.py with async_setup_entry"
Task T009: "Delete old sensor.py"
```

## Parallel Example: Phase 6 (Test Organization)

```bash
# After conftest.py is created (T024), these can run in parallel:
Task T025: "Extract TestListingSensor into tests/sensor/test_listing.py"
Task T026: "Extract reservation tests into tests/sensor/test_reservation.py"
Task T027: "Extract setup test into tests/sensor/test_setup.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1: Setup (scaffold package directory)
2. Complete Phase 2: Foundational (extract all source code and replace
   `sensor.py`)
3. Complete Phase 3: User Story 1 (validate structure)
4. Complete Phase 4: User Story 2 (validate behavior)
5. **STOP and VALIDATE**: All 317 tests pass, package structure correct
6. This proves the refactor is safe and complete

### Incremental Delivery

1. Setup + Foundational → Package exists and is functional
2. US1 + US2 → Behavioral equivalence proven (MVP!)
3. US3 → Compliance verified
4. US4 → Test organization complete
5. Polish → Final cross-cutting validation

### Sequential Single-Developer Strategy

Since this is a structural refactor with sequential dependencies:

1. Phase 1 + Phase 2 (create package, extract code, replace old file)
2. Phase 3 (verify structure — trivial after extraction)
3. Phase 4 (fix test imports so suite passes)
4. Phase 5 (verify headers — trivial, done during extraction)
5. Phase 6 (split test file)
6. Phase 7 (final validation)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- The refactor follows the identical pattern from spec 005 (services package refactor)
- No new tests are written — existing 63 tests validate equivalence
- `_warned_statuses` patch targets move from
  `custom_components.hostaway.sensor._warned_statuses` to
  `custom_components.hostaway.sensor.helpers._warned_statuses`
- `async_setup_entry` import path remains unchanged (resolves via `__init__.py`)
- `noqa: RUF012` comment must be preserved on `_attr_options` in reservation.py
- Commit after each phase or logical group per constitution principle III
