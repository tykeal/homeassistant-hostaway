# Implementation Plan: Hostaway Task Management Services

**Branch**: `003-task-management-services` | **Date**: 2025-07-14 | **Spec**:
`specs/003-task-management-services/spec.md` **Input**: Feature specification
from `/specs/003-task-management-services/spec.md`

## Summary

Add four Home Assistant services (`create_task`, `update_task`, `delete_task`,
`get_tasks`) that provide full CRUD management of Hostaway tasks via the
Hostaway Tasks API (`/v1/tasks`). The implementation follows the established
patterns in the codebase: API client methods modeled after
`update_reservation()`, service handlers modeled after
`async_handle_set_door_code()`, voluptuous schemas for validation, and listing
name resolution via the existing coordinator cache.

## Technical Context

**Language/Version**: Python 3.14.2 **Primary Dependencies**: httpx (HTTP client),
voluptuous (schema validation), homeassistant (core HA framework) **Storage**:
N/A (stateless services; listings cache from coordinator) **Testing**: pytest +
respx (HTTP mocking for API client), pytest + unittest.mock (for service layer)
**Target Platform**: Home Assistant custom component (any HA-supported platform)
**Project Type**: Home Assistant custom integration (services-only extension)
**Performance Goals**: Service calls complete in <5 seconds end-to-end (SC-001)
**Constraints**: Must not block HA event loop; must respect Hostaway rate limits
(15 req/10s per IP) **Scale/Scope**: 4 new services, ~5 new API client methods,
~200-300 lines of service logic

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

<!-- markdownlint-disable MD013 MD060 -->
| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality & Testing (TDD) | ✅ PASS | TDD Red-Green-Refactor will be followed; 100% docstring coverage |
| II. API Client Design | ✅ PASS | Task methods follow same pattern as `update_reservation()` |
| III. Atomic Commit Discipline | ✅ PASS | Feature broken into atomic commits per task |
| IV. Licensing & Attribution | ✅ PASS | All new files get SPDX headers |
| V. Pre-Commit Integrity | ✅ PASS | All hooks must pass before push |
| VI. Agent Co-Authorship & DCO | ✅ PASS | Co-authored-by + sign-off on all commits |
| VII. User Experience Consistency | ✅ PASS | Services follow existing patterns (config_entry_id, listing resolution) |
| VIII. Performance Requirements | ✅ PASS | All async; no polling introduced (FR-016) |
| IX. Phased Development | ✅ PASS | API layer → Service layer → Registration |
| X. Security & Credential Management | ✅ PASS | No credentials in code; tokens via existing auth flow |
<!-- markdownlint-enable MD013 MD060 -->

**Gate Result: PASS** — No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/003-task-management-services/
├── plan.md              # This file
├── research.md          # Phase 0: API research and design decisions
├── data-model.md        # Phase 1: Task entity and field mappings
├── quickstart.md        # Phase 1: Developer quickstart guide
├── contracts/           # Phase 1: Service call contracts
│   ├── create-task.md
│   ├── update-task.md
│   ├── delete-task.md
│   └── get-tasks.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── api/
│   ├── client.py        # Add: create_task(), update_task(), delete_task(), get_tasks()
│   └── exceptions.py    # Add: HostawayNotFoundError (if needed, or reuse HostawayResponseError)
├── services.py          # Add: 4 new handlers + schemas + registration
└── services.yaml        # Add: 4 new service definitions

tests/
├── api/
│   └── test_client.py   # Add: tests for new task API methods
└── test_services.py     # Add: tests for new task service handlers
```

**Structure Decision**: Existing single-project layout. New code extends
existing files (`client.py`, `services.py`, `services.yaml`) plus new test cases
in existing test files. No new directories needed in source tree.

## Complexity Tracking

No violations to justify — design follows established patterns exactly.
