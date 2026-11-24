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
or upcoming relative to Home Assistant's local date, never select a past
reservation, and rank the rest by group, existing status
priority, chronology, and a deterministic tie-break. Compute today in the
sensor layer with `dt_util.now().date()` and pass it to a Home-Assistant-free
selection helper, as feature 009 does for its list filter. Keep coordinator
data, cancellation filtering, status priorities, and entity interfaces
unchanged.

**Amendment (issue #300)**: The original design ranked reservations with
invalid dates ("undated") after all dated candidates, and feature 009 retained
invalid check-out dates in `upcoming_reservations`. Invalid dates are now
rejected when an API record is parsed into a `HostawayReservation`; list
parsing drops just that record through the existing skip path, logging only
IDs and a generic reason. The sensor's undated group, 009's retention rule,
and both invalid-date warnings are removed as dead code. The design below is
written in its amended form.

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
  retrieval, coordinator contents or order, cancellation handling, status
  priorities, state options, attribute keys, or the 009 list filter's order
  and past semantics. Invalid-date reservations are rejected at ingest by the
  reservation model (amendment), not in the sensor. Ingest logs carry at most
  reservation and listing IDs and a generic reason.
- **Scale/Scope**: One helper rewrite, two sensor call sites, focused tests,
  and design documentation. The amendment adds reservation-model date
  validation, a shared date parser, a tightened skip log, and service error
  translation.

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
- **X. Security**: Ingest validation errors and skip logs contain no guest
  name, raw date value, or other payload content; only reservation and
  listing IDs and the failed field name.

**Gate**: Pass. No new dependencies, persisted data, configuration options,
API contract changes, or constitution exceptions are required.

**Post-design re-check**: Pass. The design below keeps the helper module free
of Home Assistant imports and adds no complexity requiring justification.

## Design

1. **Date parsing**: Move the canonical-date parser out of
   `sensor/helpers.py` into a small shared module,
   `custom_components/hostaway/api/dates.py`, as
   `parse_calendar_date(value) -> date | None`. It accepts a string only when
   `date.fromisoformat(value).isoformat() == value`.
2. **Ingest validation** (`api/models.py`): `HostawayReservation` validates
   its dates in `__post_init__`, so every construction path, including
   `from_api_response` and `dataclasses.replace`, rejects invalid dates with
   `ValueError`. `check_in` and `check_out` must parse with
   `parse_calendar_date`, and `check_out` must not precede `check_in`. Equal
   dates (zero-night stays) are valid; the optional `nights` value is not
   cross-checked. Messages name only the field and a generic reason (for
   example, `check_in must be a YYYY-MM-DD date`). Validating in the
   constructor makes valid dates an invariant of the type, so downstream code
   needs no defensive handling.
3. **List ingest** (`api/reservations.py`): `parse_reservations` already
   catches `ValueError` per record and calls `_log_skipped_reservation`, so a
   bad record is dropped without failing the page. Tighten the log to include
   the reservation ID only when it is a positive integer (otherwise it is
   omitted), alongside the listing ID and the generic exception message.
   `reservation_page_cursor` also logs the raw last ID with `%r` when a full
   page ends in a non-integer ID; that value can be arbitrary payload text,
   so the warning drops it and states only that the last record's ID is
   invalid.
4. **Single-reservation reads** (`client.get_reservation` and the
   custom-field services): the `ValueError` propagates to the service, which
   reports `Unable to read reservation <id>: <reason>` as a
   `ServiceValidationError`. `_read_target_snapshot` (the pre-write and
   read-back path) already translates `ValueError`, so a failed pre-write read
   means no write is sent. `_read_target_collection` (the get-values service)
   and the currently unused `_read_target_model` catch only
   `HostawayApiError`; add `ValueError` to both so the rejection is reported
   as a `ServiceValidationError` rather than an unhandled error.
5. **Sensor selection and list filter** (`sensor/helpers.py`): Classify each
   reservation as past (check-out before `today`), current (check-in on or
   before `today`), or upcoming, parsing with `date.fromisoformat` because the
   model guarantees canonical dates. `_select_reservation(reservations,
   today)` skips past entries and returns the minimum rank tuple, or `None`:
   - current: `(0, priority, -check_in.toordinal(), reservation.id)`;
   - upcoming: `(1, priority, check_in.toordinal(), reservation.id)`.
   `priority` is the existing `_STATUS_PRIORITY` value with the existing
   unknown-status fallback. `_filter_upcoming_reservations` keeps entries
   whose check-out is on or after `today`, in input order. The undated rank,
   009's invalid check-out retention, and both warning flags are removed.
6. **Sensor wiring** (`sensor/reservation.py`):
   - `native_value` calls `_select_reservation(self._reservations,
     dt_util.now().date())`.
   - `extra_state_attributes` reads `dt_util.now().date()` once and passes the
     same value to `_select_reservation` and `_filter_upcoming_reservations`,
     so the selected reservation is always in the upcoming list.
   - Both properties already read the cancellation-filtered
     `_reservations`; that path is unchanged.
7. **No-selection path**: When `_select_reservation` returns `None`, the
   existing `_derive_state` returns `no_reservation` and
   `_build_reservation_attributes` returns its existing null-detail shape.
   No change is needed there.
8. **Timing**: Selection is recomputed on every entity evaluation, including
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
  upcoming; past-only returning `None`; unknown status ranked last within its
  group.
- **Ingest tests (amendment, issue #300)**: `from_api_response` and direct
  construction reject unparsable, non-canonical (for example `2025-8-1` or
  a timestamp), empty, and missing dates for each field, and check-out before
  check-in, with messages that omit the raw value; equal dates are accepted.
  A reservation page containing one invalid-date record returns the valid
  records, and the warning contains the reservation and listing IDs but not
  the bad value or guest name. A custom-field service reading an invalid-date
  reservation fails with `ServiceValidationError` and sends no write.
- **Sensor tests** using `_make_reservation_sensor` and a monkeypatched
  `dt_util.now`: replace `test_past_reservation_still_drives_selected_state`
  with a test asserting the future booking drives state and attributes;
  past-only listing returns `no_reservation` with the existing null-detail
  shape; cancellation filter on and off with a current cancelled stay and an
  upcoming booking, and with a cancelled stay as the only non-past
  reservation; state and attributes agree and the selected ID is in
  `upcoming_reservations`; data changes after a coordinator refresh; a date
  advance past check-out moves selection to the next stay and then to
  `no_reservation`.
- **Existing tests**: Update `TestSelectReservation` calls to pass a `today`
  that keeps their fixture dates current, so expectations are unchanged. Pin
  the Home Assistant date for existing sensor tests whose fixture dates
  (2025-08-01 to 2025-08-05) would otherwise be past. Remove the 009 test
  that asserted invalid check-out retention and the 010 tests that asserted
  undated selection, since such reservations can no longer be constructed;
  cover the behavior with the ingest tests above. Existing fixtures that set
  only a later check-in over a default `2025-08-05` check-out (for example in
  `tests/test_coordinator.py` and the upcoming-order tests in
  `tests/sensor/test_reservation.py`) build inverted ranges that construction
  now rejects; give them a check-out on or after check-in without changing
  their assertions. Do not change any other expectation.

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
custom_components/hostaway/
├── api/
│   ├── dates.py         # Shared canonical calendar-date parser (amendment)
│   ├── models.py        # Reservation date validation at construction
│   └── reservations.py  # Per-record skip log with IDs only
├── services/
│   └── custom_fields.py # Translate invalid single reads to service errors
└── sensor/
    ├── helpers.py       # Classification and date-aware selection
    └── reservation.py   # Supplies Home Assistant's local date to selection

tests/
├── api/                 # Model and reservation-parsing ingest tests
├── sensor/test_reservation.py
└── services/            # Custom-field read of an invalid reservation
```

Other test modules that construct the reservation status sensor
(`tests/test_integration.py`, `tests/test_edge_cases.py`,
`tests/sensor/test_custom_fields.py`) are updated only if their fixture dates
become past under the new rules, by pinning the Home Assistant date.

**Structure Decision**: Extend the existing helper and sensor modules and the
current test module. The amendment adds one small `api/dates.py` module so
the canonical-date rule lives in the API layer, where validation now happens,
without growing `models.py` or making the API layer import sensor code. The
sensor no longer needs the rule because dates are canonical by
construction.

## Complexity Tracking

No constitution violations or additional architectural complexity are proposed.
