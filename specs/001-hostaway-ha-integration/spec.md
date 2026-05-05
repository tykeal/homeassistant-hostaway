# Feature Specification: Hostaway Home Assistant Integration

**Feature Branch**: `001-hostaway-ha-integration`
**Created**: 2025-07-14
**Status**: Draft
**Input**: User description: "Create a Home Assistant custom
integration for the Hostaway property management platform API"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automate Door Code Assignment for Check-ins (Priority: P1)

As a property manager using Home Assistant, I want to
automatically update door codes on Hostaway reservations so that
guests receive unique access codes without manual intervention.

**Why this priority**: This is the PRIMARY use case driving the
integration. Automating door code assignment eliminates manual
work, reduces errors, and ensures guests always have working
access codes upon check-in.

**Independent Test**: Can be fully tested by calling the
set_door_code service with a reservation ID and verifying the
door code is updated in Hostaway. Delivers immediate automation
value for the core workflow.

**Acceptance Scenarios**:

1. **Given** a valid reservation exists in Hostaway,
   **When** the user calls `hostaway.set_door_code` with a
   reservation ID, door code, vendor, and instruction,
   **Then** the reservation's doorCode, doorCodeVendor, and
   doorCodeInstruction fields are updated via the Hostaway API.
2. **Given** a reservation ID that does not exist,
   **When** the user calls `hostaway.set_door_code`,
   **Then** the service returns a clear error indicating the
   reservation was not found.
3. **Given** the Hostaway API rate limit has been exceeded,
   **When** the user calls `hostaway.set_door_code`,
   **Then** the system retries with appropriate backoff and
   eventually succeeds or reports a clear rate-limit error.
4. **Given** the access token has expired,
   **When** the user calls `hostaway.set_door_code`,
   **Then** the system transparently refreshes the token and
   completes the request.

---

### User Story 2 - Configure Integration via Home Assistant UI (Priority: P2)

As a Home Assistant user, I want to set up the Hostaway
integration through the standard UI config flow by providing my
Hostaway credentials and selecting which listings to monitor.

**Why this priority**: Foundation for all other features. Without
authentication and configuration, no other functionality works.

**Independent Test**: Can be tested by going through the HA
config flow, entering credentials, verifying successful
authentication against the Hostaway API, and selecting listings
to monitor.

**Acceptance Scenarios**:

1. **Given** the user initiates adding the Hostaway integration,
   **When** they enter a valid client_id (account ID) and
   client_secret,
   **Then** the system authenticates successfully and proceeds
   to listing selection.
2. **Given** invalid credentials are entered,
   **When** the user submits the form,
   **Then** a clear error message is displayed and the user can
   retry.
3. **Given** successful authentication,
   **When** the listing selection step is displayed,
   **Then** all active listings from the account are shown as
   selectable options.
4. **Given** the user selects one or more listings,
   **When** they complete the config flow,
   **Then** the integration is set up and begins monitoring only
   the selected listings.

---

### User Story 3 - Monitor Listing Status and Occupancy (Priority: P3)

As a property manager, I want to see the current status of my
listings (active/inactive, occupancy, pricing) as sensors in
Home Assistant so I can build dashboards and automations based
on property state.

**Why this priority**: Provides visibility into property
portfolio state, enabling dashboards and conditional automations
(e.g., notify if a listing goes inactive).

**Independent Test**: Can be tested by verifying that after
setup, sensor entities appear for each selected listing with
current data from the Hostaway API.

**Acceptance Scenarios**:

1. **Given** the integration is configured with selected
   listings,
   **When** data is polled from Hostaway,
   **Then** sensor entities are created for each listing with
   attributes including name, status, and pricing.
2. **Given** a listing's status changes in Hostaway,
   **When** the next poll occurs,
   **Then** the corresponding sensor entity reflects the
   updated status.
3. **Given** the Hostaway API is temporarily unavailable,
   **When** a poll fails,
   **Then** the sensors retain their last known values and the
   coordinator retries on the next interval.

---

### User Story 4 - Monitor Reservations for Selected Listings (Priority: P4)

As a property manager, I want to see upcoming and current
reservations as sensors in Home Assistant so I can trigger
automations based on guest arrivals, departures, and
reservation status changes.

**Why this priority**: Reservations data enables automation
triggers (check-in/check-out events) and provides the
reservation IDs needed for the door code service.

**Independent Test**: Can be tested by verifying reservation
sensors appear with guest name, check-in/check-out dates,
status, and door code information after the integration polls
reservation data.

**Acceptance Scenarios**:

1. **Given** the integration is monitoring listings with active
   reservations,
   **When** data is polled,
   **Then** reservation sensors are created showing guest name,
   dates, status, and door code fields.
2. **Given** a new reservation is created in Hostaway,
   **When** the next poll occurs,
   **Then** a new sensor entity appears for that reservation.
3. **Given** a reservation's status changes (e.g.,
   confirmed → checked-in),
   **When** the next poll occurs,
   **Then** the sensor reflects the updated status.

---

### User Story 5 - Retrieve Reservations for a Listing On-Demand (Priority: P5)

As a Home Assistant automation developer, I want to fire an
event containing all reservations for a specific listing so I
can process reservation data in scripts and automations.

**Why this priority**: Supports advanced automation use cases
where users need programmatic access to reservation data beyond
what sensors provide.

**Independent Test**: Can be tested by calling
`hostaway.get_reservations` with a listing ID and verifying an
event is fired containing the reservation data.

**Acceptance Scenarios**:

1. **Given** a valid listing ID,
   **When** the user calls `hostaway.get_reservations`,
   **Then** an event is fired containing all reservations for
   that listing.
2. **Given** a listing with no reservations,
   **When** the service is called,
   **Then** an event is fired with an empty reservation list.

---

### Edge Cases

- What happens when the Hostaway API returns a 429 rate limit
  response? The system must implement exponential backoff with
  retry.
- What happens when the access token is invalid or expired? The
  system must automatically obtain a new token.
- What happens when a listing is deleted in Hostaway but still
  configured? The sensor should become unavailable with an
  appropriate message.
- What happens when the 1-second post-token-generation wait is
  not respected? The system must enforce the required delay
  before first API use after token generation.
- What happens when a reservation spans multiple listings? Each
  listing's reservation sensor set operates independently.
- What happens when pagination is required for large
  reservation sets? The system must handle cursor-based
  pagination to retrieve all results.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST authenticate with the Hostaway API
  using OAuth 2.0 Client Credentials Grant (client_id =
  account ID, client_secret, scope = general).
- **FR-002**: System MUST cache the access token and only
  refresh when expired or invalidated (token validity:
  24 months).
- **FR-003**: System MUST wait at least 1 second after
  generating a new token before making any API calls.
- **FR-004**: System MUST provide a multi-step config flow:
  credentials entry → listing selection.
- **FR-005**: System MUST respect Hostaway API rate limits
  (15 requests/10s per IP, 20 requests/10s per account) with
  appropriate throttling and backoff.
- **FR-006**: System MUST handle HTTP 429 responses with
  exponential backoff retry.
- **FR-007**: System MUST expose listing data as sensor
  entities following the pattern
  `sensor.hostaway_<listing_name>_<attribute>`.
- **FR-008**: System MUST poll listing data periodically and
  update sensor state accordingly.
- **FR-009**: System MUST expose reservation data as sensor
  entities for monitored listings.
- **FR-010**: System MUST poll reservation data periodically
  and update sensor state accordingly.
- **FR-011**: System MUST handle Hostaway API pagination
  (cursor-based with afterId for reservations) to retrieve
  complete data sets.
- **FR-012**: System MUST provide a
  `hostaway.set_door_code` service that updates doorCode,
  doorCodeVendor, and doorCodeInstruction fields on a
  specified reservation.
- **FR-013**: System MUST provide a
  `hostaway.get_reservations` service that fires an event
  with reservation data for a specified listing.
- **FR-014**: System MUST validate service call parameters and
  return clear error messages for invalid inputs.
- **FR-015**: System MUST gracefully handle API unavailability
  by retaining last known sensor values and retrying on the
  next poll interval.

### Key Entities

- **Listing**: Represents a property in the Hostaway platform.
  Key attributes: ID, name, status, pricing, occupancy
  information, address.
- **Reservation**: Represents a guest booking. Key attributes:
  ID, listing ID, guest name, check-in date, check-out date,
  status, door code, door code vendor, door code instruction,
  number of guests.
- **Access Token**: Authentication credential for the Hostaway
  API. Key attributes: token value, expiration timestamp,
  generation timestamp (for 1-second delay enforcement).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can complete integration setup
  (authentication + listing selection) in under 2 minutes.
- **SC-002**: Door code updates via the service call are
  reflected in Hostaway within 5 seconds of invocation
  (excluding rate limit retries).
- **SC-003**: Listing and reservation sensor data is refreshed
  within the configured polling interval (default: 5 minutes
  for listings, 2 minutes for reservations).
- **SC-004**: The integration handles 429 rate limit responses
  without crashing and successfully retries within 30 seconds.
- **SC-005**: All sensor entities display accurate, current
  data matching the Hostaway platform state after each
  successful poll.
- **SC-006**: The integration operates continuously for 30+
  days without requiring manual re-authentication or restart
  due to token issues.
- **SC-007**: Property managers can eliminate manual door code
  entry for 100% of reservations by using the set_door_code
  service in automations.

## Assumptions

- Users have an active Hostaway account with API access enabled
  (client_id and client_secret available).
- The Home Assistant instance has stable internet connectivity
  to reach the Hostaway API.
- Users manage a reasonable number of listings (under 100) and
  reservations that can be polled within rate limits.
- The Hostaway API documentation at
  <https://api.hostaway.com/documentation>
  accurately reflects current API behavior.
- Python 3.14+ and Home Assistant 2026.4.0+ are the minimum
  supported versions (per project constitution).
- The integration follows project-mandated architectural
  patterns (httpx client, Pydantic models,
  DataUpdateCoordinators) as established by the constitution
  and the Guesty sister project.
- Polling is sufficient for the initial release; webhook
  support for real-time updates is a future enhancement.
- Only the doorCode, doorCodeVendor, and doorCodeInstruction
  fields need write access on reservations; no other
  reservation fields require modification.
- Entity naming uses slugified listing names (lowercase,
  underscores replacing spaces/special characters).
