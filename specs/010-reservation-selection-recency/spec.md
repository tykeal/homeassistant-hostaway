<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Select Current or Upcoming Reservations

**Feature Branch**: `010-reservation-selection-recency`
**Created**: 2026-11-24
**Status**: Implemented
**Input**: Issue #295, "Investigate past reservations driving selected sensor
state", plus maintainer analysis and confirmed design decisions.

## Background

The reservation status sensor chooses one reservation per listing. That
reservation drives the sensor state and the selected-reservation attributes
(`reservation_id`, `guest_name`, `check_in`, `check_out`, `status`, door code
details, `num_guests`, `confirmation_code`, and `custom_fields`). Today the
choice is made only by reservation status priority, with no regard to dates.
Feature 009 removed completed stays from the separate `upcoming_reservations`
attribute but deliberately left this selection unchanged.

The listing's reservation data contains the listing's full history, ordered by
check-in date. When several reservations share the best status priority, the
oldest of them is chosen. Hostaway commonly leaves completed stays in `new` or
`modified` status, which rank the same as `confirmed`. On a listing with any
such history, the sensor therefore likely reports `awaiting_checkin` with the
details of the oldest past reservation, and a past stay left in `checked_in`
outranks every current or future booking. This analysis is based on code
review and fixture tests; it has not been verified against live Hostaway data.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Current or upcoming stay drives the sensor (Priority: P1)

As a Home Assistant user, I want the reservation status sensor and its
selected-reservation attributes to describe the stay that is in progress, or
the next stay, rather than a completed stay whose status Hostaway never
advanced.

**Why this priority**: This is the reported defect. Automations that set door
codes, prepare the property, or notify cleaners act on the selected
reservation; acting on a completed stay is wrong and may be harmful.

**Independent Test**: Provide a listing with a completed reservation in a
high-priority status and a future booking, fix the Home Assistant local date,
and verify that the state and selected attributes describe the future booking.

**Acceptance Scenarios**:

1. **Given** today is Aug 5, a reservation Aug 1-4 still in `checked_in`, and a
   `confirmed` booking Aug 10-12, **When** the sensor is evaluated, **Then** the
   state is `awaiting_checkin` and the selected attributes describe the Aug 10
   booking.
2. **Given** today is Aug 5, a reservation Jul 1-4 in `new` and a `confirmed`
   booking Aug 10-12, **When** the sensor is evaluated, **Then** the Aug 10
   booking is selected even though both have the same status priority and the
   older stay appears first in the data.
3. **Given** a stay in progress (check-in on or before today, check-out on or
   after today) and a future booking with a better status, **When** the sensor
   is evaluated, **Then** the stay in progress is selected.
4. **Given** a stay whose check-out date is today, **When** the sensor is
   evaluated, **Then** that stay is treated as in progress and remains eligible.
5. **Given** any data and a local date, **When** the sensor reports its state
   and attributes for that date, **Then** the state and every
   selected-reservation attribute describe the same reservation, and that
   reservation is also present in `upcoming_reservations`.

### User Story 2 - Past-only listing reports no reservation (Priority: P1)

As a Home Assistant user, I want a listing whose remaining reservations are
all completed to report `no_reservation`, so that automations do not act on
stale guests, door codes, or custom-field values.

**Why this priority**: Showing a completed guest as the selected reservation
is the same defect as User Story 1 for listings with no future bookings.

**Independent Test**: Provide only reservations whose check-out dates are
before today and verify the state and selected attributes.

**Acceptance Scenarios**:

1. **Given** every remaining reservation has a valid check-out date before
   today, **When** the sensor is evaluated, **Then** the state is
   `no_reservation`, every selected-reservation detail attribute is null,
   `custom_fields` is empty, `listing_id` is present, and
   `upcoming_reservations` is empty.
2. **Given** the only non-past reservation is cancelled and cancellation
   filtering is enabled, **When** the sensor is evaluated, **Then** the state is
   `no_reservation`.
3. **Given** the same data with cancellation filtering disabled, **When** the
   sensor is evaluated, **Then** the non-past cancelled reservation is selected
   and the state is `cancelled`.

### User Story 3 - Deterministic choice among candidates (Priority: P2)

As a Home Assistant user, I want a predictable choice when several eligible
reservations compete, including same-day turnovers, equal statuses, and
reservations whose dates cannot be read, so that the sensor does not change
between refreshes when the data has not changed.

**Why this priority**: Ambiguous ties cause state flapping and make
automations hard to reason about, but they are less common than the primary
defect.

**Independent Test**: Provide competing current, upcoming, and undated
reservations with equal and unequal statuses, in varying input orders, and
verify the selected reservation.

**Acceptance Scenarios**:

1. **Given** a same-day turnover where the departing guest is `checked_in` and
   the arriving booking is `confirmed`, **When** the sensor is evaluated,
   **Then** the departing stay is selected (state `checked_in`).
2. **Given** a same-day turnover where the departing stay is `checked_out` and
   the arriving booking is `confirmed`, or where both stays have the same
   status priority, **When** the sensor is evaluated, **Then** the arriving
   stay is selected. An arriving stay whose status ranks below `checked_out`
   (for example, an inquiry) does not win, because status priority decides
   first.
3. **Given** two upcoming bookings with the same status priority, **When** the
   sensor is evaluated, **Then** the one with the earlier check-in is selected.
4. **Given** two upcoming bookings with different status priorities, **When**
   the sensor is evaluated, **Then** the better status wins even if its
   check-in is later.
5. **Given** candidates tied on bucket, status priority, and check-in date,
   **When** the sensor is evaluated in any input order, **Then** the
   reservation with the lowest reservation ID is selected.
6. **Given** a reservation with a missing, malformed, or inverted date range
   and a current or upcoming reservation with valid dates, **When** the sensor
   is evaluated, **Then** the reservation with valid dates is selected
   regardless of status priority.
7. **Given** only reservations with unusable dates, **When** the sensor is
   evaluated, **Then** the best status priority is selected and input order
   breaks ties; selection logs its generic undated-reservation warning at most
   once per process, independent of feature 009's check-out warning.

### User Story 4 - Selection follows refreshes and the calendar (Priority: P2)

As a Home Assistant user, I want the selected reservation to change when the
reservation data changes or the local date advances, without new API traffic,
and without changing anything else about the integration.

**Why this priority**: Correct selection must remain correct over time; the
rest of the integration's behavior must not regress.

**Independent Test**: Evaluate the sensor, then change coordinator data or the
Home Assistant clock and evaluate again.

**Acceptance Scenarios**:

1. **Given** a current stay is selected, **When** the local date advances past
   its check-out date and the sensor is next evaluated, **Then** the next
   eligible reservation, or `no_reservation`, is reported.
2. **Given** a coordinator refresh adds, removes, or changes reservations,
   **When** the sensor is next evaluated, **Then** the selection reflects the
   refreshed data.
3. **Given** any reservation data, **When** the sensor is evaluated, **Then**
   the contents, order, and entry shape of `upcoming_reservations`, the
   coordinator data and its order, services, entity identifiers, the state
   option list, and attribute keys are unchanged from feature 009 behavior.

## Edge Cases

- **Checkout today**: A valid stay whose check-out date equals today is in
  progress, consistent with feature 009's list filter. Hostaway supplies
  calendar dates, not checkout instants.
- **Zero-night stay**: A stay whose check-in and check-out dates are both
  today is in progress.
- **Same-day turnover**: Two stays are both in progress. Status priority
  decides first, so a departing guest still marked `checked_in` is reported
  until Hostaway marks the stay `checked_out`. When status priorities tie, the
  later check-in (the arriving stay) wins, because the departing stay is
  ending that day.
- **Stale statuses on in-progress or upcoming stays**: Status priority is not
  reinterpreted from dates. A `checked_out` stay with check-out today ranks
  below an arriving `confirmed` stay by status priority. A `checked_in` status
  on an upcoming stay keeps its existing priority within the upcoming group.
- **Cancelled, declined, and expired statuses**: Their inclusion remains
  governed solely by the existing `filter_cancelled` option (enabled by
  default). When that option is disabled, such a reservation participates
  like any other: if its dates make it current, it outranks every upcoming
  reservation; within its group it keeps its existing low status priority.
- **Unknown statuses**: Continue to rank after all known statuses within their
  date group and continue to report `unknown` with the existing one-time
  warning per status.
- **Missing or malformed dates**: A date value is usable only when it is a
  canonical `YYYY-MM-DD` calendar date. The existing API parser already skips
  records with missing, empty, or non-string dates; this feature handles any
  that still reach the sensor without raising or becoming unavailable.
- **Inverted range**: A reservation whose check-out date is before its check-in
  date is treated as having unusable dates, unless its valid check-out date is
  before today, in which case it is past.
- **Past determination uses check-out only**: A reservation with a valid
  check-out date before today is past, whatever its check-in value. This
  matches feature 009, so the selected reservation always appears in
  `upcoming_reservations`.
- **Date context**: Today is the Home Assistant local calendar date, the same
  basis as feature 009. The Hostaway listing's own timezone is out of scope;
  near midnight, a Home Assistant instance in a different timezone from the
  property may move a stay between groups a few hours earlier or later.
- **Timing**: The state is recalculated whenever Home Assistant evaluates the
  entity, including each coordinator refresh. No midnight timer or extra API
  request is added, so a date-boundary change is reflected on the next
  refresh (by default within two minutes). Home Assistant reads the state and
  the attributes separately, microseconds apart; if local midnight falls
  between those two reads, that single write can pair the old date's state
  with the new date's details. This window is accepted because it is
  vanishingly small, requires no new scheduling or Home Assistant internals,
  and the next refresh rewrites a consistent state.
- **Unavailable data**: When coordinator data is unavailable, availability and
  attribute behavior are unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The sensor MUST determine "today" as the current Home Assistant
  local calendar date each time the selection is evaluated.
- **FR-002**: The sensor MUST classify each reservation remaining after the
  existing cancellation filter into exactly one group:
  1. **Past**: its check-out date is a valid date strictly before today.
  2. **Current**: not past; both dates are valid; check-in is on or before
     today; check-out is on or after check-in.
  3. **Upcoming**: not past; both dates are valid; check-in is after today;
     check-out is on or after check-in.
  4. **Undated**: not past, and any date is missing or not a canonical
     `YYYY-MM-DD` calendar date, or check-out is before check-in.
- **FR-003**: The sensor MUST never select a past reservation.
- **FR-004**: The sensor MUST select a current reservation when one exists,
  otherwise an upcoming reservation when one exists, otherwise an undated
  reservation when one exists, otherwise no reservation.
- **FR-005**: Within the current group, the sensor MUST rank by existing status
  priority, then by the later check-in date, then by the lowest reservation ID.
- **FR-006**: Within the upcoming group, the sensor MUST rank by existing status
  priority, then by the earlier check-in date, then by the lowest reservation
  ID.
- **FR-007**: Within the undated group, the sensor MUST rank by existing status
  priority, then by existing input order.
- **FR-008**: Existing status priorities, the unknown-status fallback, the
  status-to-state mapping, and the state option list MUST NOT change.
- **FR-009**: When no reservation is selected, the sensor MUST report
  `no_reservation` and use the existing no-selection attribute shape: all
  selected-reservation detail attributes null, `custom_fields` empty, and
  `listing_id` and `upcoming_reservations` present.
- **FR-010**: For a given local date, the sensor state and all
  selected-reservation attributes MUST be derived from the same selection, and
  a selected reservation MUST also appear in `upcoming_reservations` computed
  for that date.
- **FR-011**: The existing `filter_cancelled` option MUST continue to govern
  whether cancelled, declined, and expired reservations are considered, and
  MUST be applied before classification.
- **FR-012**: The first time selection encounters an undated reservation in a
  process, the sensor MUST log one generic warning that contains no guest
  name, reservation ID, date value, or other reservation data. An undated
  reservation MUST NOT make the entity unavailable or fail evaluation.
- **FR-013**: The feature MUST NOT change coordinator data or its sort order,
  API requests, services, entity identifiers, attribute keys, or the contents,
  order, entry shape, and filtering semantics of `upcoming_reservations`
  defined by feature 009. It MUST NOT add timers, persisted state, or API
  calls.
- **FR-014**: This specification explicitly supersedes
  `specs/002-reservation-sensor-refactor/spec.md` FR-R03 (selection by status
  priority alone) and the selection-preservation portions of
  `specs/009-reservation-upcoming-filter/spec.md`: User Story 2, FR-003's
  clause that state selection and selected attributes remain unchanged,
  SC-004's preservation of existing state selection and selected attributes,
  and the assumption excluding reservation status selection from scope. All
  other requirements of those specifications remain in force.

### Key Entities

- **Reservation**: An existing Hostaway reservation record with an ID,
  check-in date, check-out date, status, guest, and access details. This
  feature reads dates and status only; it does not add or alter fields.
- **Date group**: A transient classification (past, current, upcoming,
  undated) of a reservation relative to today, used only for selection.
- **Selected reservation**: The single reservation, or none, that drives the
  sensor state and selected-reservation attributes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In every regression scenario, no reservation whose check-out date
  is before the Home Assistant local date drives the sensor state or selected
  attributes.
- **SC-002**: When at least one current or upcoming reservation exists, one of
  them is selected in 100% of regression scenarios, including stale
  `checked_in` and equal-priority `new`/`confirmed` histories.
- **SC-003**: A listing whose remaining reservations are all past reports
  `no_reservation` with null selected details.
- **SC-004**: For identical data and date, the selected reservation is the same
  for every input order of tied candidates, except within the undated group
  where input order is the documented tie-break.
- **SC-005**: Existing `upcoming_reservations`, cancellation-filter,
  availability, and unrelated sensor regression tests continue to pass with
  their expected outcomes unchanged. Tests whose fixture dates are now in the
  past may pin the Home Assistant date or use current dates; only the test
  that asserted the old past-selection behavior changes its expectation.

## Compatibility Impact

- Listings with only completed reservations change from reporting a stale
  reservation's state and details to `no_reservation` with null details.
  Automations that read `reservation_id`, door code, guest, or custom-field
  attributes must already handle the existing no-reservation shape.
- A completed stay left in `checked_in`, `new`, `modified`, or `confirmed` no
  longer holds the state; automations may see transitions to the next
  booking's state on the first evaluation after this change and after each
  check-out date passes.
- `checked_out` is reported only for a non-past stay that has no
  better-ranked competitor.
- No entity, attribute key, option, or service changes are made.

## Assumptions

- The maintainer analysis that Hostaway commonly leaves completed stays in
  `new` or `modified` status is unverified against live data. The rules above
  are correct whether or not that pattern occurs.
- Stays are calendar-date ranges; check-in and check-out times are not
  considered.
- Status priority remains the primary signal within a date group because it
  encodes operational state (for example, a departing guest still
  `checked_in`). Dates are used only to exclude past stays and to order
  groups and ties.
- The lowest reservation ID is used as the final tie-break for dated
  reservations because it does not depend on API response order, avoiding
  state flapping between refreshes. Undated reservations keep input order, as
  they have no reliable chronology.
- When `filter_cancelled` is disabled, a current cancelled reservation
  outranks an upcoming booking. This is the conservative reading of the
  group-first rule and preserves the user's explicit choice to see
  cancellations; enabling the default filter avoids it.
- A separate one-time warning for undated reservations is acceptable in
  addition to feature 009's one-time check-out warning; neither repeats.
- The README does not describe reservation selection rules, so no README
  change is required by this feature.
