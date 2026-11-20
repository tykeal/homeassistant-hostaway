<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Filter Past Reservations from Upcoming Attribute

**Feature Branch**: `009-reservation-upcoming-filter`
**Created**: 2026-11-20
**Status**: Implemented
**Input**: User request to omit completely past stays from the reservation
status sensor list.

## User Scenarios & Testing

### User Story 1 - See only current and future stays (Priority: P1)

As a Home Assistant user, I want the reservation status sensor's
`upcoming_reservations` attribute to omit completed stays, so that the list is
useful in automations and dashboards.

**Why this priority**: Past stays are the reported usability problem and make
this attribute difficult to use.

**Independent Test**: Provide a listing's reservations with check-out dates
before, equal to, and after the current Home Assistant local date, then inspect
the attribute.

**Acceptance Scenarios**:

1. **Given** a reservation whose check-out date is before today's local date,
   **When** the sensor attributes are built, **Then** that reservation is
   absent from `upcoming_reservations`.
2. **Given** a reservation whose check-out date is today or later, **When** the
   sensor attributes are built, **Then** it remains in `upcoming_reservations`.
3. **Given** reservations before and after the cutoff, **When** the attribute is
   built, **Then** remaining entries retain their original order and shape.
4. **Given** cancelled-reservation filtering is enabled, **When** the sensor
   attributes are built, **Then** the existing cancellation filtering still
   applies in addition to the past-date filter.

### User Story 2 - Preserve selected reservation (Priority: P1)

As a Home Assistant user, I want hiding completed entries from the list not to
change which reservation drives the sensor's state or selected-reservation
attributes.

**Why this priority**: The attribute cleanup must not alter sensor state
or selected details relied on by automations.

**Independent Test**: Use a past reservation with a higher status priority than
a future reservation and verify the existing state and selected details remain
unchanged while the past reservation is omitted from the list.

**Acceptance Scenarios**:

1. **Given** the same reservation data as before this change, **When** the
   sensor reports its native value and selected reservation attributes,
   **Then** they are determined by the existing selection behavior, regardless
   of whether an entry appears in `upcoming_reservations`.
2. **Given** no reservations or unavailable coordinator data, **When** the
   sensor attributes are built, **Then** the existing empty-list and
   availability behavior is unchanged.

## Edge Cases

- A reservation checking out today is retained. Hostaway provides calendar-date
  values, not a reliable checkout instant for this decision, so the filter
  cannot distinguish earlier from later today.
- An ongoing stay is retained because its check-out date is today or later.
- A same-day (zero-night) stay is retained when its check-out date is today.
- The current date is taken from Home Assistant's configured local timezone,
  not UTC or the Hostaway listing's timezone. Daylight-saving transitions do
  not alter date-only comparisons.
- The expected check-out representation is the API's ISO calendar date
  (`YYYY-MM-DD`). A nonempty string that cannot be parsed as that date cannot
  safely be classified as past; retain the reservation object and emit a
  bounded warning without guest, reservation, or raw date data. Do not make the
  sensor unavailable because one value is malformed. The existing model/parser
  rejects and skips API records with a missing, empty, or non-string
  `departureDate`; this feature does not change that upstream behavior.
- The attribute is recalculated through existing sensor/coordinator updates;
  this change adds no midnight timer, API request, or coordinator data filter.

## Requirements

### Functional Requirements

- **FR-001**: The reservation status sensor MUST exclude from
  `upcoming_reservations` each reservation whose valid check-out calendar date
  is strictly earlier than the current Home Assistant local calendar date.
- **FR-002**: The sensor MUST retain reservations checking out today, ongoing
  reservations, and reservations with a future check-out date.
- **FR-003**: The date filter MUST apply only when building
  `upcoming_reservations`; it MUST NOT change the reservations coordinator's
  data, cancellation filtering, native sensor value, selected reservation, or
  selected-reservation attributes.
- **FR-004**: The sensor MUST preserve the existing list entry fields and
  ordering for retained reservations.
- **FR-005**: A reservation object supplied to attribute construction with
  a check-out value that is missing, malformed, or unsupported MUST be retained
  rather than treated as past, and MUST produce a one-time warning that does
  not disclose guest or reservation data. Such input MUST NOT make the entity
  unavailable or fail attribute construction. Existing upstream validation
  that rejects and skips API records with missing, empty, or non-string
  `departureDate` values MUST remain unchanged.
- **FR-006**: Empty and unavailable coordinator data MUST retain existing
  behavior; no new API calls, persisted state, or date-boundary scheduling may
  be introduced.
- **FR-007**: This specification explicitly supersedes
  `specs/002-reservation-sensor-refactor/spec.md` FR-R05's requirement to list
  all listing reservations. The `upcoming_reservations` contract is now the
  date-filtered list defined above; all other existing reservation sensor
  behavior remains in force.

## Assumptions

- Hostaway reservation check-in and check-out values are expected to be ISO
  calendar-date strings. The current model requires a nonempty string but does
  not validate its date format; the parser skips records rejected by the
  existing model validations.
- Existing coordinator order is authoritative and is preserved after filtering.
- Existing `filter_cancelled` behavior continues to run before attributes are
  assembled.
- “Completely in the past” means check-out calendar date strictly before
  today. A reservation ending today is not completely in the past.
- The request applies only to the `upcoming_reservations` attribute, not API
  retrieval or reservation status selection.

## Success Criteria

### Measurable Outcomes

- **SC-001**: No reservation with a valid check-out date earlier than the
  current Home Assistant local date appears in `upcoming_reservations`.
- **SC-002**: Every reservation with a valid check-out date equal to or later
  than the current local date remains in the list, in its existing relative
  order and with its existing fields.
- **SC-003**: An unparsable check-out date on a reservation object supplied
  to attribute construction remains visible and does not make the sensor
  unavailable; existing parser behavior for invalid API records is unchanged.
- **SC-004**: Existing state selection, selected-reservation attributes,
  cancellation behavior, and unrelated sensor behavior continue to pass their
  regression tests.
