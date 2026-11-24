<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Select Current or Upcoming Reservations

**Feature Branch**: `010-reservation-selection-recency`
**Created**: 2026-11-24
**Status**: Implemented; amended 2026-11-24 (issue #300)
**Input**: Issue #295, "Investigate past reservations driving selected sensor
state", plus maintainer analysis and confirmed design decisions.

## Amendment: Drop Invalid-Date Reservations at Ingest (Issue #300)

After this feature shipped, the maintainer decided that "a reservation with
missing or invalid dates is... invalid, it should not be anything we ever
surface / use," and that "we should drop bad data during ingest and log
identifying information at most about it." This amendment replaces the
original last-resort "undated" selection group:

- A reservation's dates are **invalid** when its check-in (`arrivalDate`) or
  check-out (`departureDate`) is missing or not a canonical `YYYY-MM-DD`
  calendar date, or when its check-out date is before its check-in date.
- Invalid reservations are rejected when the API response is parsed into a
  reservation object. In reservation lists, the bad record is dropped and the
  rest of the page is kept; the log names at most the reservation ID, the
  listing ID, and a generic reason, never raw date values, guest data, or
  other payload content.
- Because invalid reservations never reach the sensor, they are never
  selected and never appear in `upcoming_reservations`. When only past
  reservations remain, the sensor reports `no_reservation` with the existing
  null-detail attributes.
- The sensor's original undated group and undated warning, and feature 009's
  retention of unparsable check-out dates with its warning, are removed.

The requirements below are written in their amended form; the original
undated design is preserved in git history (PRs #296-#299).

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
4. **Given** a listing whose API data contains only past reservations and
   reservations with invalid dates, **When** the data is fetched and the
   sensor is evaluated, **Then** the invalid records are dropped at ingest and
   the state is `no_reservation` with the same null-detail attributes and an
   empty `upcoming_reservations`.

### User Story 3 - Deterministic choice among candidates (Priority: P2)

As a Home Assistant user, I want a predictable choice when several eligible
reservations compete, including same-day turnovers and equal statuses, and I
want reservations whose dates cannot be trusted to be dropped, so that the
sensor does not change between refreshes when the data has not changed.

**Why this priority**: Ambiguous ties cause state flapping and make
automations hard to reason about, but they are less common than the primary
defect.

**Independent Test**: Provide competing current and upcoming reservations
with equal and unequal statuses, in varying input orders, and verify the
selected reservation; parse API records with invalid dates and verify that
they are dropped.

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
6. **Given** an API page containing a reservation with a missing, malformed,
   or inverted date range and a reservation with valid dates, **When** the
   page is parsed, **Then** only the valid reservation is returned, the
   invalid record is logged with at most its reservation ID, listing ID, and
   a generic reason, and the sensor never selects or lists the invalid
   record whatever its status.
7. **Given** a listing whose API data contains only reservations with
   invalid dates, **When** the data is fetched and the sensor is evaluated,
   **Then** the state is `no_reservation` and `upcoming_reservations` is
   empty.

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
   the order and entry shape of `upcoming_reservations`, its past and
   checkout-today semantics, the coordinator data and its order, services,
   entity identifiers, the state option list, and attribute keys are unchanged
   from feature 009 behavior; only invalid-date reservations, now dropped at
   ingest, no longer appear.

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
- **Missing or malformed dates**: A date value is valid only when it is a
  canonical `YYYY-MM-DD` calendar date. A reservation is invalid when either
  date is missing or not valid, or when its check-out date is before its
  check-in date (an inverted range). Invalid reservations are rejected when
  parsed, including one with a valid past check-out and a malformed check-in.
  The parser already skipped records with missing, empty, or non-string
  dates; it now also skips non-canonical and inverted dates.
- **Zero-night stays at ingest**: Equal check-in and check-out dates are
  valid. The optional `nights` value is not cross-checked against the dates;
  it is already validated as non-negative and is not used for selection.
- **Ingest logging**: A dropped list record is logged by the existing skip
  path with at most its reservation ID (only when it is a positive integer),
  the listing ID, and a generic reason naming the failed field. Raw date
  values, guest names, and other payload content are never logged, including
  by the pagination cursor warning for a page whose last record has an
  invalid ID, which reports only that the ID is invalid. The skip
  path logs each time a refresh parses the record, as it already does for
  other malformed records.
- **Single-reservation reads**: Services that read one reservation by explicit
  ID (the custom-field read and write services) receive the same rejection.
  The service fails with a clear error naming the reservation and the generic
  reason, and no write is attempted when the pre-write read fails.
- **Past determination**: A reservation is past when its check-out date is
  before today. Feature 009's list filter and selection share this rule, so
  the selected reservation always appears in `upcoming_reservations`.
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
  1. **Past**: its check-out date is strictly before today.
  2. **Current**: not past; check-in is on or before today.
  3. **Upcoming**: not past; check-in is after today.
  Every reservation reaching the sensor has valid dates (FR-015).
- **FR-003**: The sensor MUST never select a past reservation.
- **FR-004**: The sensor MUST select a current reservation when one exists,
  otherwise an upcoming reservation when one exists, otherwise no reservation.
- **FR-005**: Within the current group, the sensor MUST rank by existing status
  priority, then by the later check-in date, then by the lowest reservation ID.
- **FR-006**: Within the upcoming group, the sensor MUST rank by existing status
  priority, then by the earlier check-in date, then by the lowest reservation
  ID.
- **FR-007**: `upcoming_reservations` MUST contain exactly the current and
  upcoming reservations, in existing coordinator order.
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
- **FR-012**: The sensor MUST NOT contain a selection group, retention rule,
  or warning for invalid-date reservations; invalid data is handled only at
  ingest (FR-015, FR-016).
- **FR-013**: Apart from dropping invalid-date reservations at ingest
  (FR-015 to FR-017), the feature MUST NOT change coordinator data or its
  sort order, API requests, services, entity identifiers, attribute keys, or
  the order, entry shape, and past and checkout-today semantics of
  `upcoming_reservations` defined by feature 009. It MUST NOT add timers,
  persisted state, or API calls.
- **FR-014**: This specification explicitly supersedes
  `specs/002-reservation-sensor-refactor/spec.md` FR-R03 (selection by status
  priority alone) and the following portions of
  `specs/009-reservation-upcoming-filter/spec.md`: User Story 2, FR-003's
  clause that state selection and selected attributes remain unchanged,
  SC-004's preservation of existing state selection and selected attributes,
  the assumption excluding reservation status selection from scope, and (by
  the issue #300 amendment) the edge case, FR-005, and SC-003 rules that
  retain reservations with unparsable check-out dates. All other
  requirements of those specifications remain in force.
- **FR-015**: Parsing a Hostaway reservation MUST fail with a validation error
  when `arrivalDate` or `departureDate` is missing or not a canonical
  `YYYY-MM-DD` calendar date, or when `departureDate` is before
  `arrivalDate`. Equal dates are valid. The error message MUST name only the
  failed field and a generic reason, never the raw value.
- **FR-016**: When a reservation list page contains a record that fails
  FR-015, only that record MUST be dropped; the remaining records MUST be
  returned and the fetch MUST NOT fail. The log entry MUST contain at most the
  reservation ID (when it is a positive integer), the listing ID, and the
  generic reason, and MUST NOT contain raw date values, guest names, or other
  payload content. No other reservation ingest warning, including the
  pagination cursor warning, may log a raw non-integer ID value.
- **FR-017**: A service that reads a single reservation by explicit ID MUST
  report a reservation that fails FR-015 as a clear service validation error
  naming the reservation ID and the generic reason, and MUST NOT write to it.

### Key Entities

- **Reservation**: An existing Hostaway reservation record with an ID,
  check-in date, check-out date, status, guest, and access details. This
  feature reads dates and status only and adds no fields; the amendment
  requires valid dates for a reservation to be parsed at all.
- **Date group**: A transient classification (past, current, upcoming) of a
  reservation relative to today, used for selection and for
  `upcoming_reservations`.
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
  for every input order of tied candidates.
- **SC-005**: Existing `upcoming_reservations`, cancellation-filter,
  availability, and unrelated sensor regression tests continue to pass with
  their expected outcomes unchanged. Tests whose fixture dates are now in the
  past may pin the Home Assistant date or use current dates. Only tests that
  asserted past-reservation selection, invalid-date selection, or retention of
  invalid check-out dates in `upcoming_reservations` change their
  expectations.
- **SC-006**: Every invalid-date case (unparsable, non-canonical, empty,
  missing, and check-out before check-in) is rejected at parse time; a page
  containing one such record still returns its valid records; the log names
  only IDs and a generic reason; and a listing whose API data has only invalid
  and/or past reservations reports `no_reservation`.

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
- Reservations with invalid dates are dropped at ingest, so they no longer
  drive the state or appear in `upcoming_reservations`. A listing whose only
  non-past data has invalid dates reports `no_reservation` instead of that
  reservation's details. Custom-field services addressing such a reservation
  by ID fail with a clear validation error.
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
- The lowest reservation ID is used as the final tie-break because it does
  not depend on API response order, avoiding state flapping between
  refreshes.
- When `filter_cancelled` is disabled, a current cancelled reservation
  outranks an upcoming booking. This is the conservative reading of the
  group-first rule and preserves the user's explicit choice to see
  cancellations; enabling the default filter avoids it.
- A reservation with invalid dates cannot be placed in time and is treated as
  invalid data, per the maintainer's decision in issue #300. Dropping it at
  ingest is preferred over surfacing guest, door-code, or custom-field
  details that may be stale or wrong, and keeps the sensor free of
  invalid-data handling.
- The existing per-record skip log is sufficient for dropped records; it
  carries identifiers only and needs no new rate limiting.
- The README does not describe reservation selection rules, so no README
  change is required by this feature.
