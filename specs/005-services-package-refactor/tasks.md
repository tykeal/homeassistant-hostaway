# Tasks: Services Package Refactor

**Input**: Design documents from `/specs/005-services-package-refactor/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Existing 66 tests serve as the behavioral equivalence gate. No new
test creation required — only patch path updates for the new module structure.

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/hostaway/services/` (new package)
- **Tests**: `tests/test_services.py`
- **Integration**: `custom_components/hostaway/__init__.py`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the services package directory and establish the skeleton

- [ ] T001 Create services package directory at
  `custom_components/hostaway/services/`
- [ ] T002 Create empty `custom_components/hostaway/services/__init__.py` with
  SPDX header, aislop comment, and module docstring placeholder

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extract leaf-dependency modules (schemas and helpers) that all
handler modules will depend on. These MUST be complete before handler
extraction can begin.

**⚠️ CRITICAL**: No handler extraction (US1) can begin until schemas.py and
helpers.py are in place.

- [ ] T003 [P] Extract all validator functions (`_positive_int`,
  `_non_empty_string`, `_strict_string`, `_positive_int_list`,
  `_is_user_correctable_task_error`) and schema definitions
  (`SERVICE_SET_DOOR_CODE_SCHEMA`, `SERVICE_GET_RESERVATIONS_SCHEMA`,
  `SERVICE_FIND_RESERVATION_SCHEMA`, `SERVICE_CREATE_TASK_SCHEMA`,
  `SERVICE_UPDATE_TASK_SCHEMA`, `SERVICE_DELETE_TASK_SCHEMA`,
  `SERVICE_GET_TASKS_SCHEMA`, `SERVICE_GET_USERS_SCHEMA`,
  `SERVICE_GET_GROUPS_SCHEMA`) and constant `_TASK_STATUS_VALUES` into
  `custom_components/hostaway/services/schemas.py`
- [ ] T004 [P] Extract helper functions (`_resolve_entry_data`,
  `_get_listing_name_index`, `_resolve_listing_id`, `_prune_locked_state`,
  `_log_locked_reservation`) and module-level state
  (`_LOCKED_LOG_COOLDOWN_SECONDS`, `_LOCKED_RESERVATION_LOG_STATE`) into
  `custom_components/hostaway/services/helpers.py`

**Checkpoint**: Leaf modules ready — handler extraction can now begin in
parallel

---

## Phase 3: User Story 1 - Codebase Maintainability (Priority: P1) 🎯 MVP

**Goal**: Split handler logic from the monolithic `services.py` into focused
sub-modules organized by responsibility domain.

**Independent Test**: Each handler module can be imported independently; all
handler functions are accessible from their new locations.

### Implementation for User Story 1

- [ ] T005 [P] [US1] Extract reservation handlers
  (`async_handle_set_door_code`, `async_handle_get_reservations`,
  `async_handle_find_reservation`, `_reservation_result`) into
  `custom_components/hostaway/services/reservation_handlers.py` with imports
  from `helpers` and `schemas`
- [ ] T006 [P] [US1] Extract task handlers (`async_handle_create_task`,
  `async_handle_update_task`, `async_handle_delete_task`,
  `async_handle_get_tasks`) into
  `custom_components/hostaway/services/task_handlers.py` with imports from
  `helpers` and `schemas`
- [ ] T007 [P] [US1] Extract lookup handlers (`async_handle_get_users`,
  `async_handle_get_groups`) into
  `custom_components/hostaway/services/lookup_handlers.py` with imports from
  `helpers` and `schemas`

**Checkpoint**: All handler code is distributed across focused sub-modules;
each module imports from helpers/schemas only

---

## Phase 4: User Story 2 - Function Complexity Reduction (Priority: P1)

**Goal**: Implement table-driven service registration that reduces
`async_setup_services()` to under 30 lines and adds symmetric
`async_unregister_services()`.

**Independent Test**: `async_setup_services()` is ≤ 30 lines; service
registration table contains all 9 services (set_door_code, get_reservations,
find_reservation, create_task, update_task, delete_task, get_tasks, get_users,
get_groups); `async_unregister_services()` removes all services via the same
table.

### Implementation for User Story 2

- [ ] T008 [US2] Implement `ServiceDefinition` NamedTuple and
  `SERVICE_DEFINITIONS` list (9 entries) in
  `custom_components/hostaway/services/__init__.py` importing handlers from
  sub-modules and schemas from `schemas.py`
- [ ] T009 [US2] Implement table-driven `async_setup_services(hass)` function
  (< 30 lines) using `functools.partial` for hass injection and idempotent
  registration check in `custom_components/hostaway/services/__init__.py`
- [ ] T010 [US2] Implement table-driven `async_unregister_services(hass)`
  function using `SERVICE_DEFINITIONS` table in
  `custom_components/hostaway/services/__init__.py`
- [ ] T011 [US2] Update `custom_components/hostaway/__init__.py` to import
  `async_unregister_services` from the services package and replace the 9
  inline `hass.services.async_remove()` calls with a single
  `async_unregister_services(hass)` call

**Checkpoint**: Registration is table-driven; `async_setup_services()` ≤ 30
lines; adding a new service requires only a handler + schema + one table entry

---

## Phase 5: User Story 3 - Transparent Refactor (Priority: P1)

**Goal**: All 66 existing service tests pass without behavioral changes. Test
patch paths are updated to reference the new sub-module locations.

**Independent Test**: Run `uv run pytest tests/test_services.py -v` — all 66
tests pass with zero failures.

### Implementation for User Story 3

- [ ] T012 [US3] Update test patch paths for reservation handler tests in
  `tests/test_services.py`: change
  `custom_components.hostaway.services.HostawayApiClient` to
  `custom_components.hostaway.services.reservation_handlers.HostawayApiClient`
  for reservation-related method patches (update_reservation,
  get_all_reservations, get_reservation)
- [ ] T013 [US3] Update test patch paths for task handler tests in
  `tests/test_services.py`: change
  `custom_components.hostaway.services.HostawayApiClient` to
  `custom_components.hostaway.services.task_handlers.HostawayApiClient` for
  task-related method patches (create_task, update_task, delete_task,
  get_tasks)
- [ ] T014 [US3] Update test patch paths for lookup handler tests in
  `tests/test_services.py`: change
  `custom_components.hostaway.services.HostawayApiClient` to
  `custom_components.hostaway.services.lookup_handlers.HostawayApiClient` for
  lookup-related method patches (get_users, get_groups)
- [ ] T015 [US3] Update test patch paths for helper references in
  `tests/test_services.py`: change
  `custom_components.hostaway.services._resolve_entry_data` to
  `custom_components.hostaway.services.helpers._resolve_entry_data` and update
  logger name assertions from `custom_components.hostaway.services` to
  `custom_components.hostaway.services.reservation_handlers` (or appropriate
  sub-module)
- [ ] T016 [US3] Delete the old monolithic
  `custom_components/hostaway/services.py` file (the package directory
  `services/` now replaces it)
- [ ] T017 [US3] Run full test suite (`uv run pytest tests/test_services.py
  -v`) and confirm all 66 tests pass with zero failures

**Checkpoint**: Behavioral equivalence proven — all tests pass, services.yaml
unchanged, integration loads identically

---

## Phase 6: User Story 4 - Code Standards Compliance (Priority: P2)

**Goal**: All new files contain proper SPDX license headers, aislop-ignore-file
comments, and meaningful module docstrings.

**Independent Test**: Each file in `custom_components/hostaway/services/`
starts with the required SPDX header, contains `aislop-ignore-file:
hallucinated-import`, and has a module-level docstring.

### Implementation for User Story 4

- [ ] T018 [P] [US4] Verify and finalize SPDX header (`#
  SPDX-FileCopyrightText: 2026 Andrew Grimberg` / `# SPDX-License-Identifier:
  Apache-2.0`), aislop comment (`# aislop-ignore-file: hallucinated-import`),
  and module docstring in `custom_components/hostaway/services/__init__.py`
- [ ] T019 [P] [US4] Verify and finalize SPDX header, aislop comment, and
  module docstring in `custom_components/hostaway/services/schemas.py`
- [ ] T020 [P] [US4] Verify and finalize SPDX header, aislop comment, and
  module docstring in `custom_components/hostaway/services/helpers.py`
- [ ] T021 [P] [US4] Verify and finalize SPDX header, aislop comment, and
  module docstring in
  `custom_components/hostaway/services/reservation_handlers.py`
- [ ] T022 [P] [US4] Verify and finalize SPDX header, aislop comment, and
  module docstring in `custom_components/hostaway/services/task_handlers.py`
- [ ] T023 [P] [US4] Verify and finalize SPDX header, aislop comment, and
  module docstring in `custom_components/hostaway/services/lookup_handlers.py`

**Checkpoint**: All files comply with licensing and linting standards

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, file size checks, and pre-commit compliance

- [ ] T024 Validate file size targets: `__init__.py` < 80, `schemas.py` < 200,
  `helpers.py` < 200, `reservation_handlers.py` < 400, `task_handlers.py` <
  400, `lookup_handlers.py` < 150 lines (run `wc -l
  custom_components/hostaway/services/*.py`)
- [ ] T025 Verify `async_setup_services()` is ≤ 30 lines in
  `custom_components/hostaway/services/__init__.py`
- [ ] T026 Verify `services.yaml` is unchanged (no modifications to
  `custom_components/hostaway/services.yaml`)
- [ ] T027 Run full project test suite (`uv run pytest`) to confirm all 317
  tests pass
- [ ] T028 Run pre-commit hooks (`pre-commit run --all-files`) and resolve any
  lint/type/format issues
- [ ] T029 Run type checking (`uv run mypy
  custom_components/hostaway/services/`) and confirm zero errors

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (package directory must exist)
  — BLOCKS all handler extraction
- **User Story 1 (Phase 3)**: Depends on Phase 2 (schemas.py + helpers.py must
  be in place)
- **User Story 2 (Phase 4)**: Depends on Phase 3 (all handlers must be
  extracted before registration table references them)
- **User Story 3 (Phase 5)**: Depends on Phase 4 (full package must be
  assembled before tests can be validated)
- **User Story 4 (Phase 6)**: Can start after Phase 2 (headers can be added as
  files are created) — but final verification after Phase 4
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Foundational — Core structural split
- **User Story 2 (P1)**: Depends on US1 — Registration references extracted
  handlers
- **User Story 3 (P1)**: Depends on US2 — Full package must be assembled for
  test validation
- **User Story 4 (P2)**: Partially parallel with US1-US2 (headers added during
  file creation), final check after Phase 4

### Within Each User Story

- Models/schemas before handlers (US1 depends on Foundational)
- Handler extraction is parallelizable (T005, T006, T007 are independent)
- Registration table depends on all handlers being extracted
- Test updates depend on registration being complete
- Compliance checks are parallelizable across files

### Parallel Opportunities

- **Phase 2**: T003 (schemas.py) and T004 (helpers.py) can run in parallel — no
  mutual dependencies
- **Phase 3**: T005, T006, T007 (all handler modules) can run in parallel —
  each targets a different file and imports from the same leaf modules
- **Phase 6**: T018–T023 (all compliance checks) can run in parallel — each
  targets a different file

---

## Parallel Example: User Story 1

```bash
# Launch all handler extractions together (all [P] marked):
Task: "Extract reservation handlers into custom_components/hostaway/services/reservation_handlers.py"
Task: "Extract task handlers into custom_components/hostaway/services/task_handlers.py"
Task: "Extract lookup handlers into custom_components/hostaway/services/lookup_handlers.py"
```

## Parallel Example: Foundational

```bash
# Launch both leaf modules together (all [P] marked):
Task: "Extract validators and schemas into custom_components/hostaway/services/schemas.py"
Task: "Extract helpers and state into custom_components/hostaway/services/helpers.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 + 3)

1. Complete Phase 1: Setup (create package directory)
2. Complete Phase 2: Foundational (schemas.py + helpers.py)
3. Complete Phase 3: User Story 1 (extract all handlers)
4. Complete Phase 4: User Story 2 (table-driven registration)
5. Complete Phase 5: User Story 3 (test validation)
6. **STOP and VALIDATE**: Run `uv run pytest tests/test_services.py -v` — all
   66 tests pass
7. The refactor is functionally complete at this point

### Incremental Delivery

1. Complete Setup + Foundational → Leaf modules ready
2. Add US1 (handler split) → Package structure in place
3. Add US2 (registration) → Integration fully wired
4. Add US3 (test validation) → Behavioral equivalence proven (MVP!)
5. Add US4 (compliance) → Standards adherence verified
6. Polish → File sizes validated, pre-commit clean, full suite green

### Sequential Execution (Single Developer)

This refactor is inherently sequential due to strong inter-story dependencies:

1. Phases 1–2: Foundation (~30 min)
2. Phase 3: Handler extraction (~45 min, parallel within phase)
3. Phase 4: Registration wiring (~20 min)
4. Phase 5: Test path updates (~30 min)
5. Phase 6: Compliance (~10 min, parallel within phase)
6. Phase 7: Polish & validation (~15 min)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- The old `services.py` MUST be deleted (T016) only after the package is fully
  functional
- `services.py` and `services/` directory cannot coexist — Python would be
  ambiguous
- Git tracks this as delete + create (not rename) since paths differ
  structurally
- All handler function signatures remain identical — only module location
  changes
- `functools.partial(handler, hass)` replaces per-service closure definitions
- Test patch paths must reference the sub-module where the symbol is imported
  (not `__init__.py`)
