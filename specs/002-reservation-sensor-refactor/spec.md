<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Spec: Reservation Sensor Refactor

## Problem

The current implementation creates a sensor entity per
reservation. Completed/cancelled reservations leave stale
unavailable entities that accumulate indefinitely. This
diverges from the Guesty sister integration's proven
per-listing pattern.

## Solution

Replace per-reservation sensors with a single
reservation-status sensor per listing. The sensor uses
priority-based selection to show the most relevant
reservation's status and details.

## User Story

As a property manager, I want one reservation sensor per
listing that shows the current occupancy status so I can
trigger automations without accumulating stale entities.

### Acceptance Scenarios

1. **Given** a listing with a checked-in reservation,
   **When** data is polled,
   **Then** the reservation sensor state is "checked_in"
   and attributes show guest details and door code.
2. **Given** a listing with no reservations,
   **When** data is polled,
   **Then** the reservation sensor state is
   "no_reservation" with null attributes.
3. **Given** a listing with multiple reservations,
   **When** data is polled,
   **Then** the sensor selects the highest-priority
   reservation (checked_in > confirmed > checked_out >
   cancelled) and lists all reservations in
   upcoming_reservations.
4. **Given** a reservation completes (checked_out),
   **When** no other reservations exist,
   **Then** sensor transitions to "no_reservation"
   without leaving stale entities.

## Functional Requirements

- **FR-R01**: System MUST create exactly one
  reservation-status sensor per monitored listing.
- **FR-R02**: Sensor state MUST be an enum: checked_in,
  awaiting_checkin (maps from confirmed), checked_out,
  cancelled, no_reservation. Unknown statuses pass through.
- **FR-R03**: Sensor MUST select the highest-priority
  active reservation using priority order: checked_in >
  confirmed > checked_out > cancelled.
- **FR-R04**: Sensor extra_state_attributes MUST include:
  reservation_id, guest_name, check_in, check_out,
  status, door_code, door_code_vendor,
  door_code_instruction, num_guests, confirmation_code,
  listing_id, upcoming_reservations.
- **FR-R05**: upcoming_reservations attribute MUST list
  all reservations for the listing sorted by check_in,
  each with: id, guest_name, check_in, check_out,
  status.
- **FR-R06**: unique_id MUST be
  `{entry_unique_id}_{listing_id}_reservation_status`
  (stable across restarts).
- **FR-R07**: Sensor MUST use SensorDeviceClass.ENUM.
- **FR-R08**: The per-reservation HostawayReservationSensor
  class and its dynamic entity discovery
  (_async_add_new_reservations) MUST be removed.
- **FR-R09**: The hostaway.set_door_code and
  hostaway.get_reservations services MUST continue to
  work unchanged.

## Supersedes

This spec supersedes the per-reservation sensor design
from spec 001 (US4 acceptance scenarios 1-3, FR-009).
