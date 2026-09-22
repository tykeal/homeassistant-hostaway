<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

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

### Prior art and deliberate Hostaway divergence

This feature follows the sibling Guesty integration's
`specs/004-custom-variables` prior art where the two APIs permit the same user
experience: per-field dynamic listing sensors, a dedicated custom-field
definitions coordinator, and matching custom-field service names.

The write path intentionally diverges. Guesty has scoped custom-field endpoints
that support true partial updates. Hostaway does not; it only exposes whole
listing and reservation update endpoints. Hostaway writes therefore require a
read-modify-write merge and explicit no-clobber guarantees so custom-variable
updates do not erase unrelated custom values or built-in fields.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read custom variables from sensors (Priority: P1)

As a property manager using Home Assistant, I want the custom variables my team
maintains in Hostaway to appear as per-field listing sensors and on
reservation sensors, so that dashboards and templates can display operational
data (parking bay, pet policy, cleaner notes) without any extra configuration.

**Why this priority**: Reading is the foundational capability — nothing else in
this feature is useful without values arriving in Home Assistant. It delivers
standalone value even if no write surface ever ships.

**Independent Test**: Configure an account with at least one listing custom
field and one reservation custom field populated, let the integration poll, and
confirm that the listing field appears as its own sensor and the reservation
field appears on the corresponding reservation sensor, with names and values
matching the Hostaway dashboard.

**Acceptance Scenarios**:

1. **Given** a listing with populated custom variables, **When** the listings
   poll completes, **Then** each variable is exposed as a diagnostic sensor for
   that listing, with a stable key derived from `custom_` plus the slugified
   `varName`.
2. **Given** a reservation with populated custom variables, **When** the
   reservations poll completes, **Then** those variables are exposed as
   attributes on that listing's reservation sensor for the selected
   reservation, resolved to human-readable names when definitions are
   available.
3. **Given** a custom field defined with `isPublic=0` (hidden), **When** the
   poll completes, **Then** its value is still exposed — hidden fields are
   included in reads.
4. **Given** a listing or reservation with no custom field values, **When** the
   poll completes, **Then** no listing custom-field sensors are created for
   absent listing values, and the reservation custom-variable attribute surface
   exposes an empty collection rather than failing or omitting the attribute.
5. **Given** a custom variable's value is changed in the Hostaway dashboard,
   **When** the next scheduled poll runs, **Then** the matching sensor or
   reservation attribute reflects the new value without a Home Assistant
   restart.
6. **Given** a new listing custom field starts appearing at runtime, **When**
   the listings coordinator observes the new field, **Then** the integration
   creates the new per-field sensor without requiring a Home Assistant restart.

---

### User Story 2 - Read custom variables from services (Priority: P1)

As an automation author, I want response-returning services that return the
custom-field definitions and custom-field values for a given listing or
reservation, so that an automation can branch on a value without needing it
pre-rendered onto an entity attribute.

**Why this priority**: Service reads cover the cases sensor attributes cannot —
listings or reservations that are not currently represented by the selected
entity, multi-account setups, and automations that need a structured payload
including each field's type and allowed values.

**Independent Test**: Call `hostaway.get_custom_field_values` from Developer
Tools → Actions with a known listing id and confirm the response contains every
custom field for that object with its id, `varName`, display name, type, and
current value. Call `hostaway.get_custom_fields` and confirm it returns the
cached definition set.

**Acceptance Scenarios**:

1. **Given** a valid listing id, **When** `hostaway.get_custom_field_values` is
   called, **Then** the response lists that listing's custom variables with
   `customFieldId`, `varName`, display name, type, possible values for
   dropdowns, and current value.
2. **Given** a valid reservation id, **When**
   `hostaway.get_custom_field_values` is called, **Then** the response lists
   that reservation's custom variables in the same shape.
3. **Given** a defined custom field that has no value set on the object,
   **When** `hostaway.get_custom_field_values` is called, **Then** the field is
   still present in the response with an empty or unset value, so automations
   can see the field exists.
4. **Given** an id that does not exist or is not accessible, **When**
   `hostaway.get_custom_field_values` is called, **Then** the call fails with a
   clear, actionable error naming the object and id.
5. **Given** exactly one Hostaway account is configured, **When**
   `hostaway.get_custom_fields` or `hostaway.get_custom_field_values` is
   called without `config_entry_id`, **Then** the service uses that account.
6. **Given** multiple Hostaway accounts are configured, **When** either read
   service is called without `config_entry_id`, **Then** the service fails
   closed with `config_entry_id required when multiple entries exist` before
   performing any read.

---

### User Story 3 - Write without clobbering others (Priority: P1)

As an automation author, I want a service that sets one custom variable on a
listing or reservation, so that Home Assistant can push operational state back
into Hostaway — and I need certainty that normal writes do not erase the other
values read immediately before the write or any unrelated listing data.

**Why this priority**: Write is the other half of the requested capability, and
the clobber risk makes it the highest-stakes part of the feature: a careless
write destroys live property data.

**Independent Test**: On a listing with three populated custom variables, call
`hostaway.set_custom_field` for one of them, then re-read the listing and
confirm the target changed and the other two — plus the listing's built-in
fields — are untouched.

**Acceptance Scenarios**:

1. **Given** a listing with multiple populated custom variables, **When**
   `hostaway.set_custom_field` sets exactly one of them, **Then** the other
   custom variables retain their previous values.
2. **Given** a listing with populated built-in fields (name, price, bedrooms,
   door code, etc.), **When** `hostaway.set_custom_field` sets a custom
   variable, **Then** no built-in listing field is changed.
3. **Given** a reservation, **When** `hostaway.set_custom_field` sets a custom
   variable, **Then** the change is visible in Hostaway and other reservation
   fields, including `doorCode`, are unchanged.
4. **Given** a successful write, **When** the write completes, **Then** the new
   value is reflected in the corresponding sensor or reservation attribute
   without waiting for the next scheduled poll.
5. **Given** the write request fails at the Hostaway API, **When** the service
   returns, **Then** it raises a clear error and no partial local state is
   presented as successful.
6. **Given** multiple Hostaway accounts are configured, **When**
   `hostaway.set_custom_field` is called without `config_entry_id`, **Then**
   the service fails closed with `config_entry_id required when multiple
   entries exist` before resolving the field or sending any write.

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

1. **Given** a write request that names a field by `varName`, **When**
   `hostaway.set_custom_field` runs, **Then** the name is resolved to the
   correct `customFieldId` and the correct field is affected.
2. **Given** a write request that names a field by `customFieldId`, **When**
   `hostaway.set_custom_field` runs, **Then** the id is used after validating
   that it is defined for the requested target object type.
3. **Given** a request that supplies neither `varName` nor `customFieldId`, or
   supplies both identifiers even if they resolve to the same definition,
   **When** the service runs, **Then** it fails validation with a message
   explaining that exactly one field identifier is required.
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
  object carries no value. The read service includes the field with an unset
  value so automations can discover it; listing custom-field sensors are
  created only for values that are present on a listing.
- **Stale cached definitions**: a field created in the dashboard after the last
  definitions refresh becomes available after the next definitions coordinator
  refresh. An unresolvable id is surfaced under a stable fallback key instead
  of causing an immediate extra definitions fetch.
- **Malformed value record**: an entry in `customFieldValues` missing
  `customFieldId`, or with a non-integer id, or otherwise unparsable. The entry
  is skipped with a warning; the rest of the object parses normally and the
  coordinator refresh completes. This mirrors the existing
  `parse_reservations` behaviour introduced after a single malformed record
  crashed an entire refresh.
- **Malformed definition record**: an entry in the definitions response that
  cannot be parsed is skipped with a warning; remaining definitions are usable.
- **Dropdown value not in `possibleValues`**: a write requests a value the
  definition does not allow.
- **Type mismatch on write**: a non-numeric value, including a boolean, is
  written to a `number` field.
- **Unknown future field type**: Hostaway introduces a type the integration
  does not know. The service passes values for that field through to Hostaway
  for server-side validation rather than hard-rejecting them locally.
- **Duplicate `varName`**: two definitions share a `varName` across different
  object types (for example one listing field and one reservation field with
  the same name). Resolution MUST be scoped by object type so the correct field
  is chosen.
- **Task-type definitions present**: the account defines `objectType: task`
  custom fields. They MUST be ignored entirely by this feature.
- **Partial-payload rejection**: Hostaway turns out to treat
  `PUT /v1/listings/{id}` as a full replacement and drops omitted custom values.
  Listing writes MUST NOT ship until they can be performed without silently
  destroying data.
- **Concurrent external edit**: a user changes the same Hostaway object in the
  dashboard after Home Assistant reads current custom values but before the
  write reaches Hostaway. This feature only guarantees preservation of values
  visible to the write before it sends the update, unless Hostaway provides a
  conflict-detection mechanism that can make concurrent edits detectable.
- **Concurrent Home Assistant writes**: two Home Assistant service calls update
  different custom variables on the same object at the same time. Successful
  calls MUST NOT clobber each other; each successful write must be based on a
  current view that includes earlier successful Home Assistant writes.
- **Empty account**: an account with no custom fields defined at all. Reads
  return empty collections, writes fail cleanly, nothing errors on startup.
- **Rate limiting**: Hostaway permits 200 requests per 10 seconds per account
  and per IP. Larger responses and definition lookups must not push the
  integration toward that ceiling.
- **Value clearing**: a user wants to set a custom variable to empty. Clearing
  uses `value: null` so it is distinguishable from omitting the field and from
  writing an empty string. Existing listing custom-field sensors for a cleared
  value remain present with an empty state after a successful clear; no new
  sensor is created for a field that was already absent.

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
- **FR-005**: The integration MUST cache definitions in a dedicated custom
  field definitions coordinator rather than refetching them for every read or
  write operation. Definition caches MUST be isolated per authenticated
  Hostaway account or config entry so one account's definitions never label,
  validate, or expose another account's data.
- **FR-006**: The custom field definitions coordinator MUST poll on a
  user-configurable interval that defaults to 15 minutes, matching the existing
  listing coordinator default. The interval MUST enforce the integration's
  existing positive minimum of one minute. Fields created in the dashboard
  become usable after the next scheduled definitions refresh; this feature MUST
  NOT add a user-invokable definitions refresh service.
- **FR-007**: A failure to retrieve definitions MUST NOT prevent listing or
  reservation data from loading; the integration degrades to surfacing values
  by numeric id under stable fallback keys.

#### Reading values

- **FR-008**: Every listing or reservation retrieval that may supply custom
  field values MUST request Hostaway's `includeResources=1` option so that
  `customFieldValues` is populated rather than returned empty. This applies to
  each page of paginated coordinator reads and to direct object reads performed
  by services such as `hostaway.get_custom_field_values` and
  `hostaway.set_custom_field`.
- **FR-009**: The listing model MUST carry parsed custom field values.
- **FR-010**: The reservation model MUST carry parsed custom field values.
- **FR-011**: Each listing custom variable value MUST be exposed as its own
  diagnostic sensor for that listing. The entity key MUST be stable and derived
  from `custom_` plus the slugified `varName` when a definition is available
  before that value is first observed; unresolved values MUST use a stable key
  derived from the numeric id. If a later definitions refresh resolves a value
  that already created a fallback-key sensor, the existing sensor MUST retain
  its fallback entity key and update its metadata rather than creating a second
  entity or renaming the entity id.
- **FR-012**: Newly appearing listing custom variables MUST be discovered at
  runtime and added as new sensors without requiring a Home Assistant restart.
- **FR-013**: The existing diagnostic listing sensors (`listing_id`,
  `external_name`, `status`, `base_price`, `bedrooms`, `bathrooms`, and
  `max_guests`) MUST remain unchanged and MUST NOT gain custom-variable
  attributes.
- **FR-014**: Custom variables MUST be exposed as attributes on the existing
  reservation sensor, for the reservation that sensor currently represents.
  Existing reservation attributes MUST remain unchanged except for adding the
  custom-variable collection.
- **FR-015**: Reservation custom-variable attributes MUST resolve field ids to
  human-readable names when definitions are available, while preserving a
  stable fallback key for unresolvable ids.
- **FR-016**: Reads MUST include fields flagged hidden (`isPublic=0`).
- **FR-017**: Listing custom-field sensors and reservation attributes MUST
  include enough metadata to identify resolved fields: `customFieldId`,
  `varName`, display name, type, possible values when the type is `dropdown`,
  current value, and `resolved: true`.
- **FR-018**: A value whose `customFieldId` has no matching definition MUST
  still be surfaced under `custom_field_<customFieldId>`. The entry MUST
  contain `customFieldId`, current value, `resolved: false`, and empty or absent
  definition metadata so it is clearly distinguishable from a resolved field.
- **FR-019**: Parsing of custom field values MUST NOT be able to fail a listing
  or reservation coordinator refresh. Malformed entries are skipped with a
  logged warning and the remainder of the object is used.

#### Read services

- **FR-020**: The integration MUST provide `hostaway.get_custom_fields`, a
  response-returning service that returns cached custom field definitions.
- **FR-021**: `hostaway.get_custom_fields` MUST accept an optional
  `config_entry_id` selector so users can target a specific Hostaway account.
  When exactly one Hostaway config entry is loaded, omitting
  `config_entry_id` MUST select that entry. When multiple entries are loaded,
  omitting `config_entry_id` MUST fail closed with
  `config_entry_id required when multiple entries exist` before any read is
  performed.
- **FR-022**: The integration MUST provide
  `hostaway.get_custom_field_values`, a response-returning service that returns
  the custom variables for a specified listing or reservation.
- **FR-023**: `hostaway.get_custom_field_values` MUST accept `target_type`,
  `target_id`, and optional `config_entry_id` fields. `target_type` MUST be a
  selector limited to `listing` and `reservation`. When exactly one Hostaway
  config entry is loaded, omitting `config_entry_id` MUST select that entry.
  When multiple entries are loaded, omitting `config_entry_id` MUST fail closed
  with `config_entry_id required when multiple entries exist` before resolving
  the target id or performing any read.
- **FR-024**: The value read service response MUST include, per resolved field:
  `customFieldId`, `varName`, display name, type, possible values when the type
  is `dropdown`, and the current value. Unresolved fields MUST remain in the
  response using the same `custom_field_<customFieldId>` key and unresolved
  entry shape as sensor attributes.
- **FR-025**: The value read service MUST include defined fields that currently
  have no value, so automations can discover the available field set.
- **FR-026**: The value read service MUST return a clear, actionable error when
  the referenced listing or reservation does not exist or is inaccessible.

#### Write service

- **FR-027**: The integration MUST provide `hostaway.set_custom_field`, a
  service that sets one custom variable value on a specified listing or
  reservation.
- **FR-028**: `hostaway.set_custom_field` MUST accept `target_type`,
  `target_id`, a field identifier, `value`, and optional `config_entry_id`.
  `target_type` MUST be a selector limited to `listing` and `reservation`.
  When exactly one Hostaway config entry is loaded, omitting
  `config_entry_id` MUST select that entry. When multiple entries are loaded,
  omitting `config_entry_id` MUST fail closed with
  `config_entry_id required when multiple entries exist` before resolving the
  field identifier or sending any write.
- **FR-029**: `hostaway.set_custom_field` MUST support Home Assistant
  `SupportsResponse.OPTIONAL`, returning a structured result when a caller asks
  for a response while preserving fire-and-forget automation compatibility. On
  success, the response MUST contain exactly these required top-level keys:
  `target_type` (string enum: `listing` or `reservation`), `target_id`
  (integer Hostaway object id), `customFieldId` (integer resolved custom field
  definition id), `varName` (string resolved machine name from the definition),
  `addressed_by` (string enum: `customFieldId` or `varName`, naming which
  identifier the caller supplied), and `result` (string value `success`). When
  the caller supplies `varName`, `customFieldId` MUST contain the resolved
  numeric id. When the caller supplies `customFieldId`, `varName` MUST contain
  the resolved machine name from the validated definition.
- **FR-030**: `hostaway.set_custom_field` MUST accept exactly one field
  identifier: either numeric `customFieldId` or `varName`, but not both. Fields
  addressed by `customFieldId` MUST still resolve to a definition for the
  target object type before any write is sent.
- **FR-031**: The write service MUST reject a request that identifies no field,
  identifies more than one field, or identifies a field that cannot be resolved
  for the target object type, and MUST make no change when it does so.
  Definition failures or unavailable definitions MUST make writes fail rather
  than fall back to unchecked numeric ids.
- **FR-032**: The write MUST preserve every custom variable the caller did not
  name — setting one variable MUST NOT clear the others. Writes MUST be based on
  current values read before the update and submit a merged set, including
  unresolved values whose definitions are unavailable.
- **FR-033**: Concurrent Home Assistant writes to the same object MUST be
  coordinated so one successful call cannot overwrite another successful call's
  custom-variable changes.
- **FR-034**: The write MUST NOT modify any built-in field of the target
  listing or reservation that is visible before the write is sent. If a safe
  listing payload strategy cannot preserve or detect concurrent built-in field
  edits, the write MUST fail rather than risk overwriting them.
- **FR-035**: Before the merge strategy is relied upon, it MUST be explicitly
  verified against a real Hostaway listing that a partial payload to
  `PUT /v1/listings/{id}` does not clear unrelated listing data. The existing
  `update_reservation` partial payload (`{"doorCode": ...}`) is supporting
  evidence but is not verification for the listing endpoint. If that
  verification fails, listing write support MUST use a safe full payload or
  another safe endpoint before it ships.
- **FR-036**: The write service MUST validate submitted values against the
  field's declared type where practical. `text` and `textarea` fields require
  strings; `number` fields accept numeric values but MUST reject booleans;
  `dropdown` fields accept only values present in `possibleValues`.
- **FR-037**: Values for unknown or future field types MUST pass through to
  Hostaway for server-side validation rather than being hard-rejected locally.
- **FR-038**: A validation failure MUST reject the whole call with an
  explanatory error and MUST NOT write the requested field.
- **FR-039**: The write service MUST support clearing a custom variable to an
  empty value by accepting `value: null`. Null means clear the selected field
  and bypasses local type validation; an empty string remains a literal value
  for string-compatible Hostaway types.
- **FR-040**: After a successful write, the affected sensor or reservation
  attribute MUST reflect the new value without waiting for the next scheduled
  poll. After a successful clear, an existing listing custom-field sensor MUST
  remain present with an empty state rather than disappearing immediately.
- **FR-041**: The write service MUST NOT create writable entities (text,
  number, or select) for custom variables. Service-only is the write surface
  for this feature.
- **FR-042**: Hostaway API errors during a write MUST surface as clear,
  actionable Home Assistant errors, consistent with existing services. A
  rejected custom-variable mutation MUST NOT be logged and then returned as
  successful.

#### Documentation

- **FR-043**: User-facing service documentation MUST describe each new service,
  its fields, selector behaviour, and the exact `hostaway.set_custom_field`
  success response schema defined by FR-029.
- **FR-044**: Documentation MUST explicitly distinguish Hostaway **built-in**
  fields (such as `doorCode`, written by `hostaway.set_door_code`) from
  **custom variables**, and cross-reference the two so they are not conflated.
- **FR-045**: Documentation MUST state that hidden (`isPublic=0`) fields are
  included in reads, and that task-type custom fields are unsupported.

#### Operational constraints

- **FR-046**: The added request volume (larger listing/reservation responses
  plus definition lookups) MUST stay within Hostaway's published limit of 200
  requests per 10 seconds per account and per IP under normal polling.
- **FR-047**: Definition lookups MUST NOT be performed per listing, per
  reservation, or per field during a poll.

### Key Entities

- **Custom Field Definition**: The account-level description of a custom
  variable. Identified by a numeric id and a machine-readable `varName`. Carries
  a display name, an object type (`listing`, `reservation`, or `task`), a type
  (`text`, `textarea`, `number`, `dropdown`), optional possible values for
  dropdowns, a public/hidden flag, and a sort order. Read-only to this feature.
- **Custom Field Value**: The value of one custom field on one listing or one
  reservation. Identified by the definition's numeric id plus the owning object.
  Meaningful to a user only when joined to its definition.
- **Custom Field Definitions Coordinator**: A per-config-entry coordinator that
  fetches and caches definitions on a user-configurable interval defaulting to
  15 minutes, matching the listing coordinator default, and enforcing the
  integration's one-minute minimum interval.
- **Listing Custom-Field Sensor**: A dynamic per-listing, per-field diagnostic
  sensor whose state is one listing custom variable value. The stable entity
  key is based on `custom_` plus slugified `varName` when the definition is
  known before first observation, with a numeric-id fallback for unresolved
  values. Fallback-key sensors keep that entity key if a later definitions
  refresh resolves their metadata.
- **Reservation**: Existing entity, extended to carry a collection of custom
  field values for the selected reservation.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of a listing's or reservation's non-task custom variables —
  hidden fields included — are visible in Home Assistant within one poll
  interval of being set in the Hostaway dashboard.
- **SC-002**: A newly appearing listing custom variable is added as a new
  per-field sensor at runtime, without requiring a Home Assistant restart.
- **SC-003**: Setting a single custom variable via `hostaway.set_custom_field`
  leaves 100% of that object's other custom variables and all of its built-in
  fields unchanged, verified for both listings and reservations. Listing
  verification MUST use a real listing carrying at least three populated custom
  variables.
- **SC-004**: An automation author can read or write a named custom variable
  using `varName`, without knowing any numeric id.
- **SC-005**: A malformed or unrecognised custom field record never prevents a
  listing or reservation refresh from completing; the remaining objects and
  fields are still delivered.
- **SC-006**: A write with an invalid value for a `dropdown` or `number` field
  is rejected before any request that would change data is sent, and boolean
  values are not accepted as numbers.
- **SC-007**: Per Hostaway account, this feature adds zero extra API requests
  to each listing or reservation refresh cycle beyond the existing poll
  requests with `includeResources=1`. It adds one custom-field definitions
  coordinator cycle per configured interval, defaulting to 15 minutes and never
  lower than one minute. Each definitions cycle uses Hostaway's maximum page
  size and makes at least one request and no more than
  `max(1, floor(definition_count / 500) + 1)` paginated
  `GET /v1/customFields` requests, stopping earlier when the API pagination
  response indicates that no next page exists. These requests MUST remain
  subject to the integration's rate limiting so counted requests never exceed
  Hostaway's 200 requests per 10 seconds per account and per IP limit.
- **SC-008**: A user reading the integration documentation can correctly state
  whether `doorCode` is a built-in field or a custom variable, and which service
  writes it.
- **SC-009**: All existing integration behaviour — including `set_door_code`
  and existing sensor states — is unchanged by this feature. Existing
  attributes remain unchanged except for the new custom-variable collection on
  the reservation sensor.

---

## Assumptions

- Guesty `specs/004-custom-variables` is the prior art for this feature.
  Hostaway follows Guesty's user-facing mental model where the APIs permit it:
  per-field dynamic listing sensors, a dedicated definitions coordinator, and
  matching custom-field service names.
- Hostaway deliberately diverges from Guesty's write implementation because
  Hostaway lacks scoped custom-field endpoints. Read-modify-write merging and
  no-clobber requirements are required to protect existing Hostaway data.
- Listing custom variables use per-field dynamic sensors so the existing
  diagnostic listing sensors keep their current state and attribute surfaces.
- Where a definition is available, `varName` is used for listing custom-field
  sensor keys because it is the stable machine identifier; display names may
  contain spaces and may change.
- Values with no matching definition are keyed by their numeric id so that data
  is never silently dropped.
- Hostaway's reservation update endpoint honours partial payloads, as supported
  by the existing `update_reservation` behaviour. Listing update behaviour is
  unverified, and FR-035 requires explicit verification before reliance.
- A read-modify-write merge is performed immediately before each write rather
  than relying on possibly stale coordinator data. Concurrent dashboard edits
  made after that read are outside the no-clobber guarantee unless Hostaway
  exposes a way to detect them.
- Read and write services follow the integration's existing user-facing service
  conventions, including clear documentation and response support where a
  payload is returned.
- Services accept an optional `config_entry_id` where needed so multi-account
  users can target a specific Hostaway account.
- Task-type custom fields, custom field definition management, and writable
  per-field entities are explicitly out of scope and may be revisited in a
  later feature.
