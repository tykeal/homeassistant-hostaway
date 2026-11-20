<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Upcoming Reservation Date Filter

## Current Behavior

- `HostawayReservationStatusSensor._reservations` reads the listing's
  coordinator records and applies the configured cancellation filter.
- `native_value` and `extra_state_attributes` both select from that same
  cancellation-filtered list using `_select_reservation`.
- `_build_reservation_attributes` currently maps every supplied reservation
  into `upcoming_reservations` without date filtering.
- The reservations coordinator sorts records by `check_in`; list order is
  expected to survive filtering.
- `HostawayReservation.check_out` is a string. `from_api_response` requires a
  nonempty string but does not validate its date format. `parse_reservations`
  skips records rejected by the model, so missing, empty, and non-string API
  values retain their current behavior and are outside this change.
- Existing API fixtures use ISO calendar dates such as `2025-08-05`.

## Decisions

### Date and timezone source

Use `homeassistant.util.dt.now().date()` at the sensor attribute boundary. The
Home Assistant utility uses the configured default timezone when no explicit
zone is passed. The helper receives a `date` argument, which keeps
`sensor/helpers.py` independent of Home Assistant and makes filtering tests
deterministic. Do not use UTC or a listing-specific timezone: the specification
sets the cutoff to Home Assistant's local date.

### Comparison boundary

Treat a reservation as past only if `check_out < today`. Equality is retained:
Hostaway supplies calendar dates, not a checkout instant suitable for
classifying a same-day stay as fully complete. Date-only comparisons are
unaffected by elapsed-hour or DST arithmetic.

### Date parsing and invalid values

Parse the canonical API form `YYYY-MM-DD` with `date.fromisoformat`, and verify
that serializing the result returns the original string. This avoids treating
other accepted ISO forms as the Hostaway API's documented calendar-date shape.
A parse failure is not proof that the reservation is past. Retain the record
and emit one generic warning per process, with no raw date, guest name, or
reservation identifier. Do not change upstream model validation or parser
behavior for missing, empty, or non-string API values.

### Scope boundary

Filter only the list passed to the existing attribute builder. Do not mutate
coordinator records or change the list used for `_select_reservation`. Keep
cancellation filtering in `_reservations` and preserve the existing sorted
order among retained items. The sensor reevaluates this attribute with existing
updates; no midnight timer, API query, or additional coordinator refresh is
needed.

## Alternatives Considered

- Filtering coordinator data is rejected because it could change other
  consumers or sensor state selection.
- Filtering `_reservations` is rejected because both state and selected
  details use that list.
- Comparing checkout timestamps in UTC is rejected because checkout is a date,
  not an instant.
- Dropping bad dates or failing the sensor is rejected because invalid data
  does not prove a stay is past.
- Adding a midnight callback is rejected because existing updates recalculate
  attributes.
- Importing Home Assistant into the helper is rejected; pass the local date in
  and keep the helper independent.

## References in the Repository

- `custom_components/hostaway/sensor/reservation.py`: reservation selection,
  cancellation filtering, and attribute construction call.
- `custom_components/hostaway/sensor/helpers.py`:
  `_build_reservation_attributes` and current list shape.
- `custom_components/hostaway/api/models.py`: required string validation for
  `arrivalDate` and `departureDate`.
- `custom_components/hostaway/api/reservations.py`: malformed API records are
  skipped using the model's existing validation.
- `specs/002-reservation-sensor-refactor/spec.md`: former all-reservations
  requirement, explicitly superseded by feature spec FR-007.
