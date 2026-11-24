<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Filter Past Reservations from Upcoming Attribute

> **Superseded in part (issue #300)**: Retention of reservations with
> invalid check-out dates is superseded; invalid-date reservations are now
> dropped at ingest and never reach `upcoming_reservations`. See
> `specs/010-reservation-selection-recency/spec.md` (amendment, FR-015).

**Branch**: `009-reservation-upcoming-plan` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/009-reservation-upcoming-filter/spec.md`

## Summary

Filter only the reservation status sensor's `upcoming_reservations` attribute
by Hostaway check-out date. Exclude a reservation only when its valid ISO
calendar date is earlier than the current Home Assistant local date. Compute
today in the sensor layer with Home Assistant's `dt_util.now().date()` and
pass that value into a Home-Assistant-free helper, retaining today's,
future, and unparsable entries. Keep coordinator data, cancellation filtering,
reservation selection, sensor state, and selected reservation details intact.

## Technical Context

- **Language/Version**: Python 3.14
- **Primary Dependencies**: Home Assistant 2026.9.4 and standard-library
  `datetime.date`; no new dependency.
- **Storage**: None; date classification is transient.
- **Testing**: pytest and pytest-homeassistant-custom-component; focused sensor
  tests plus the full suite.
- **Target Platform**: Home Assistant custom integration (HACS).
- **Project Type**: Single Python custom component.
- **Performance Goals**: One date parse per reservation; no I/O or timers.
- **Constraints**: Compare dates using Home Assistant's configured local date,
  not UTC or listing-local time. Do not change API retrieval, models,
  coordinator contents, cancellation handling, or state selection. Retain
  invalid date strings and log once per process without sensitive values.
  Missing, empty, and non-string API fields keep existing model/parser behavior.
- **Scale/Scope**: One filter helper, the reservation attribute path, focused
  tests in `tests/sensor/test_reservation.py`, and design documentation.

## Constitution Check

- Code-level TDD: add focused failing tests for date boundaries, malformed values,
  state preservation, and local-time behavior before implementing the helper.
- Quality: keep public typing and docstrings complete; run focused tests, the
  complete test suite, Ruff, mypy, interrogate, and pre-commit hooks.
- Compatibility: preserve existing entity attributes and state selection except
  for excluding completed records from the upcoming list.
- Performance: perform only in-memory date parsing; add no coordinator or API work.
- Licensing: use existing `REUSE.toml` coverage for `specs/**`; modify only
  already-licensed source and test files.

**Gate**: Pass. The approach requires no new dependencies, persistent data,
configuration changes, API contract changes, or justified constitution
exceptions.

## Design

1. In `sensor/reservation.py`, obtain the local date with
   `homeassistant.util.dt.now().date()` when the extra attributes are built.
2. Pass the reservations already selected by the existing
   `filter_cancelled` option and that local date to a small helper in
   `sensor/helpers.py`. Keep Home Assistant imports out of the helper module.
3. The helper accepts only canonical `YYYY-MM-DD` dates (parse with
   `date.fromisoformat` and require `parsed.isoformat() == input`). Preserve
   order and entries with checkout date equal to or later than today.
4. If parsing fails, retain the entry and emit one generic warning per process;
   do not include the date, reservation id, guest name, or other reservation
   details in the message. Do not repeatedly log the same bad upstream value.
5. Pass the filtered list to the existing attribute builder. Continue choosing
   the selected reservation from the unfiltered `_reservations` list so the
   native value and selected details do not change.
6. Recalculate on existing sensor/coordinator attribute evaluations only. Do
   not add a midnight scheduler or filter coordinator/API data.

### Test Design

- Freeze the HA time provider in tests to deterministic aware datetimes.
- Cover strictly-past exclusion; checkout-today, same-day, and future retention;
  order and entry-shape preservation; and empty input.
- Cover a malformed nonempty string: retain it, warn once, and do not leak guest,
  reservation, or date values into logs.
- At the sensor level, cover a UTC/local-date boundary and a daylight-saving
  transition with Home Assistant's local time zone.
- Verify existing cancellation filtering and that a past high-priority
  reservation can still drive sensor state and selected attributes.
- Preserve existing API model/parser validation for absent, empty, and
  non-string `departureDate` values.

## Project Structure

### Documentation (this feature)

```text
specs/009-reservation-upcoming-filter/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md
```

### Source Code

```text
custom_components/hostaway/sensor/
├── helpers.py       # Date-only filter; remains independent of Home Assistant
└── reservation.py   # Supplies Home Assistant's local date at attribute time

tests/sensor/
└── test_reservation.py
```

**Structure Decision**: Extend the existing reservation sensor and helper
modules and their current test module. No package or module split is justified
for this narrowly scoped behavior.

## Complexity Tracking

No constitution violations or additional architectural complexity are proposed.
