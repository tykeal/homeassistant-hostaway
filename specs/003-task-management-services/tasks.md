# Tasks: Hostaway Task Management Services

**Input**: Design documents from `/specs/003-task-management-services/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
contracts/ ✅, quickstart.md ✅

**Tests**: Included per Constitution Principle I (TDD Red-Green-Refactor).

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/hostaway/`
- **Tests**: `tests/`
- **API client**: `custom_components/hostaway/api/client.py`
- **Services**: `custom_components/hostaway/services.py`
- **Service YAML**: `custom_components/hostaway/services.yaml`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Service YAML definitions and listing resolution helper shared by
all stories

- [X] T001 Add `_resolve_listing_id()` helper function to
  custom_components/hostaway/services.py
- [X] T002 Add task service definitions (create_task, update_task, delete_task,
  get_tasks) to custom_components/hostaway/services.yaml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: API client methods that ALL service handlers depend on. Must
complete before any user story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational Phase

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation (TDD
  Red phase)**

- [X] T003 [P] Write failing test for `create_task()` success and error cases in
  tests/api/test_client.py
- [X] T004 [P] Write failing test for `update_task()` success and error cases in
  tests/api/test_client.py
- [X] T005 [P] Write failing test for `delete_task()` success and error cases in
  tests/api/test_client.py
- [X] T006 [P] Write failing test for `get_tasks()` success and error cases in
  tests/api/test_client.py

### Implementation for Foundational Phase

- [X] T007 [P] Implement `create_task(self, data: dict[str, Any]) -> dict[str,
  Any]` in custom_components/hostaway/api/client.py
- [X] T008 [P] Implement `update_task(self, task_id: int, data: dict[str, Any])
  -> dict[str, Any]` in custom_components/hostaway/api/client.py
- [X] T009 [P] Implement `delete_task(self, task_id: int) -> None` in
  custom_components/hostaway/api/client.py
- [X] T010 [P] Implement `get_tasks(self, params: dict[str, Any] | None = None)
  -> list[dict[str, Any]]` in custom_components/hostaway/api/client.py

**Checkpoint**: All API client methods implemented and passing tests. Service
handlers can now be built.

---

## Phase 3: User Story 1 - Create Task from Automation (Priority: P1) 🎯 MVP

**Goal**: Property managers can create Hostaway tasks from HA automations with
title (required) and optional fields (listing, description, dates, etc.)

**Independent Test**: Call create_task service with a title → verify task
created in Hostaway and data returned.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation (TDD
  Red phase)**

- [X] T011 [US1] Write failing tests for `async_handle_create_task()` in
  tests/test_services.py covering: success with title only, success with all
  fields, listing_name resolution, listing_name not found error, API error
  handling, config_entry_id auto-detection

### Implementation for User Story 1

- [X] T012 [US1] Implement `SERVICE_CREATE_TASK_SCHEMA` voluptuous schema in
  custom_components/hostaway/services.py
- [X] T013 [US1] Implement `async_handle_create_task()` handler in
  custom_components/hostaway/services.py (builds camelCase payload, resolves
  listing, calls api_client.create_task, handles errors per contract)
- [X] T014 [US1] Register create_task service in `async_setup_services()` with
  SupportsResponse.ONLY in custom_components/hostaway/services.py

**Checkpoint**: User Story 1 fully functional — create_task service works
end-to-end with all acceptance scenarios passing.

---

## Phase 4: User Story 2 - Update Existing Task (Priority: P2)

**Goal**: Property managers can update task details (status, description,
assignee, priority) from automations to progress tasks automatically.

**Independent Test**: Call update_task with a known task_id and new status →
verify updated fields in returned data.

### Tests for User Story 2

- [X] T015 [US2] Write failing tests for `async_handle_update_task()` in
  tests/test_services.py covering: success with status update, success with
  multiple fields, task_id not found error, listing_name resolution, API error
  handling

### Implementation for User Story 2

- [X] T016 [US2] Implement `SERVICE_UPDATE_TASK_SCHEMA` voluptuous schema in
  custom_components/hostaway/services.py
- [X] T017 [US2] Implement `async_handle_update_task()` handler in
  custom_components/hostaway/services.py (builds camelCase payload from optional
  fields, resolves listing, calls api_client.update_task, handles not-found and
  API errors per contract)
- [X] T018 [US2] Register update_task service in `async_setup_services()` with
  SupportsResponse.ONLY in custom_components/hostaway/services.py

**Checkpoint**: User Story 2 fully functional — update_task service works
end-to-end.

---

## Phase 5: User Story 3 - List and Filter Tasks (Priority: P3)

**Goal**: Property managers can retrieve and filter tasks for conditional
automation logic and reporting.

**Independent Test**: Call get_tasks with various filter combinations → verify
returned list matches expectations.

### Tests for User Story 3

- [X] T019 [US3] Write failing tests for `async_handle_get_tasks()` in
  tests/test_services.py covering: success with no filters, listing_name filter
  resolution, status filter, date range filters, listing_name not found error,
  API error handling

### Implementation for User Story 3

- [X] T020 [US3] Implement `SERVICE_GET_TASKS_SCHEMA` voluptuous schema in
  custom_components/hostaway/services.py
- [X] T021 [US3] Implement `async_handle_get_tasks()` handler in
  custom_components/hostaway/services.py (builds camelCase query params,
  resolves listing filter, calls api_client.get_tasks, wraps result in {"tasks":
  [...]}, handles errors per contract)
- [X] T022 [US3] Register get_tasks service in `async_setup_services()` with
  SupportsResponse.ONLY in custom_components/hostaway/services.py

**Checkpoint**: User Story 3 fully functional — get_tasks service works
end-to-end with all filter combinations.

---

## Phase 6: User Story 4 - Delete Task (Priority: P4)

**Goal**: Property managers can delete cancelled/erroneous tasks created by
automations.

**Independent Test**: Call delete_task with a known task_id → verify task
removed (no data returned).

### Tests for User Story 4

- [X] T023 [US4] Write failing tests for `async_handle_delete_task()` in
  tests/test_services.py covering: success (returns None), task_id not found
  error, API error handling

### Implementation for User Story 4

- [X] T024 [US4] Implement `SERVICE_DELETE_TASK_SCHEMA` voluptuous schema in
  custom_components/hostaway/services.py
- [X] T025 [US4] Implement `async_handle_delete_task()` handler in
  custom_components/hostaway/services.py (calls api_client.delete_task, handles
  not-found and API errors per contract, returns None)
- [X] T026 [US4] Register delete_task service in `async_setup_services()` with
  SupportsResponse.NONE in custom_components/hostaway/services.py

**Checkpoint**: User Story 4 fully functional — delete_task service works
end-to-end.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validation, documentation, and final integration checks

- [X] T027 [P] Verify all new methods and handlers have 100% docstring coverage
  per Constitution Principle I in custom_components/hostaway/api/client.py and
  custom_components/hostaway/services.py
- [X] T028 [P] Verify SPDX license headers present on any new/modified files per
  Constitution Principle IV
- [X] T029 Run full test suite (`uv run pytest tests/ -v`) and confirm all tests
  pass
- [X] T030 Run pre-commit hooks (`uv run pre-commit run --all-files`) and fix
  any issues
- [X] T031 Run quickstart.md validation steps (ruff check, ruff format, mypy)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T001 (listing resolver needed for
  service tests). BLOCKS all user stories.
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion (API
  client methods)
  - User stories can proceed sequentially in priority order (P1 → P2 → P3 → P4)
  - Or in parallel since they modify the same file (services.py) — coordinate
    carefully
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on T001 (listing resolver), T007 (create_task
  client method) — no dependency on other stories
- **User Story 2 (P2)**: Depends on T001 (listing resolver), T008 (update_task
  client method) — no dependency on other stories
- **User Story 3 (P3)**: Depends on T001 (listing resolver), T010 (get_tasks
  client method) — no dependency on other stories
- **User Story 4 (P4)**: Depends on T009 (delete_task client method) — no
  dependency on other stories, no listing resolution needed

### Within Each User Story (TDD Cycle)

1. Write failing tests FIRST (Red)
2. Implement schema
3. Implement handler (Green)
4. Register service
5. Refactor if needed

### Parallel Opportunities

- T003-T006: All API client tests can be written in parallel (different test
  functions, same file)
- T007-T010: All API client methods can be implemented in parallel (same file
  but independent methods)
- T011, T015, T019, T023: Service handler tests CAN run in parallel if
  coordinated (same file)
- T027-T028: Polish tasks can run in parallel (different concerns)

---

## Parallel Example: Foundational Phase

```bash
# Launch all API client tests in parallel (TDD Red):
Task T003: "Write failing test for create_task() in tests/api/test_client.py"
Task T004: "Write failing test for update_task() in tests/api/test_client.py"
Task T005: "Write failing test for delete_task() in tests/api/test_client.py"
Task T006: "Write failing test for get_tasks() in tests/api/test_client.py"

# Then launch all API client implementations in parallel (TDD Green):
Task T007: "Implement create_task() in custom_components/hostaway/api/client.py"
Task T008: "Implement update_task() in custom_components/hostaway/api/client.py"
Task T009: "Implement delete_task() in custom_components/hostaway/api/client.py"
Task T010: "Implement get_tasks() in custom_components/hostaway/api/client.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational — at minimum T003 + T007 for create_task
   client method
3. Complete Phase 3: User Story 1 (T011-T014)
4. **STOP and VALIDATE**: Test create_task service independently
5. Deploy/demo if ready — automations can now create tasks

### Incremental Delivery

1. Setup + Foundational → API layer ready
2. Add User Story 1 (create_task) → Test → Deploy (MVP!)
3. Add User Story 2 (update_task) → Test → Deploy (closed-loop automation)
4. Add User Story 3 (get_tasks) → Test → Deploy (conditional logic)
5. Add User Story 4 (delete_task) → Test → Deploy (full CRUD)
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (create_task)
   - Developer B: User Story 2 (update_task)
   - Developer C: User Story 3 (get_tasks) + User Story 4 (delete_task)
3. Coordinate on services.py edits (non-overlapping functions)

---

## Notes

- [P] tasks = different files or independent functions, no dependencies
- [Story] label maps task to specific user story for traceability
- TDD: Write tests first (Red), implement (Green), refactor
- All service handlers follow `async_handle_set_door_code()` pattern exactly
- All API client methods follow `update_reservation()` pattern exactly
- snake_case → camelCase mapping is manual (per R2 decision)
- Commit after each task with conventional commit format per quickstart.md
- SPDX headers required on all new/modified files
