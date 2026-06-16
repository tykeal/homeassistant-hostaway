# Feature Specification: Sensor Package Refactor

**Feature Branch**: `006-sensor-package-refactor`
**Created**: 2026-06-16
**Status**: Draft
**Input**: User description: "Refactor `sensor.py` into a `sensor/` package
to reduce file size below the 400-line threshold (issue #70)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Codebase Maintainability (Priority: P1)

As a developer contributing to the Hostaway integration, I want the sensor
platform code split into focused, single-responsibility modules so that I can
locate, understand, and modify individual sensor classes without navigating a
555-line monolithic file.

**Why this priority**: The monolithic file directly triggers the aislop
file-size flag (issue #70). Splitting it into a package eliminates the flag and
improves developer experience for all future sensor work.

**Independent Test**: Can be independently validated by confirming
`sensor.py` has been replaced by the `sensor/` package, each new module stays
within the size limits, and
`from custom_components.hostaway.sensor import async_setup_entry` still
resolves. Full behavioral equivalence is validated in User Story 2.

**Acceptance Scenarios**:

1. **Given** the old `sensor.py` exists as a single 555-line file, **When** the
   refactor is complete, **Then** `sensor.py` no longer exists and a `sensor/`
   package directory has taken its place with all sensor logic distributed
   across sub-modules.
2. **Given** the integration is loaded by Home Assistant, **When**
   `async_setup_entry()` is called from the package `__init__.py`, **Then** all
   sensor entities are created with the same names, unique IDs, and behavior as
   before.
3. **Given** any sub-module in the new package, **When** its line count is
   measured, **Then** it does not exceed 400 lines.

---

### User Story 2 - Transparent Refactor (Priority: P1)

As an end user of the Hostaway integration, I want the refactor to produce zero
observable changes so that my dashboards, automations, and entity configurations
continue working exactly as before.

**Why this priority**: Any behavioral regression would directly impact end
users. The refactor must be invisible to consumers of the integration.

**Independent Test**: Can be tested by running all 317 existing tests with only
import or patch path updates to the test files (no logic or assertion changes)
and confirming they pass.

**Acceptance Scenarios**:

1. **Given** the full test suite, **When** all 317 tests are executed against
   the refactored code, **Then** all tests pass without changes to test logic
   or assertions (except import or patch path updates if needed).
2. **Given** a user's Home Assistant instance with the Hostaway integration,
   **When** the integration loads after the refactor, **Then** all sensor
   entities have the same entity IDs, states, and attributes as before.
3. **Given** the Home Assistant platform loader, **When** it resolves the
   `sensor` platform for the Hostaway integration, **Then** it finds
   `async_setup_entry` in `custom_components.hostaway.sensor` without any
   changes to `manifest.json` or platform registration.

---

### User Story 3 - Code Standards Compliance (Priority: P2)

As a project maintainer, I want all new files to include proper SPDX license
headers, aislop ignore comments using the `ai-slop/hallucinated-import` token,
and module docstrings so that the codebase remains compliant with licensing and
linting standards.

**Why this priority**: Important for ongoing compliance but does not affect
functionality. Can be verified independently of behavior.

**Independent Test**: Can be tested by checking each new file for the presence
of required headers and docstrings.

**Acceptance Scenarios**:

1. **Given** any file in the new `sensor/` package, **When** its first lines
   are inspected, **Then** it contains an SPDX header specifying Apache-2.0 and
   2026 Andrew Grimberg.
2. **Given** any file in the new `sensor/` package, **When** its contents are
   searched, **Then** it contains an aislop ignore comment using the
   `ai-slop/hallucinated-import` token.
3. **Given** any file in the new `sensor/` package, **When** its module-level
   docstring is inspected, **Then** it contains a meaningful description of the
   module's purpose.

---

### User Story 4 - Test Organization (Priority: P2)

As a developer, I want the sensor tests split into focused test modules that
mirror the source package structure so that I can run targeted tests for a
specific sensor concern without executing the entire sensor test suite.

**Why this priority**: Improves developer velocity for targeted testing and
keeps the test directory organized consistently with the source.

**Independent Test**: Can be tested by verifying the test directory structure
matches the source package, all test imports resolve, and total test count is
preserved.

**Acceptance Scenarios**:

1. **Given** the refactored test directory `tests/sensor/`, **When** its
   contents are listed, **Then** it contains `__init__.py`,
   `test_listing.py`, `test_reservation.py`, and `test_setup.py`.
2. **Given** the old `tests/test_sensor.py`, **When** the refactor is
   complete, **Then** the file no longer exists and all its tests live in
   the new `tests/sensor/` sub-modules.
3. **Given** a developer runs tests from `tests/sensor/test_listing.py`,
   **When** the tests execute, **Then** only listing-sensor-related tests run.

---

### Edge Cases

- What happens when the integration is reloaded — does `async_setup_entry`
  still register the coordinator listener for new listings and clean up on
  unload?
- What happens when tests or internal code import moved sensor classes or
  helpers — they should update to the new sub-module paths instead of
  relying on package-level re-exports, while
  `custom_components.hostaway.sensor` continues to expose
  `async_setup_entry` via `sensor/__init__.py`?
- What happens when a new listing appears at runtime — does
  `_async_add_new_listings` still create both listing sensors and a
  reservation sensor for the new listing?
- What happens with the module-level `_warned_statuses` set — is it
  correctly shared across usages within the helpers module after the split?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The monolithic `sensor.py` file MUST be replaced by a `sensor/`
  package directory containing separate sub-modules organized by responsibility.
- **FR-002**: The package MUST expose `async_setup_entry` from its `__init__.py`
  module so that the Home Assistant platform loader resolves
  `custom_components.hostaway.sensor` unchanged.
- **FR-003**: The `sensor/__init__.py` MUST contain `async_setup_entry`,
  `_async_add_new_listings`, and the necessary platform imports to wire up
  both listing and reservation sensors.
- **FR-004**: A `sensor/listing.py` sub-module MUST contain the
  `HostawayListingSensorDescription` dataclass, the
  `LISTING_SENSOR_DESCRIPTIONS` tuple, and the `HostawayListingSensor` class.
- **FR-005**: A `sensor/reservation.py` sub-module MUST contain the
  `HostawayReservationStatusSensor` class.
- **FR-006**: A `sensor/helpers.py` sub-module MUST contain the status maps
  (`_STATUS_PRIORITY`, `_STATUS_TO_DERIVED`, `_CANCELLED_STATUSES`), the
  module-level `_MAX_WARNED_STATUSES` cap, the module-level
  `_warned_statuses` set, and the helper functions
  (`_select_reservation`, `_derive_state`, `_build_reservation_attributes`).
- **FR-007**: All 317 existing tests MUST pass after the refactor without
  behavioral changes.
- **FR-008**: Each new file in the `sensor/` package MUST stay under 400 lines.
- **FR-009**: The old `sensor.py` file MUST be removed once the package is in
  place.
- **FR-010**: Each new file MUST include the exact REUSE-compliant Python
  copyright line `# SPDX-FileCopyrightText: 2026 Andrew Grimberg
  <tykeal@bardicgrove.org>`, the exact Apache-2.0 SPDX license identifier
  line, an aislop ignore comment using the `ai-slop/hallucinated-import`
  token, and a module docstring.
- **FR-011**: Existing aislop inline suppressions MUST be preserved or moved
  to their new home files.
- **FR-012**: No new external dependencies MUST be introduced.
- **FR-013**: The test file `tests/test_sensor.py` MUST be replaced by a
  `tests/sensor/` package with `__init__.py`, `test_listing.py`,
  `test_reservation.py`, and `test_setup.py`.
- **FR-014**: Test logic and assertions MUST remain unchanged; only import paths
  and patch targets may be updated to reflect the new module structure.

### Key Entities

- **HostawayListingSensorDescription**: Dataclass extending
  `SensorEntityDescription` with a `value_fn` callable for extracting listing
  attribute values. Lives in `sensor/listing.py`.
- **HostawayListingSensor**: Entity class for per-listing attribute sensors
  (ID, name, status, price, bedrooms, bathrooms, max guests). Lives in
  `sensor/listing.py`.
- **HostawayReservationStatusSensor**: Entity class for per-listing reservation
  status with priority-based selection and state derivation. Lives in
  `sensor/reservation.py`.
- **Status Maps**: `_STATUS_PRIORITY`, `_STATUS_TO_DERIVED`,
  `_CANCELLED_STATUSES` — configuration data driving reservation status logic.
  Lives in `sensor/helpers.py`.
- **Helper Functions**: `_select_reservation`, `_derive_state`,
  `_build_reservation_attributes` — pure-logic functions used by the
  reservation sensor. Lives in `sensor/helpers.py`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 317 existing tests pass with zero failures after the refactor.
- **SC-002**: No file in the `sensor/` package exceeds 400 lines.
- **SC-003**: The old monolithic `sensor.py` file no longer exists in the
  codebase.
- **SC-004**: The `sensor/__init__.py` file is under 100 lines (platform setup
  wiring only).
- **SC-005**: The total test count across `tests/sensor/` equals the count
  previously in `tests/test_sensor.py` (63 test functions preserved).
- **SC-006**: All new files contain the required REUSE-compliant SPDX header,
  aislop comment using the `ai-slop/hallucinated-import` token, and module
  docstring.
- **SC-007**: The integration loads and creates all sensor entities identically
  to pre-refactor behavior when tested in a Home Assistant environment.
- **SC-008**: The public import path `custom_components.hostaway.sensor`
  continues to resolve `async_setup_entry` without changes to manifest or
  platform registration.

## Assumptions

- The existing 317 tests provide sufficient coverage to validate behavioral
  equivalence (no hidden untested behaviors exist that could silently break).
- Test files may require import path updates (e.g., if tests patch internal
  module symbols), but test logic and assertions remain unchanged.
- The `_warned_statuses` module-level set in `helpers.py` continues to function
  correctly as shared mutable state since it is accessed only at runtime through
  function calls (same process, same module instance).
- The refactor follows the same pattern established in the successful services/
  package refactor (spec 005).
- No other files in the integration import private/internal symbols from
  `sensor.py` beyond what is needed via the platform loader's standard
  `async_setup_entry` lookup.
- The `noqa: RUF012` comment on `_attr_options` in
  `HostawayReservationStatusSensor` is preserved in its new location.
