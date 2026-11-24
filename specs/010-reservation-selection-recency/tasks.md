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

- [x] T001 [US1,US2,US3,US4] Add failing tests in
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
- [x] T002 [US1,US2,US4] Add failing sensor tests in
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

- [x] T003 [US1,US2,US3] In `custom_components/hostaway/sensor/helpers.py`,
  rename `_parse_check_out_date` to `_parse_calendar_date` and update the 009
  list filter to use it with identical behavior; add a typed, documented
  classification helper (past, current, upcoming, undated) per FR-002; change
  `_select_reservation(reservations, today)` to skip past entries and return
  the minimum rank tuple from `data-model.md`, or `None`; add the one-time
  generic undated warning behind its own module flag.
- [x] T004 [US1,US2,US4] In `custom_components/hostaway/sensor/reservation.py`,
  pass `dt_util.now().date()` to `_select_reservation` in `native_value`; in
  `extra_state_attributes`, read the date once and pass the same value to
  `_select_reservation` and `_filter_upcoming_reservations`. Keep
  `_reservations`, cancellation filtering, coordinator data, and attribute
  construction unchanged.
- [x] T005 [US3,US4] Update existing `TestSelectReservation` calls to pass a
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

- [x] T006 Run `uv run pytest tests/ -x -q`,
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/`,
  `uv run interrogate custom_components/`, and
  `uv run pre-commit run --all-files`; resolve any regressions.
- [x] T007 Mark the specification status as implemented in a separate
  documentation commit from implementation and test code.
- [x] T008 Mark completed items in this `tasks.md` in a separate documentation
  commit from implementation, test code, and the specification status.

## Phase 3: Amendment - Drop Invalid-Date Reservations at Ingest (Issue #300)

**Goal**: Reject reservations with invalid dates (a missing or non-canonical
check-in or check-out, or check-out before check-in) when they are parsed,
drop them from reservation lists with an IDs-only log, and remove the
sensor's now-dead invalid-date handling, per spec FR-002, FR-012, FR-015 to
FR-017, and SC-006.

**Independent Test**: Parse reservation payloads and pages containing
invalid dates; verify rejection, that valid records survive, the log
content, and the custom-field service error; verify sensor selection and
`upcoming_reservations` for valid data are unchanged.

### Amendment Tests First (Red)

- [ ] T009 [US3] In the API model tests, add failing tests that
  `HostawayReservation.from_api_response` and direct construction raise
  `ValueError` for unparsable, non-canonical (for example `2025-8-1` and a
  timestamp), and empty values of `arrivalDate` and `departureDate`, and for
  a departure before arrival, with messages that omit the raw value; equal
  dates are accepted.
- [ ] T010 [US3] In the reservation parsing tests, add failing tests that a
  page containing one invalid-date record returns the remaining valid
  records, and that the skip warning contains the reservation and listing
  IDs but not the raw date value or guest name; a non-integer reservation ID
  is omitted from the log; and a full page whose last record has a
  non-integer ID (via `fetch_all_reservations`) logs no raw ID value.
- [ ] T011 [US3] In the custom-field service tests, add a failing test that
  reading or writing a reservation whose payload has invalid dates raises
  `ServiceValidationError` naming the reservation and sends no write.
- [ ] T012 [US3] In `tests/sensor/test_reservation.py`, remove the 009 test
  asserting invalid check-out retention and the 010 undated selection and
  warning tests (`test_undated_ranks_after_dated`,
  `test_undated_only_uses_status_then_input_order`, the inverted and
  malformed-date cases, and both warning tests), since such reservations can
  no longer be constructed. Give existing fixtures that build inverted ranges
  (a later check-in over the default `2025-08-05` check-out, in
  `tests/test_coordinator.py` and `tests/sensor/test_reservation.py`) a
  check-out on or after check-in, keeping their assertions. Keep valid-data
  selection and list tests otherwise unchanged.

### Amendment Implementation

- [ ] T013 [US3] Add `custom_components/hostaway/api/dates.py` with the
  canonical `parse_calendar_date` moved from the sensor helpers; validate
  reservation dates in `HostawayReservation.__post_init__` in
  `api/models.py` with generic messages; log the reservation ID in
  `_log_skipped_reservation` only when it is a positive integer, and drop
  the raw last ID from the `reservation_page_cursor` warning; translate
  `ValueError` in the custom-field services' `_read_target_collection` and
  `_read_target_model` (`_read_target_snapshot` already does).
- [ ] T014 [US1,US2,US3] In `custom_components/hostaway/sensor/helpers.py`,
  remove the undated group and rank, 009's invalid check-out retention, both
  warning flags, and the local date parser; parse dates with
  `date.fromisoformat`; update docstrings. Past, current, and upcoming rules,
  tie-breaks, and list order are unchanged.
- [ ] T015 Run `uv run pytest tests/ -q`,
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/`, `uv run interrogate custom_components/`,
  and `uv run pre-commit run --all-files`; resolve any regressions.
- [ ] T016 Mark T009-T016 complete in this `tasks.md` in a separate
  documentation commit from implementation and test code.

**Checkpoint**: No invalid-date reservation is parsed, selected, or listed;
valid reservation selection, tie-breaks, list order, and past and
checkout-today semantics are unchanged.

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
- T009-T012 MUST fail (or, for T012, be removed) against the current
  behavior before T013 begins.
- T013 depends on T009-T011; T014 depends on T012 and T013.
- T015 depends on T013 and T014.
- T016 depends on T015 and is a separate commit from source and test changes.
