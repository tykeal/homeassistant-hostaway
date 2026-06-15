# Feature Specification: Services Package Refactor

**Feature Branch**: `005-services-package-refactor` **Created**: 2026-06-15
**Status**: Draft **Input**: User description: "Refactor services.py into a
services/ package"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Codebase Maintainability (Priority: P1)

As a developer contributing to the Hostaway integration, I want the service
handler code split into focused, single-responsibility modules so that I can
locate, understand, and modify individual service handlers without navigating a
1100+ line monolithic file.

**Why this priority**: The monolithic file is the root cause of both issues
(#68 and #71). Splitting it into a package directly addresses the complexity
flags and makes all subsequent maintenance easier.

**Independent Test**: Can be fully tested by running the entire existing test
suite (317 tests) after the refactor and confirming zero failures, proving
behavioral equivalence.

**Acceptance Scenarios**:

1. **Given** the old `services.py` exists as a single 1100+ line file, **When**
   the refactor is complete, **Then** `services.py` no longer exists and a
   `services/` package directory has taken its place with all handler logic
   distributed across sub-modules.
2. **Given** the integration is loaded by Home Assistant, **When**
   `async_setup_services()` is called from the package `__init__.py`, **Then**
   all services are registered with the same names, schemas, and behavior as
   before.
3. **Given** any sub-module in the new package, **When** its line count is
   measured, **Then** it does not exceed the file size targets defined for that
   module.

---

### User Story 2 - Function Complexity Reduction (Priority: P1)

As a developer, I want the `async_setup_services()` function reduced from 138
lines to under 30 lines so that it is easy to read at a glance and new services
can be added by appending a single entry to a registration table.

**Why this priority**: This directly addresses issue #71 and the
`complexity/function-too-long` flag. A table-driven approach eliminates
per-service boilerplate closures.

**Independent Test**: Can be tested by inspecting the line count of
`async_setup_services()` and verifying all services are registered correctly
when the integration loads.

**Acceptance Scenarios**:

1. **Given** the new `services/__init__.py` file, **When** the
   `async_setup_services()` function is measured, **Then** it is under 30
   lines.
2. **Given** the service registration table, **When** a developer adds a new
   service entry, **Then** only a single tuple/entry addition is needed with no
   new closure or function definition required in `__init__.py`.
3. **Given** Home Assistant calls `async_setup_services()`, **When** a service
   is already registered (idempotent reload), **Then** the function does not
   raise an error or duplicate the registration.

---

### User Story 3 - Transparent Refactor (Priority: P1)

As an end user of the Hostaway integration, I want the refactor to produce zero
observable changes so that my automations, service calls, and dashboards
continue working exactly as before.

**Why this priority**: Any behavioral regression would directly impact end
users. The refactor must be invisible to consumers of the integration.

**Independent Test**: Can be tested by running all 317 existing tests without
modification and confirming they pass.

**Acceptance Scenarios**:

1. **Given** the full test suite, **When** all 317 tests are executed against
   the refactored code, **Then** all tests pass without changes to test logic
   or assertions (except import or patch path updates if needed).
2. **Given** a user calls any Hostaway service via Home Assistant, **When** the
   call is processed, **Then** the response and side effects are identical to
   the pre-refactor behavior.
3. **Given** the `services.yaml` service definitions file, **When** the
   refactor is complete, **Then** the file remains unchanged.

---

### User Story 4 - Code Standards Compliance (Priority: P2)

As a project maintainer, I want all new files to include proper SPDX license
headers, aislop ignore comments using the `ai-slop/hallucinated-import` token,
and module docstrings so that the codebase remains compliant with licensing and
linting standards.

**Why this priority**: Important for ongoing compliance but does not affect
functionality. Can be verified independently of behavior.

**Independent Test**: Can be tested by checking each new file for the presence
of required headers and docstrings.

**Acceptance Scenarios**:

1. **Given** any file in the new `services/` package, **When** its first lines
   are inspected, **Then** it contains an SPDX header specifying Apache-2.0 and
   2026 Andrew Grimberg.
2. **Given** any file in the new `services/` package, **When** its contents are
   searched, **Then** it contains an aislop ignore comment using the
   `ai-slop/hallucinated-import` token.
3. **Given** any file in the new `services/` package, **When** its module-level
   docstring is inspected, **Then** it contains a meaningful description of the
   module's purpose.

---

### Edge Cases

- What happens when the integration is reloaded (config entry reload) — does
  the idempotent registration in `async_setup_services()` gracefully skip
  already-registered services?
- What happens when `async_unregister_services()` is called — are all services
  cleanly removed regardless of which sub-module defined them?
- What happens if a test imports a handler directly from the old `services`
  module path — does it fail with a clear import error?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The monolithic `services.py` file MUST be replaced by a
  `services/` package directory containing separate sub-modules organized by
  responsibility.
- **FR-002**: The package MUST expose `async_setup_services()` and
  `async_unregister_services()` from its `__init__.py` module.
- **FR-003**: Service registration MUST use a table-driven approach where each
  service is defined as an entry containing its name, handler reference,
  schema, and response support flag.
- **FR-004**: The `async_setup_services()` function MUST check whether a
  service is already registered before attempting registration (idempotent
  behavior).
- **FR-005**: All service schemas and validator functions MUST reside in a
  dedicated `schemas.py` sub-module.
- **FR-006**: All shared utility functions (entry data resolution, listing name
  indexing, listing map ID resolution, locked reservation logging, locked state
  pruning) MUST reside in a dedicated `helpers.py` sub-module.
- **FR-007**: Reservation-related service handlers MUST reside in a dedicated
  `reservation_handlers.py` sub-module.
- **FR-008**: Task-related service handlers MUST reside in a dedicated
  `task_handlers.py` sub-module.
- **FR-009**: Lookup-related service handlers (users, groups) MUST reside in a
  dedicated `lookup_handlers.py` sub-module.
- **FR-010**: The integration's main `__init__.py` MUST continue to import
  `async_setup_services` from the services package without modification to the
  import statement.
- **FR-011**: All 317 existing tests MUST pass after the refactor without
  behavioral changes.
- **FR-012**: The `services.yaml` file MUST remain unchanged.
- **FR-013**: The old `services.py` file MUST be removed once the package is in
  place.
- **FR-014**: Each new file MUST include an SPDX license header (Apache-2.0,
  2026 Andrew Grimberg), an aislop ignore comment using the
  `ai-slop/hallucinated-import` token, and a module docstring.
- **FR-015**: File size targets MUST be met: `__init__.py` < 80 lines,
  `schemas.py` < 200 lines, `helpers.py` < 200 lines, `reservation_handlers.py`
  < 400 lines, `task_handlers.py` < 400 lines, `lookup_handlers.py` < 150
  lines.
- **FR-016**: The `async_setup_services()` function MUST be under 30 lines.

### Key Entities

- **Service Definition Entry**: A structured record containing the service
  name, handler callable, validation schema, and whether the service supports
  response data. Used by the table-driven registration loop.
- **Service Handler**: An async function that processes a specific service
  call. Each handler is imported from its domain-specific sub-module.
- **Schema**: A validation definition that constrains the input data for a
  service call. All schemas are centralized in the schemas sub-module.
- **Helper Utility**: A shared function used by multiple handlers across
  different sub-modules, centralized to avoid duplication.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 317 existing tests pass with zero failures after the
  refactor.
- **SC-002**: No file in the `services/` package exceeds 400 lines.
- **SC-003**: The `async_setup_services()` function is 30 lines or fewer.
- **SC-004**: The old monolithic `services.py` file no longer exists in the
  codebase.
- **SC-005**: Adding a new service requires modifying only 2 locations: adding
  the handler function in the appropriate sub-module, and adding a single entry
  to the service registration table.
- **SC-006**: All new files contain the required SPDX header, aislop comment
  using the `ai-slop/hallucinated-import` token, and module docstring.
- **SC-007**: The integration loads and registers all services identically to
  pre-refactor behavior when tested in a Home Assistant environment.

## Assumptions

- The existing 317 tests provide sufficient coverage to validate behavioral
  equivalence (no hidden untested behaviors exist that could silently break).
- Test files may require import path updates (e.g., if tests import helpers or
  schemas directly from `services`), but test logic and assertions remain
  unchanged.
- The `async_unregister_services()` function follows a similar table-driven
  pattern for cleanup.
- The handler function signatures and return values remain identical — only
  their module location changes.
- No other files in the integration import private/internal symbols from
  `services.py` beyond what is exposed through the package's public interface.
