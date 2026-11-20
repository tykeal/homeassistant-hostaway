---
description: "Implementation tasks for filtering completed reservations"
---

# Tasks: Filter Past Reservations from Upcoming Attribute

**Input**: Design documents from
`/specs/009-reservation-upcoming-filter/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`quickstart.md`

## Phase 1: User Story 1 - Show Current and Future Reservations (P1)

**Goal**: Omit reservations with a valid check-out date before Home Assistant's
local date from the sensor's `upcoming_reservations` attribute.

**Independent Test**: Construct reservation sensor data with past, today,
future, and unparsable check-out values; verify the list contents, order, and
entry shape using a deterministic local clock.

### Tests First (Red)

- [ ] T001 [US1,US2] Add failing tests in
  `tests/sensor/test_reservation.py` for past-date exclusion; checkout-today,
  zero-night, and future retention; malformed-date retention and one-time
  privacy-safe warning; stable order and attributes; local timezone and DST
  boundaries; cancellation filtering; and unchanged selected state/details.

### Implementation

- [ ] T002 [US1] Add a typed, documented date-filter helper in
  `custom_components/hostaway/sensor/helpers.py` that accepts a reservation
  list and local `date`, removes only valid dates strictly before today,
  retains invalid values in order, and emits one generic warning per process.
- [ ] T003 [US1,US2] In
  `custom_components/hostaway/sensor/reservation.py`, obtain today's date from
  Home Assistant's local time utility and pass only the filtered list to
  attribute construction; keep `_reservations`, coordinator data, and selected
  reservation/state behavior unchanged.
- [ ] T004 [US1,US2] Make existing sensor-list regression tests in
  `tests/sensor/test_reservation.py` deterministic and update only those whose
  fixtures are now past relative to the test clock.

**Checkpoint**: The focused sensor tests pass, including the strict date
boundary and unchanged state-selection behavior.

## Phase 2: Quality and Completion

**Purpose**: Verify the full regression surface and record completion.

- [ ] T005 Run `uv run pytest tests/ -x -q`,
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/`,
  `uv run interrogate custom_components/`, and
  `uv run pre-commit run --all-files`; resolve any regressions.
- [ ] T006 Mark completed items in this `tasks.md` in a separate documentation
  commit from implementation and test code.

## Dependencies and Execution Order

- T001 has no implementation dependency and MUST fail on the current behavior
  before T002 or T003 begins.
- T002 depends on T001's failing date-boundary and malformed-date tests.
- T003 depends on T001 and T002; it wires filtering into the attribute path
  without changing state selection.
- T004 depends on T003 and keeps existing sensor tests meaningful under a
  deterministic date.
- T005 depends on T001–T004.
- T006 depends on T005 and is a separate commit from source and test changes.
