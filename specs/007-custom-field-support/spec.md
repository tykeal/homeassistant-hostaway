# Feature Specification: Custom Variable (Custom Field) Support

**Feature Branch**: `007-custom-field-support`
**Created**: 2026-09-21
**Status**: Draft
**Input**: User description: "Add the ability to read and write Hostaway custom
fields (custom variables) for both listings and reservations (issue #195)."

## Overview

Hostaway lets an account define **custom fields** — shown in the Hostaway
dashboard as *custom variables* — on listings and on reservations. Each
definition has a human name, a machine `varName`, a numeric id, a type
(`text`, `textarea`, `number`, `dropdown`), an optional set of possible values,
and a public/hidden flag. Property managers use them to carry operational data
that Hostaway has no built-in field for: gate codes, parking bay numbers, pet
policies, cleaner notes, linen counts, and similar.

The integration today has **no** support for them. Values are never requested
from Hostaway, never parsed, never surfaced, and never writable. This feature
closes that gap for both listings and reservations, in both directions.

### Built-in fields are not custom variables

Hostaway also has **built-in** first-class fields such as `doorCode`,
`doorCodeVendor`, and `doorCodeInstruction`. The existing
`hostaway.set_door_code` service writes those built-in fields. They are *not*
custom variables, they are not part of the custom field definition list, and
they are not affected by this feature. This distinction MUST be documented so
the two are never conflated again.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read custom variables from sensors (Priority: P1)

As a property manager using Home Assistant, I want the custom variables my team
maintains in Hostaway to appear on dedicated listing custom-variable sensors and
on reservation sensors, so that dashboards and templates can display
operational data (parking bay, pet policy, cleaner notes) without any extra
configuration.

**Why this priority**: Reading is the foundational capability — nothing else in
this feature is useful without values arriving in Home Assistant. It delivers
standalone value even if no write surface ever ships.

**Independent Test**: Configure an account with at least one listing custom
field and one reservation custom field populated, let the integration poll, and
confirm the values appear as attributes on the dedicated listing custom-variable
sensor and the corresponding reservation sensor with names and values matching
the Hostaway dashboard.

**Acceptance Scenarios**:

1. **Given** a listing with populated custom variables, **When** the listings
   poll completes, **Then** those variables are exposed as attributes on a
   dedicated listing custom-variable sensor, keyed by their `varName`, with the
   human-readable field name also available.
2. **Given** a reservation with populated custom variables, **When** the
   reservations poll completes, **Then** those variables are exposed as
   attributes on that listing's reservation sensor for the selected
   reservation.
3. **Given** a custom field defined with `isPublic=0` (hidden), **When** the
   poll completes, **Then** its value is still exposed — hidden fields are
   included in reads.
4. **Given** a listing or reservation with no custom field values, **When** the
   poll completes, **Then** the custom-variable attribute surface exposes an
   empty collection rather than failing or omitting the attribute.
5. **Given** a custom variable's value is changed in the Hostaway dashboard,
   **When** the next scheduled poll runs, **Then** the sensor attribute
   reflects the new value without a Home Assistant restart.

---

### User Story 2 - Read custom variables from a service (Priority: P1)

As an automation author, I want a response-returning service that returns the
custom variables for a given listing or reservation, so that an automation can
branch on a value without needing it pre-rendered onto an entity attribute.

**Why this priority**: Service reads cover the cases sensor attributes cannot —
listings or reservations that are not the currently selected one, and
automations that need a structured payload including each field's type and
allowed values.

**Independent Test**: Call the read service from Developer Tools → Actions with
a known listing id and confirm the response contains every custom field for that
object with its id, `varName`, display name, type, and current value.

**Acceptance Scenarios**:

1. **Given** a valid listing id, **When** the read service is called, **Then**
   the response lists that listing's custom variables with `customFieldId`,
   `varName`, display name, type, possible values (for dropdowns), and current
   value.
2. **Given** a valid reservation id, **When** the read service is called,
   **Then** the response lists that reservation's custom variables in the same
   shape.
3. **Given** a defined custom field that has no value set on the object,
   **When** the read service is called, **Then** the field is still present in
   the response with an empty/unset value, so automations can see the field
   exists.
4. **Given** an id that does not exist or is not accessible, **When** the read
   service is called, **Then** the call fails with a clear, actionable error
   naming the object and id.

---

### User Story 3 - Write without clobbering others (Priority: P1)

As an automation author, I want a service that sets one or more custom variables
on a listing or reservation, so that Home Assistant can push operational state
back into Hostaway — and I need certainty that normal writes do not erase the
other values read immediately before the write or any unrelated listing data.

**Why this priority**: Write is the other half of the requested capability, and
the clobber risk makes it the highest-stakes part of the feature: a careless
write destroys live property data.

**Independent Test**: On a listing with three populated custom variables, call
the write service for one of them, then re-read the listing and confirm the
target changed and the other two — plus the listing's built-in fields — are
untouched.

**Acceptance Scenarios**:

1. **Given** a listing with multiple populated custom variables, **When** the
   write service sets exactly one of them, **Then** the other custom variables
   retain their previous values.
2. **Given** a listing with populated built-in fields (name, price, bedrooms,
   door code, etc.), **When** the write service sets a custom variable,
   **Then** no built-in listing field is changed.
3. **Given** a reservation, **When** the write service sets a custom variable,
   **Then** the change is visible in Hostaway and other reservation fields
   (including `doorCode`) are unchanged.
4. **Given** a successful write, **When** the write completes, **Then** the new
   value is reflected in the corresponding sensor attribute without waiting for
   the next scheduled poll.
5. **Given** the write request fails at the Hostaway API, **When** the service
   returns, **Then** it raises a clear error and no partial local state is
   presented as successful.

---

### User Story 4 - Address fields by name or by id (Priority: P2)

As an automation author, I want to identify a custom field either by its
`varName` or by its numeric `customFieldId`, so that I can write readable
automations without hard-coding opaque numbers, while still having the id
available when names are ambiguous or change.

**Why this priority**: Improves usability of Stories 2 and 3 substantially but
those stories remain functional with a single addressing mode.

**Independent Test**: Perform the same write twice — once addressed by
`varName`, once by `customFieldId` — and confirm both reach the same field.

**Acceptance Scenarios**:

1. **Given** a write request that names a field by `varName`, **When** the
   service runs, **Then** the name is resolved to the correct `customFieldId`
   and the correct field is affected.
2. **Given** a write request that names a field by `customFieldId`, **When**
   the service runs, **Then** the id is used directly.
3. **Given** a request that supplies neither `varName` nor `customFieldId`, or
   supplies both with conflicting targets, **When** the service runs, **Then**
   it fails validation with a message explaining the correct usage.
4. **Given** a `varName` that matches no definition for the object type,
   **When** the service runs, **Then** it fails with an error naming the
   unknown field and it does not write anything.

---

### User Story 5 - Understand built-in vs custom fields (Priority: P3)

As a user of the integration, I want documentation that clearly separates
Hostaway built-in fields from custom variables, so that I do not try to set
`doorCode` through the custom variable service or expect a custom variable to
appear in a built-in field service.

**Why this priority**: Prevents a recurring class of user confusion and support
load, but does not block any functional capability.

**Independent Test**: Review the service documentation and confirm both the
custom variable services and `set_door_code` explicitly state which category of
field they operate on and cross-reference each other.

**Acceptance Scenarios**:

1. **Given** the integration documentation, **When** a user reads the custom
   variable service descriptions, **Then** they state that built-in fields such
   as `doorCode` are not custom variables and point to `set_door_code`.
2. **Given** the `set_door_code` documentation, **When** a user reads it,
   **Then** it states that it writes built-in reservation fields, not custom
   variables.

---

### Edge Cases

- **Value with no matching definition**: a `customFieldId` appears in an
  object's values but is absent from the fetched definitions (newly created, or
  deleted after caching). The value MUST still be surfaced, under a stable
  synthetic key derived from the id, and MUST NOT cause the refresh to fail.
- **Definition with no value**: a field is defined for the object type but the
  object carries no value. Reads surface the field with an unset value.
- **Stale cached definitions**: a field created in the dashboard after the
  definitions were cached. Definitions refresh after a fixed one-hour cache
  lifetime and once when a value or requested field cannot be resolved from the
  cached definitions, so recovery does not require a Home Assistant restart.
- **Malformed value record**: an entry in `customFieldValues` missing
  `customFieldId`, or with a non-integer id, or otherwise unparsable. The entry
  is skipped with a warning; the rest of the object parses normally and the
  coordinator refresh completes. This mirrors the existing `parse_reservations`
  behaviour introduced after a single malformed record crashed an entire
  refresh.
- **Malformed definition record**: an entry in the definitions response that
  cannot be parsed is skipped with a warning; remaining definitions are usable.
- **Dropdown value not in `possibleValues`**: a write requests a value the
  definition does not allow.
- **Type mismatch on write**: a non-numeric value written to a `number` field.
- **Duplicate `varName`**: two definitions share a `varName` across different
  object types (e.g. one listing field and one reservation field with the same
  name). Resolution MUST be scoped by object type so the correct field is
  chosen.
- **Task-type definitions present**: the account defines `objectType: task`
  custom fields. They MUST be ignored entirely by this feature.
- **Partial-payload rejection**: Hostaway turns out to treat
  `PUT /v1/listings/{id}` as a full replacement and drops omitted custom values.
  Listing writes MUST remain unavailable until they can be performed without
  silently destroying data.
- **Concurrent external edit**: a user changes the same Hostaway object in the
  dashboard after Home Assistant reads current custom values but before the
  write reaches Hostaway. This feature only guarantees preservation of values
  visible to the write before it sends the update, unless Hostaway provides a
  conflict-detection mechanism that can make concurrent edits detectable.
- **Empty account**: an account with no custom fields defined at all. Reads
  return empty collections, writes fail cleanly, nothing errors on startup.
- **Rate limiting**: Hostaway permits 200 requests per 10 seconds per account
  and per IP. Larger responses and definition lookups must not push the
  integration toward that ceiling.
- **Value clearing**: a user wants to set a custom variable to empty. Clearing
  must be expressible and distinguishable from "leave unchanged".
- **Repeated definition misses**: one stale-definition miss during a refresh
  cycle MUST trigger at most one definitions refresh for the whole cycle. It
  MUST NOT refresh once per object or field in a tight loop.

---

## Requirements *(mandatory)*

### Functional Requirements

#### Definitions

- **FR-001**: The integration MUST be able to retrieve Hostaway custom field
  **definitions** (id, name, `varName`, type, possible values, object type,
  public flag, sort order) for the configured account.
- **FR-002**: The integration MUST retrieve all definitions across pagination,
  not just the first page.
- **FR-003**: The integration MUST use definitions only for `listing` and
  `reservation` object types, and MUST ignore `task` definitions.
- **FR-004**: The integration MUST NOT create, update, or delete custom field
  definitions. Definition management remains dashboard-only.
- **FR-005**: The integration MUST cache definitions rather than refetching them
  for every read or write operation, to limit API call volume against the rate
  limit.
- **FR-006**: The cached definitions MUST be refreshable without restarting
  Home Assistant so that fields created in the dashboard become usable.
  Definitions MUST refresh after a fixed one-hour cache lifetime and on a cache
  miss. A cache miss means encountering a `customFieldId` present in values but
  absent from cached definitions, or a `varName` that does not resolve. On a
  miss, definitions refresh once and resolution is retried before the field is
  treated as unresolvable. This feature MUST NOT add a user-invokable
  definitions refresh service.
- **FR-007**: A failure to retrieve definitions MUST NOT prevent listing or
  reservation data from loading; the integration degrades to surfacing values
  by numeric id.

#### Reading values

- **FR-008**: Listing and reservation retrieval MUST request Hostaway's extra
  resources so that `customFieldValues` is populated rather than returned empty.
- **FR-009**: The listing model MUST carry parsed custom field values.
- **FR-010**: The reservation model MUST carry parsed custom field values.
- **FR-011**: Custom variables for each listing MUST be exposed as attributes
  on one new dedicated listing custom-variables sensor for that listing. The
  existing diagnostic listing sensors (`listing_id`, `external_name`, `status`,
  `base_price`, `bedrooms`, `bathrooms`, and `max_guests`) MUST remain
  unchanged and MUST NOT gain custom-variable attributes.
- **FR-012**: Custom variables MUST be exposed as attributes on the existing
  reservation sensor, for the reservation that sensor currently represents.
  Existing reservation attributes MUST remain unchanged except for adding the
  custom-variable collection.
- **FR-013**: Reads MUST include fields flagged hidden (`isPublic=0`).
- **FR-014**: Sensor attributes MUST expose one custom-variable collection. The
  collection MUST be keyed by `varName` where a definition is available, with
  the human-readable field name also discoverable.
- **FR-015**: A value whose `customFieldId` has no matching definition MUST
  still be surfaced, under a stable key derived from the id, and MUST be clearly
  distinguishable from a resolved field.
- **FR-016**: The integration MUST NOT create a separate entity per custom
  field. Per-field dynamic entities are out of scope.
- **FR-017**: Parsing of custom field values MUST NOT be able to fail a listing
  or reservation coordinator refresh. Malformed entries are skipped with a
  logged warning and the remainder of the object is used.

#### Read service

- **FR-018**: The integration MUST provide a response-returning service that
  returns the custom variables for a specified listing or reservation.
- **FR-019**: The read service response MUST include, per resolved field:
  `customFieldId`, `varName`, display name, type, possible values when the type
  is `dropdown`, and the current value. Unresolved fields MUST remain in the
  response with the numeric id, current value, and a clear unresolved indicator,
  while unavailable definition metadata is omitted or empty.
- **FR-020**: The read service MUST include defined fields that currently have
  no value, so automations can discover the available field set.
- **FR-021**: The read service MUST return a clear, actionable error when the
  referenced listing or reservation does not exist or is inaccessible.

#### Write service

- **FR-022**: The integration MUST provide a service that sets custom variable
  values on a specified listing or reservation.
- **FR-023**: The write service MUST accept one or more field/value pairs in a
  single call.
- **FR-024**: The write service MUST accept a field addressed by either
  `varName` or `customFieldId`.
- **FR-025**: The write service MUST reject a request that identifies no field,
  or that identifies a field that cannot be resolved for the target object type,
  and MUST make no change when it does so.
- **FR-026**: The write MUST preserve every custom variable the caller did not
  name — setting one variable MUST NOT clear the others. Writes MUST be based on
  current values read before the update and submit a merged set.
- **FR-027**: The write MUST NOT modify any built-in field of the target
  listing or reservation.
- **FR-028**: Before the merge strategy is relied upon, it MUST be explicitly
  verified against a real Hostaway listing that a partial payload to
  `PUT /v1/listings/{id}` does not clear unrelated listing data. The existing
  `update_reservation` partial payload (`{"doorCode": ...}`) is supporting
  evidence but is not verification for the listing endpoint. If that
  verification fails, listing writes MUST remain unavailable until a safe
  payload or endpoint is identified.
- **FR-029**: The write service MUST validate submitted values against the
  field's declared type where practical: `number` fields accept numeric values;
  `dropdown` fields accept only values present in `possibleValues`.
- **FR-030**: A validation failure MUST reject the whole call with an
  explanatory error and MUST NOT write any of the requested fields.
- **FR-031**: The write service MUST support clearing a custom variable to an
  empty value, distinguishably from omitting it.
- **FR-032**: After a successful write, the affected sensor attributes MUST
  reflect the new value without waiting for the next scheduled poll.
- **FR-033**: The write service MUST NOT create writable entities (text, number,
  or select) for custom variables. Service-only is the write surface for this
  feature.
- **FR-034**: Hostaway API errors during a write MUST surface as clear,
  actionable Home Assistant errors, consistent with existing services.

#### Documentation

- **FR-035**: User-facing service documentation MUST describe each new service,
  its fields, and its response shape.
- **FR-036**: Documentation MUST explicitly distinguish Hostaway **built-in**
  fields (such as `doorCode`, written by `hostaway.set_door_code`) from
  **custom variables**, and cross-reference the two so they are not conflated.
- **FR-037**: Documentation MUST state that hidden (`isPublic=0`) fields are
  included in reads, and that task-type custom fields are unsupported.

#### Operational constraints

- **FR-038**: The added request volume (larger listing/reservation responses
  plus definition lookups) MUST stay within Hostaway's published limit of 200
  requests per 10 seconds per account and per IP under normal polling.
- **FR-039**: Definition lookups MUST NOT be performed per listing, per
  reservation, or per field during a poll.
- **FR-040**: Refresh-on-miss MUST NOT become an amplification vector. One
  definitions refresh MUST serve the whole refresh cycle instead of triggering
  repeated refreshes per object or per field.

### Key Entities

- **Custom Field Definition**: The account-level description of a custom
  variable. Identified by a numeric id and a machine-readable `varName`. Carries
  a display name, an object type (`listing`, `reservation`, or `task`), a type
  (`text`, `textarea`, `number`, `dropdown`), optional possible values for
  dropdowns, a public/hidden flag, and a sort order. Read-only to this feature.
- **Custom Field Value**: The value of one custom field on one listing or one
  reservation. Identified by the definition's numeric id plus the owning object.
  Meaningful to a user only when joined to its definition.
- **Listing Custom-Variables Sensor**: New per-listing sensor whose attributes
  carry the listing's custom variable collection without changing existing
  diagnostic listing sensors.
- **Reservation**: Existing entity, extended to carry a collection of custom
  field values.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of a listing's or reservation's non-task custom variables —
  hidden fields included — are visible in Home Assistant within one poll
  interval of being set in the Hostaway dashboard.
- **SC-002**: Setting a single custom variable via the write service leaves
  100% of that object's other custom variables and all of its built-in fields
  unchanged, verified against a real listing carrying at least three populated
  custom variables.
- **SC-003**: An automation author can read a named custom variable and act on
  it from the documented service response using only `varName`, without knowing
  any numeric id.
- **SC-004**: A malformed or unrecognised custom field record never prevents a
  listing or reservation refresh from completing; the remaining objects and
  fields are still delivered.
- **SC-005**: A write with an invalid value for a `dropdown` or `number` field
  is rejected before any request that would change data is sent.
- **SC-006**: Normal operation adds no more than a negligible fraction of
  Hostaway's 200-requests-per-10-seconds budget: definitions are fetched no
  more than once per cache lifetime, plus at most once per refresh cycle after
  a cache miss, not once per object or per field.
- **SC-007**: A user reading the integration documentation can correctly state
  whether `doorCode` is a built-in field or a custom variable, and which service
  writes it.
- **SC-008**: All existing integration behaviour — including `set_door_code`
  and existing sensor states — is unchanged by this feature. Existing
  attributes remain unchanged except for the new custom-variable collection on
  the reservation sensor.

---

## Assumptions

- Custom variables are surfaced as a structured collection attribute rather
  than one flattened attribute per field, keeping attribute names stable and
  predictable when fields are added or removed in the dashboard.
- Listing custom variables use a dedicated per-listing sensor so the existing
  diagnostic listing sensors keep their current state and attribute surfaces.
- Where a definition is available, `varName` is used as the attribute key
  because it is the stable machine identifier; display names may contain spaces
  and may change.
- Values with no matching definition are keyed by their numeric id so that data
  is never silently dropped.
- Hostaway's `PUT` endpoints honour partial payloads — supported by the existing
  `update_reservation` behaviour — but this is treated as unverified for
  listings and FR-028 requires explicit verification before reliance.
- A read-modify-write merge is performed immediately before each write rather
  than relying on possibly stale coordinator data. Concurrent dashboard edits
  made after that read are outside the no-clobber guarantee unless Hostaway
  exposes a way to detect them.
- Read and write services follow the integration's existing user-facing service
  conventions, including clear documentation and response support where a
  payload is returned.
- Services operate on the account the config entry is authenticated against;
  multi-account support is unchanged by this feature.
- Task-type custom fields, custom field definition management, and writable
  per-field entities are explicitly out of scope and may be revisited in a
  later feature.
