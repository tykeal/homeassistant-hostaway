<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Current or Upcoming Reservation Selection

## Current Behavior

- `HostawayReservationsCoordinator` fetches every reservation for each selected
  listing without a date filter and sorts each list by the `check_in` string.
- `HostawayReservationStatusSensor._reservations` returns that list, minus
  cancelled, declined, and expired records when `filter_cancelled` is enabled
  (the default).
- `native_value` and `extra_state_attributes` both call
  `_select_reservation(self._reservations)`, which is
  `min(reservations, key=status priority)`. `min` returns the first of equal
  keys, so ties go to the earliest check-in: the oldest reservation.
- `_STATUS_PRIORITY` ranks `checked_in` (0) first, then `confirmed`, `new`,
  and `modified` (1), down to inquiry statuses (8-9) and `unknown` (10);
  unlisted statuses fall back after all known ones.
- Feature 009's `_filter_upcoming_reservations` removes valid check-out dates
  before today from `upcoming_reservations` only, retaining checkout-today and
  invalid dates with a one-time generic warning. `_parse_check_out_date`
  accepts only canonical `YYYY-MM-DD` strings.
- With no selection, `_derive_state` returns `no_reservation` and
  `_build_reservation_attributes` returns null detail fields, empty
  `custom_fields`, `listing_id`, and the upcoming list.
- The API model requires nonempty string `arrivalDate` and `departureDate`;
  the parser skips records that fail. Formats are not validated, so malformed
  strings can reach the sensor.

## Decisions

### Date source

- **Decision**: `homeassistant.util.dt.now().date()`, read in the sensor and
  passed to the helper.
- **Rationale**: Same basis as feature 009, so list filtering and selection
  agree. Passing a `date` keeps `sensor/helpers.py` free of Home Assistant and
  makes tests deterministic.
- **Alternatives considered**: UTC date (wrong near midnight for most users);
  the listing's timezone (not consistently available on the reservation
  model and would diverge from 009; recorded as out of scope).

### Group-first ranking

- **Decision**: Rank current, then upcoming, then undated; never select past.
  Status priority orders reservations within a group.
- **Rationale**: Dates decide relevance; status still carries operational
  meaning such as a departing guest who has not checked out. Keeping
  `_STATUS_PRIORITY` unchanged within groups minimizes behavior change.
- **Alternatives considered**: Pure chronological selection (drops the
  operational signal of `checked_in`); status first with dates as tie-break
  only (still selects stale `checked_in` past stays); falling back to the most
  recent past stay when nothing else exists (rejected by the confirmed design:
  stale guest details are worse than `no_reservation`).

### Past determination

- **Decision**: A reservation is past when its valid check-out date is before
  today, regardless of its check-in value.
- **Rationale**: Matches feature 009 exactly, so every selectable reservation
  is also in `upcoming_reservations`. A valid past check-out proves the stay is
  over even if check-in is malformed.
- **Alternatives considered**: Requiring both dates to be valid before calling
  a stay past (would let a malformed check-in resurrect a finished stay and
  break the list/selection invariant).

### Validity and undated reservations

- **Decision**: Current or upcoming requires both dates to be canonical and
  check-out on or after check-in. Anything else that is not past is undated
  and ranks after all dated candidates by status priority, then input order.
- **Rationale**: An inverted or unreadable range cannot be placed in time.
  Keeping it eligible avoids hiding a possibly real booking when it is the
  only data, but it must not displace a reservation with trustworthy dates.
  Input order is the only stable signal left; it is the coordinator order.
- **Alternatives considered**: Excluding undated reservations (could hide
  real bookings and produce `no_reservation` incorrectly); ranking them by
  status alongside dated reservations (reintroduces the defect when a stale
  record has a malformed date).

### Tie-breaks

- **Decision**: Current ties prefer the later check-in; upcoming ties prefer
  the earlier check-in; the final tie-break for dated reservations is the
  lowest reservation ID.
- **Rationale**: On a same-day turnover with equal status priority, the
  departing stay ends today and the arriving stay is the one about to matter.
  For upcoming stays, the soonest arrival is the next actionable booking.
  Reservation IDs are integers independent of API response order, so the
  choice cannot flap between refreshes even if the API reorders equal
  check-in records.
- **Alternatives considered**: Input order as the final dated tie-break
  (depends on API order for equal check-in strings); earliest check-in for
  current ties (would keep the departing guest on turnover days when statuses
  tie).

### Cancelled and checked-out statuses

- **Decision**: No special handling. `filter_cancelled` still decides whether
  cancelled, declined, and expired records are considered; status priority
  orders whatever remains within each group.
- **Rationale**: The confirmed design keeps existing semantics unless clearly
  wrong. With the filter enabled (the default), cancelled records never
  compete. With it disabled the user has chosen to see cancellations, and a
  cancelled stay spanning today is relevant current information. A
  `checked_out` stay whose check-out is today is current and loses to an
  arriving `confirmed` stay by priority, which is the desired turnover result.
- **Alternatives considered**: Demoting inactive statuses below all active
  upcoming stays (adds a new ranking concept and silently overrides the
  user's filter choice).

### Warning behavior

- **Decision**: One generic warning per process the first time selection sees
  an undated reservation, using a flag separate from 009's.
- **Rationale**: Consistent with 009 (bounded, no personal data). A separate
  flag reports malformed check-in values and inverted ranges that 009 never
  sees, while keeping 009's own warning behavior and tests unchanged.
- **Alternatives considered**: Reusing 009's flag (would hide one of the two
  conditions depending on evaluation order); per-reservation warnings (log
  spam on every refresh).

### Recalculation

- **Decision**: No timer; rely on existing entity evaluations.
- **Rationale**: The coordinator refreshes every two minutes by default, which
  rewrites state. A midnight callback would add complexity for at most one
  refresh interval of staleness.

## References in the Repository

- `custom_components/hostaway/sensor/helpers.py`: `_STATUS_PRIORITY`,
  `_select_reservation`, `_parse_check_out_date`,
  `_filter_upcoming_reservations`, `_build_reservation_attributes`.
- `custom_components/hostaway/sensor/reservation.py`: `_reservations`,
  `native_value`, `extra_state_attributes`.
- `custom_components/hostaway/coordinator.py`: per-listing fetch and
  `check_in` sort.
- `custom_components/hostaway/api/models.py`: required date strings.
- `tests/sensor/test_reservation.py`: existing selection, state, and 009
  filter tests, including the superseded
  `test_past_reservation_still_drives_selected_state`.
