<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Select Current or Upcoming Reservations

**Branch**: `010-reservation-selection-plan` | **Date**: 2026-11-24 |
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from
`specs/010-reservation-selection-recency/spec.md`

## Summary

Make the reservation status sensor's selected reservation date-aware. Classify
each reservation left after the existing cancellation filter as past, current,
upcoming, or undated relative to Home Assistant's local date, never select a
past stay, and rank the rest by group, existing status priority, chronology,
and a deterministic tie-break. Compute today in the sensor layer with
`dt_util.now().date()` and pass it to a Home-Assistant-free selection helper,
as feature 009 does for its list filter. Keep coordinator data, cancellation
filtering, `upcoming_reservations`, status priorities, and entity interfaces
unchanged.

## Technical Context

- **Language/Version**: Python 3.14
- **Primary Dependencies**: Home Assistant 2026.11 and standard-library
  `datetime.date`; no new dependency.
- **Storage**: None; classification and ranking are transient.
- **Testing**: pytest with `pytest-homeassistant`; focused helper and sensor
  tests in `tests/sensor/test_reservation.py`, then the full suite.
- **Target Platform**: Home Assistant custom integration (HACS).
- **Project Type**: Single Python custom component.
- **Performance Goals**: One pass over a listing's reservations per
  evaluation with at most two date parses each; no I/O, timers, or sorting of
  the full list.
- **Constraints**: Use Home Assistant's configured local date, not UTC or the
  listing timezone. Accept only canonical `YYYY-MM-DD` dates. Do not change API
  retrieval, models, coordinator contents or order, cancellation handling,
  status priorities, state options, attribute keys, or the 009 list filter.
  Log at most one generic warning per process for undated reservations.
- **Scale/Scope**: One helper rewrite, two sensor call sites, focused tests,
  and design documentation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code quality and TDD**: Add failing helper and sensor tests for every
  group, tie-break, and regression scenario before changing production code.
  Keep public typing and docstrings complete; run Ruff, mypy, interrogate,
  the full test suite, and pre-commit.
- **III. Atomic commits**: Tests and the fix ship together as one bug-fix
  commit; spec status and `tasks.md` completion are separate commits.
- **IV. Licensing**: `specs/**` is covered by `REUSE.toml`; source and test
  files already carry SPDX headers.
- **VII. UX consistency**: Entity names, state options, and attribute keys are
  unchanged. The behavior change (past stays no longer selected, past-only
  listings report `no_reservation`) is documented in the spec's compatibility
  impact and the implementation PR for release notes.
- **VIII. Performance**: In-memory classification only; no API calls,
  coordinator work, or scheduled callbacks.
- **X. Security**: The new warning contains no guest name, reservation ID, or
  date value.

**Gate**: Pass. No new dependencies, persisted data, configuration options,
API contract changes, or constitution exceptions are required.

**Post-design re-check**: Pass. The design below keeps the helper module free
of Home Assistant imports and adds no complexity requiring justification.

## Design

1. **Date parsing**: Generalize the existing canonical-date parser in
   `sensor/helpers.py` (today `_parse_check_out_date`) into one
   `_parse_calendar_date` used for check-in and check-out values. It accepts a
   string only when `date.fromisoformat(value).isoformat() == value`. The 009
   list filter keeps identical behavior by calling the renamed parser.
2. **Classification**: Add a private helper that returns the group for one
   reservation and `today`:
   - past when the parsed check-out date is valid and before `today`;
   - otherwise undated when either date is invalid or check-out is before
     check-in;
   - otherwise current when check-in is on or before `today`;
   - otherwise upcoming.
3. **Selection**: Change `_select_reservation(reservations, today)` to require
   the local date. Enumerate the input, skip past entries, and return the
   minimum of a rank tuple, or `None` when nothing remains:
   - current: `(0, priority, -check_in.toordinal(), reservation.id)`;
   - upcoming: `(1, priority, check_in.toordinal(), reservation.id)`;
   - undated: `(2, priority, 0, input_index)`.
   `priority` is the existing `_STATUS_PRIORITY` value with the existing
   unknown-status fallback. Integer tuples make the order total and
   deterministic.
4. **Warning**: The first time selection classifies a reservation as undated,
   log one generic warning guarded by a new module flag that is separate from
   009's check-out warning flag. The message contains no reservation data and
   does not reuse 009's "invalid check-out date" wording, so the two warnings
   remain distinguishable.
5. **Sensor wiring** (`sensor/reservation.py`):
   - `native_value` calls `_select_reservation(self._reservations,
     dt_util.now().date())`.
   - `extra_state_attributes` reads `dt_util.now().date()` once and passes the
     same value to `_select_reservation` and `_filter_upcoming_reservations`,
     so the selected reservation is always in the upcoming list.
   - Both properties already read the cancellation-filtered
     `_reservations`; that path is unchanged.
6. **No-selection path**: When `_select_reservation` returns `None`, the
   existing `_derive_state` returns `no_reservation` and
   `_build_reservation_attributes` returns its existing null-detail shape.
   No change is needed there.
7. **Timing**: Selection is recomputed on every entity evaluation, including
   each coordinator refresh. No midnight timer is added. State and attributes
   are read microseconds apart during a single state write; a local-midnight
   crossing between those two reads is an accepted, self-correcting race that
   the next refresh resolves, as recorded in the spec's timing edge case and
   FR-010. Guaranteeing one date per write would require hooking Home
   Assistant's private write path or caching selection outside property
   evaluation, which would also freeze the date between coordinator updates;
   see research.md.

### Test Design

- **Helper unit tests** with an explicit `today`, covering: stale `checked_in`
  past vs future `confirmed`; equal-priority past `new` vs future `confirmed`
  in coordinator order; checkout today and zero-night stays as current; a
  current stay beating a better-status upcoming stay; same-day turnovers
  (`checked_in` departing wins, `checked_out` departing loses to `confirmed`
  arriving, `checked_out` departing beats an arriving `inquiry`, equal
  priority picks the later check-in); upcoming status
  priority beating sooner check-in; upcoming equal priority picking the
  sooner check-in; lowest-ID tie-break in both input orders for current and
  upcoming; undated after dated regardless of status; undated-only status
  then input order; inverted range treated as undated; valid past check-out
  with malformed check-in excluded; past-only returning `None`; unknown status
  ranked last within its group; one generic warning without sensitive data.
- **Sensor tests** using `_make_reservation_sensor` and a monkeypatched
  `dt_util.now`: replace `test_past_reservation_still_drives_selected_state`
  with a test asserting the future booking drives state and attributes;
  past-only listing returns `no_reservation` with the existing null-detail
  shape; cancellation filter on and off with a current cancelled stay and an
  upcoming booking; state and attributes agree and the selected ID is in
  `upcoming_reservations`; data changes after a coordinator refresh; a date
  advance past check-out moves selection to the next stay and then to
  `no_reservation`.
- **Existing tests**: Update `TestSelectReservation` calls to pass a `today`
  that keeps their fixture dates current, so expectations are unchanged. Pin
  the Home Assistant date for existing sensor tests whose fixture dates
  (2025-08-01 to 2025-08-05) would otherwise be past. Do not change any other
  expectation.

## Project Structure

### Documentation (this feature)

```text
specs/010-reservation-selection-recency/
├── spec.md
├── checklists/requirements.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md            # Created by the tasks stage
```

No `contracts/` directory is produced: the externally visible contract is the
entity state and attributes, which are unchanged in shape and are specified in
`spec.md`.

### Source Code

```text
custom_components/hostaway/sensor/
├── helpers.py       # Date parser, classification, and date-aware selection
└── reservation.py   # Supplies Home Assistant's local date to selection

tests/sensor/
└── test_reservation.py
```

Other test modules that construct the reservation status sensor
(`tests/test_integration.py`, `tests/test_edge_cases.py`,
`tests/sensor/test_custom_fields.py`) are updated only if their fixture dates
become past under the new rules, by pinning the Home Assistant date.

**Structure Decision**: Extend the existing helper and sensor modules and the
current test module. The change is small and cohesive; no new module is
justified.

## Complexity Tracking

No constitution violations or additional architectural complexity are proposed.
