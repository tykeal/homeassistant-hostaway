---
description: "Implementation tasks for date-aware reservation selection"
---

<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Select Current or Upcoming Reservations

**Input**: Design documents from
`/specs/010-reservation-selection-recency/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`quickstart.md`

## Phase 1: User Stories 1 and 2 - Never Select Past Stays (P1)

**Goal**: Select a current stay, otherwise the next upcoming stay, and report
`no_reservation` when only past stays remain after cancellation filtering.

**Independent Test**: Construct reservation sensor data with stale past,
current, upcoming, and past-only listings; fix the Home Assistant local date;
verify the state and selected attributes.

### Tests First (Red)

- [ ] T001 [US1,US2,US3,US4] Add failing tests in
  `tests/sensor/test_reservation.py` that call `_select_reservation` with an
  explicit `today` for every scenario in the plan's helper test design:
  stale `checked_in` past vs future `confirmed`; equal-priority past `new` vs
  future `confirmed` in coordinator order; checkout-today and zero-night stays
  as current; a current stay beating a better-status upcoming stay; same-day
  turnovers (`checked_in` departing wins, `checked_out` departing loses to an
  arriving `confirmed`, `checked_out` departing beats an arriving `inquiry`,
  equal priority picks the later check-in); upcoming status priority before
  sooner check-in; upcoming equal priority picking the sooner check-in;
  lowest-ID tie-break in both input orders for current and upcoming; undated
  after dated regardless of status; undated-only status then input order;
  inverted range as undated; valid past check-out with malformed check-in
  excluded; past-only returning `None`; unknown status last within its group;
  and one generic undated warning without reservation data, independent of
  feature 009's check-out warning.
- [ ] T002 [US1,US2,US4] Add failing sensor tests in
  `tests/sensor/test_reservation.py` using `_make_reservation_sensor` and a
  monkeypatched `dt_util.now`: replace
  `TestUpcomingReservationFiltering::test_past_reservation_still_drives_selected_state`
  with a test asserting the future booking drives state and attributes;
  past-only listing reports `no_reservation` with the existing null-detail
  shape; cancellation filter on and off with a current cancelled stay and an
  upcoming booking; a listing whose only non-past reservation is cancelled
  reporting `no_reservation` with the filter on and `cancelled` with it off;
  state and attributes agree and the selected ID appears in
  `upcoming_reservations`; selection follows changed coordinator data; a date
  advance past check-out moves selection to the next stay and then to
  `no_reservation`.

### Implementation

- [ ] T003 [US1,US2,US3] In `custom_components/hostaway/sensor/helpers.py`,
  rename `_parse_check_out_date` to `_parse_calendar_date` and update the 009
  list filter to use it with identical behavior; add a typed, documented
  classification helper (past, current, upcoming, undated) per FR-002; change
  `_select_reservation(reservations, today)` to skip past entries and return
  the minimum rank tuple from `data-model.md`, or `None`; add the one-time
  generic undated warning behind its own module flag.
- [ ] T004 [US1,US2,US4] In `custom_components/hostaway/sensor/reservation.py`,
  pass `dt_util.now().date()` to `_select_reservation` in `native_value`; in
  `extra_state_attributes`, read the date once and pass the same value to
  `_select_reservation` and `_filter_upcoming_reservations`. Keep
  `_reservations`, cancellation filtering, coordinator data, and attribute
  construction unchanged.
- [ ] T005 [US3,US4] Update existing `TestSelectReservation` calls to pass a
  `today` that keeps their fixture dates current, and pin the Home Assistant
  date in existing sensor tests (in `tests/sensor/test_reservation.py` and,
  only where fixture dates are now past, `tests/test_integration.py`,
  `tests/test_edge_cases.py`, and `tests/sensor/test_custom_fields.py`) so
  their expected outcomes are unchanged.

**Checkpoint**: The focused sensor tests pass, including every group,
tie-break, past-only, cancellation, and calendar-advance scenario, with
`upcoming_reservations` behavior unchanged.

## Phase 2: Quality and Completion

**Purpose**: Verify the full regression surface and record completion.

- [ ] T006 Run `uv run pytest tests/ -x -q`,
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/`,
  `uv run interrogate custom_components/`, and
  `uv run pre-commit run --all-files`; resolve any regressions.
- [ ] T007 Mark the specification status as implemented in a separate
  documentation commit from implementation and test code.
- [ ] T008 Mark completed items in this `tasks.md` in a separate documentation
  commit from implementation, test code, and the specification status.

## Dependencies and Execution Order

- T001 and T002 have no implementation dependency and MUST fail on the
  current behavior before T003 or T004 begins.
- T003 depends on T001's failing helper tests.
- T004 depends on T002 and T003; it wires the local date into selection
  without changing attribute construction or the 009 list filter.
- T005 depends on T003 and T004 and keeps existing tests meaningful under a
  deterministic date.
- T006 depends on T001–T005.
- T007 depends on T006.
- T008 depends on T007 and is a separate commit from source and test changes.
