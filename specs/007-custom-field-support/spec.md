<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Custom Variable (Custom Field) Support

**Feature Branch**: `007-custom-field-support`
**Created**: 2026-09-21
**Status**: Ready for implementation
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
   `varName`, disambiguated by numeric id when that key would collide.
2. **Given** a reservation with populated custom variables, **When** the
   reservations poll completes, **Then** those variables are exposed under the
   reservation sensor's `custom_fields` attribute using
   `custom_field_<customFieldId>` keys, with human-readable names included as
   entry metadata when definitions are available.
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
   still present in the response with `value: null`, so automations can see the
   field exists and can distinguish an unset value from an empty string.
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

**Independent Test**: On a listing with one populated custom variable, first
self-write that variable's current value and confirm the whole listing object
diff is empty. Then write a distinct sentinel value, confirm exactly that one
field changed, restore the original value, and confirm the final object
matches the pre-write snapshot exactly.

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
  `value: null` so automations can discover it; listing custom-field sensors
  are created only for values that are present on a listing.
- **Stale cached definitions**: a field created in the dashboard after the last
  definitions refresh becomes available after the next definitions coordinator
  refresh. An unresolvable id is surfaced under a stable fallback key instead
  of causing an immediate extra definitions fetch.
- **Malformed value record**: an entry in `customFieldValues` missing
  `customFieldId`, or with a non-integer id, or otherwise unparsable. The entry
  is skipped with a warning for sensor and attribute presentation; the rest of
  the object parses normally and the coordinator refresh completes. The
  original raw malformed entry is still preserved for read-modify-write merges
  so a later write can resubmit it unchanged rather than erase it. This mirrors
  the existing `parse_reservations` behaviour introduced after a single
  malformed record crashed an entire refresh, while extending the write path's
  no-clobber guarantee to malformed data.
- **Malformed definition record**: an entry in the definitions response that
  cannot be parsed is skipped with a warning; remaining definitions are usable.
- **Dropdown value not in `possibleValues`**: a write requests a value the
  definition does not allow.
- **Type mismatch on write**: a non-numeric value, including a boolean, is
  written to a `number` field.
- **Unknown future field type**: Hostaway introduces a type the integration
  does not know. The service passes values for that field through to Hostaway
  for server-side validation rather than hard-rejecting them locally.
- **Duplicate `varName`**: two definitions share a `varName`. Across different
  object types (for example one listing field and one reservation field with
  the same name), resolution MUST be scoped by object type so the correct field
  is chosen. Within the same object type, `varName` addressing MUST fail as
  ambiguous and require numeric `customFieldId` addressing instead.
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
  value remain present after a successful clear with native value `None`
  (rendered by Home Assistant as `unknown`) and `value: null` in their custom
  field metadata; no new sensor is created for a field that was already absent.

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
  user-configurable interval persisted as
  `custom_field_definitions_scan_interval` in the existing options flow. The
  user-facing option MUST use a required integer number selector in minutes,
  be labeled as the custom field definitions polling interval, default to 15
  minutes, and enforce the integration's existing positive minimum of one
  minute. Fields created in the dashboard become usable after the next
  scheduled definitions refresh; this feature MUST NOT add a user-invokable
  definitions refresh service.
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
- **FR-009**: Listing custom field values returned by Hostaway MUST be
  available through the listing custom-field sensor and service contracts
  defined by FR-011, FR-017, FR-024, and FR-040.
- **FR-010**: Reservation custom field values returned by Hostaway MUST be
  available through the reservation attribute and service contracts defined by
  FR-014, FR-017, FR-024, and FR-040.
- **FR-011**: Each listing custom variable value MUST be exposed as its own
  diagnostic sensor for that listing. The entity key MUST be stable and derived
  from `custom_` plus the slugified `varName` when a definition is available
  before that value is first observed. If the listing custom-field definition
  set contains multiple `listing` definitions that would produce the same
  `custom_<slugified varName>` key before any affected value is observed, every
  resolved value for those definitions MUST use the deterministic key
  `custom_<slugified varName>_<customFieldId>` from first observation. This
  includes same-object-type duplicate `varName` definitions and distinct
  `varName` values that slugify identically. If a later definitions refresh
  introduces a collision after an affected sensor already exists under
  `custom_<slugified varName>`, the existing sensor MUST retain that entity key
  and any additional colliding resolved values MUST use
  `custom_<slugified varName>_<customFieldId>` instead of overwriting or
  renaming the existing sensor. Unresolved listing values MUST use
  `custom_field_<customFieldId>` as their fallback key. If a later definitions
  refresh resolves a value that already created a fallback-key sensor, the
  existing sensor MUST retain its fallback entity key and update its metadata
  rather than creating a second entity or renaming the entity id. All listing
  custom-field sensor keys, resolved and fallback alike, MUST be allocated from
  one shared namespace per listing on a first-come-first-served basis. Any
  newly allocated key that would collide with an existing resolved or fallback
  key MUST append `_<customFieldId>` to the candidate key instead of
  overwriting or renaming an existing entity. If that suffixed key also
  collides, allocation MUST append deterministic numeric suffixes (`_2`, `_3`,
  and so on) until an unreserved key is found.
- **FR-012**: Newly appearing listing custom variables MUST be discovered at
  runtime and added as new sensors without requiring a Home Assistant restart.
- **FR-013**: The existing diagnostic listing sensors (`listing_id`,
  `external_name`, `status`, `base_price`, `bedrooms`, `bathrooms`, and
  `max_guests`) MUST remain unchanged and MUST NOT gain custom-variable
  attributes.
- **FR-014**: Custom variables MUST be exposed on the existing reservation
  sensor, for the reservation that sensor currently represents, under one new
  `custom_fields` attribute. Existing reservation attributes MUST remain
  unchanged except for adding that collection.
- **FR-015**: The reservation `custom_fields` attribute MUST be a mapping keyed
  by `custom_field_<customFieldId>` for every custom field value present on the
  reservation, whether or not its definition is resolved. Resolved value
  entries MUST include the human-readable display name as metadata so duplicate
  display names cannot overwrite each other. Unresolvable value ids MUST use
  the same key and include the unresolved entry shape from FR-018. Defined
  reservation fields that have no value MUST NOT be included in the reservation
  sensor attribute; those fields are exposed by the value read service per
  FR-025. A reservation with no custom field values MUST expose
  `custom_fields: {}`.
- **FR-016**: Reads MUST include fields flagged hidden (`isPublic=0`).
- **FR-017**: Listing custom-field sensors and reservation `custom_fields`
  attribute entries for resolved fields MUST expose the same required metadata
  keys used by the value read service: `customFieldId`, `varName`, `name`,
  `type`, `possibleValues`, `value`, and `resolved: true`. `possibleValues`
  MUST be a list containing allowed values for `dropdown` definitions and an
  empty list for other types.
- **FR-018**: A value whose `customFieldId` has no matching definition MUST
  still be surfaced. Listing unresolved values MUST use FR-011's exact
  fallback-key contract, while reservation unresolved values MUST use
  `custom_field_<customFieldId>`. Each unresolved entry MUST include
  `customFieldId`, `value`, and `resolved: false`; definition metadata keys
  (`varName`, `name`, `type`, and `possibleValues`) MUST be omitted for
  unresolved entries.
- **FR-019**: Parsing of custom field values MUST NOT be able to fail a listing
  or reservation coordinator refresh. Malformed entries are skipped with a
  logged warning for presentation and entity population only, and the remainder
  of the object is used. Skipping a malformed entry MUST NOT discard the
  original raw entry needed by FR-032's write merge.

#### Read services

- **FR-020**: The integration MUST provide `hostaway.get_custom_fields`, a
  response-returning service registered with Home Assistant
  `SupportsResponse.ONLY` that returns cached custom field definitions. The
  response MUST contain exactly one required top-level key, `custom_fields`,
  whose value is a list. Each definition entry MUST contain exactly these keys:
  `customFieldId`, `varName`, `name` (the human-readable display name), `type`,
  `objectType`, `possibleValues`, `isPublic`, and `sortOrder`;
  `possibleValues` MUST be a list containing allowed values for `dropdown`
  definitions and an empty list for other types. When the definitions cache is
  empty, the service MUST return `{"custom_fields": []}` rather than omitting
  the key or raising an error.
- **FR-021**: `hostaway.get_custom_fields` MUST accept an optional
  `config_entry_id` selector so users can target a specific Hostaway account.
  When exactly one Hostaway config entry is loaded, omitting
  `config_entry_id` MUST select that entry. When multiple entries are loaded,
  omitting `config_entry_id` MUST fail closed with
  `config_entry_id required when multiple entries exist` before any read is
  performed.
- **FR-022**: The integration MUST provide
  `hostaway.get_custom_field_values`, a response-returning service registered
  with Home Assistant `SupportsResponse.ONLY` that returns the custom variables
  for a specified listing or reservation.
- **FR-023**: `hostaway.get_custom_field_values` MUST accept `target_type`,
  `target_id`, and optional `config_entry_id` fields. `target_type` MUST be a
  selector limited to `listing` and `reservation`. `target_id` MUST be the
  integer Hostaway object id for the selected target type; missing or
  non-integer `target_id` values MUST fail validation before any read is
  performed. Boolean values are NOT valid integers for `target_id`, even
  though Python's `bool` subclasses `int`. When exactly one Hostaway config
  entry is loaded, omitting `config_entry_id` MUST select that entry. When
  multiple entries are loaded, omitting `config_entry_id` MUST fail closed with
  `config_entry_id required when multiple entries exist` before resolving the
  target id or performing any read.
- **FR-024**: The value read service response MUST contain exactly one
  top-level key, `custom_fields`, whose value is a mapping. For listing
  targets, mapping keys MUST be allocated with the same shared-namespace rules
  as FR-011 across every entry returned in that response: resolved valued
  fields, resolved fields with `value: null`, unresolved values, and any
  existing persisted listing custom-field sensor keys for that listing. An
  existing persisted listing custom-field sensor key for the same
  `customFieldId` MUST be reused as that entry's response key. Persisted keys
  for other `customFieldId`s reserve the namespace and can cause suffixing. The
  candidate key for a resolved listing entry with no persisted key for the same
  `customFieldId` is `custom_<slugified varName>`, using
  `custom_<slugified varName>_<customFieldId>` when the listing definition set
  contains duplicate or slug-colliding definitions. The candidate key for an
  unresolved listing entry is `custom_field_<customFieldId>`. Any response key
  candidate that collides with an already allocated resolved, unresolved, or
  persisted listing key MUST append `_<customFieldId>` instead of overwriting
  another entry. If the suffixed key also collides, the response allocator
  MUST append deterministic numeric suffixes (`_2`, `_3`, and so on) until an
  unreserved response key is found. Computing a key for a listing entry with
  `value: null` MUST NOT create or reserve a listing sensor entity outside
  that service response.
  For reservation targets, mapping keys MUST match the reservation
  `custom_field_<customFieldId>` key contract from FR-015. For both listing and
  reservation targets, each resolved entry MUST contain exactly these keys:
  `customFieldId`, `varName`, `name` (the human-readable display name), `type`,
  `possibleValues`, `value`, and `resolved`; `resolved` MUST be `true`.
  `possibleValues` MUST be a list containing allowed values for `dropdown`
  definitions and an empty list for other types. A defined field with no value
  MUST include `value: null`; omitting `value` is invalid, and an empty string
  remains a literal value. Each unresolved entry MUST contain exactly these
  keys: `customFieldId`, `value`, and `resolved`; `resolved` MUST be `false`.
  Empty results MUST return exactly `{"custom_fields": {}}` rather than
  omitting the key or raising an error.
- **FR-025**: The value read service MUST include defined fields that currently
  have no value, so automations can discover the available field set. These
  entries MUST use `value: null` in the response.
- **FR-026**: The value read service MUST return a clear, actionable error when
  the referenced listing or reservation does not exist or is inaccessible.

#### Write service

- **FR-027**: The integration MUST provide `hostaway.set_custom_field`, a
  service that sets one custom variable value on a specified listing or
  reservation.
- **FR-028**: `hostaway.set_custom_field` MUST accept `target_type`,
  `target_id`, exactly one field identifier, a required `value` key, and
  optional `config_entry_id`. `target_type` MUST be a selector limited to
  `listing` and `reservation`. `target_id` MUST be the integer Hostaway object
  id for the selected target type. Boolean values are NOT valid integers for
  `target_id`. The field identifier MUST be either numeric `customFieldId` or
  string `varName`, as constrained by FR-030. Boolean values are NOT valid
  integers for `customFieldId`. Omitting the `value` key MUST fail validation
  rather than being treated as a clear request.
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
  target object type before any write is sent. Fields addressed by `varName`
  MUST fail as ambiguous without writing if more than one definition for the
  target object type has that `varName`.
- **FR-031**: The write service MUST reject a request that identifies no field,
  identifies more than one field, or identifies a field that cannot be resolved
  for the target object type, and MUST make no change when it does so.
  Definition failures or unavailable definitions MUST make writes fail rather
  than fall back to unchecked numeric ids.
- **FR-032**: The write MUST preserve every custom variable the caller did not
  name — setting one variable MUST NOT clear the others. Writes MUST be based on
  current values read before the update and submit a merged set, including
  unresolved values whose definitions are unavailable and original raw malformed
  value entries skipped from presentation under FR-019. Malformed entries MUST
  be passed through unmodified in the merged Hostaway payload. If the raw form
  of a skipped malformed entry cannot be preserved, the write MUST fail closed
  instead of silently discarding that entry.
- **FR-033**: Concurrent Home Assistant writes to the same object MUST be
  coordinated so one successful call cannot overwrite another successful call's
  custom-variable changes. Coordinator refresh results for the affected object
  that were read before a successful write MUST NOT overwrite the post-write
  local value.
- **FR-034**: The write MUST NOT modify any built-in field of the target
  listing or reservation that is visible before the write is sent. If a safe
  listing payload strategy cannot preserve or detect concurrent built-in field
  edits made after the pre-write read, the write MUST fail rather than risk
  overwriting them unless the endpoint provides a conditional/version check
  that detects the concurrent edit. The no-clobber guarantee applies to the
  canonicalized pre-write snapshot plus the conditional/version check; without
  such detection, full-object strategies that can overwrite post-read external
  edits MUST remain disabled.
- **FR-035**: Listing partial-`PUT /v1/listings/{id}` semantics MUST remain
  untrusted until the Step 0 through Step 5 verification ladder in FR-051
  through FR-054 passes for the listing endpoint. The integration MUST
  implement both a partial-payload
  builder and a full-object payload builder. Full-object strategy selection is
  listing-only for this feature; reservation full-object writes remain disabled
  until a separate reservation protocol defines and verifies them. The
  executable state MUST record the selected payload strategy separately from
  whether partial `PUT` semantics were verified; a full-object listing strategy
  MUST NOT be represented by setting
  `listing_partial_put_verified` to true. If partial listing verification
  fails and a full-object listing strategy is selected, the no-op, sentinel,
  and restore steps MUST be repeated with the reconstructed full-object payload
  before listing writes are enabled. A full-object payload strategy MUST remain
  disabled until the implementation defines a per-target writable-field
  allowlist and normalization rules; deep-copying a `GET` response into a
  `PUT` payload is prohibited. The existing `update_reservation` partial
  payload
  (`{"doorCode": ...}`) is supporting evidence for reservation top-level merge
  semantics, but it is not verification for the listing endpoint. If listing
  partial verification fails, listing write support MUST use a verified
  full-object payload strategy, another verified safe endpoint, or remain
  disabled before it ships.
- **FR-036**: The write service MUST validate submitted values against the
  field's declared type where practical. `text` and `textarea` fields require
  strings; `number` fields accept numeric values but MUST reject booleans;
  `dropdown` fields accept only values present in `possibleValues`.
- **FR-037**: Values for unknown or future field types MUST pass through to
  Hostaway for server-side validation rather than being hard-rejected locally.
- **FR-038**: A validation failure MUST reject the whole call with an
  explanatory error and MUST NOT write the requested field.
- **FR-039**: The write service MUST support clearing a custom variable to an
  empty value by accepting an explicitly present `value: null`. Null means clear
  the selected field and bypasses local type validation; omitting `value` is
  invalid, and an empty string remains a literal value for string-compatible
  Hostaway types.
- **FR-040**: After a successful write targeting an object represented by a
  Home Assistant entity, the affected sensor or reservation attribute MUST
  reflect the new value without waiting for the next scheduled poll. An
  in-flight coordinator refresh that read older data before the write MUST NOT
  publish that older value over the immediate post-write state. After a
  successful clear, an existing listing custom-field sensor MUST remain present
  with native value `None` (rendered by Home Assistant as `unknown`) and
  `value: null` in its custom field metadata rather than disappearing
  immediately. When the write targets an unselected listing or a reservation
  that is not the current reservation represented by the reservation sensor,
  the service still succeeds and returns its normal response, but no immediate
  entity update is required and the absence of an entity MUST NOT be treated as
  an error.
- **FR-041**: The write service MUST NOT create writable entities (text,
  number, or select) for custom variables. Service-only is the write surface
  for this feature.
- **FR-042**: Hostaway API errors during a write MUST surface as clear,
  actionable Home Assistant errors, consistent with existing services. If the
  referenced listing or reservation does not exist, is inaccessible, or cannot
  be read for the required pre-write merge, the service MUST fail with a clear
  error naming the target type and id and MUST NOT present local state as
  updated. A rejected custom-variable mutation MUST NOT be logged and then
  returned as successful.

#### Documentation

- **FR-043**: User-facing service documentation MUST describe each new service,
  its fields, selector behaviour, and the exact response schemas defined by
  FR-020, FR-024, and FR-029.
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
- **FR-048**: A write MUST fail closed before any mutating request when the
  current target object omits `customFieldValues`, supplies
  `customFieldValues: null`, or supplies any non-list `customFieldValues`
  shape. A present empty list (`customFieldValues: []`) is a valid, genuinely
  empty custom-field collection and MUST NOT fail this requirement by itself.
- **FR-049**: A write MUST fail closed before any mutating request when the raw
  current `customFieldValues` collection contains duplicate entries for the
  addressed `customFieldId`, including duplicates that would otherwise be
  skipped from presentation.
- **FR-050**: A write MUST fail closed before any mutating request when the raw
  current entry for the addressed `customFieldId` is malformed. This is
  distinct from preserving malformed unaddressed entries under FR-019 and
  FR-032; the addressed malformed entry MUST NOT be replaced, dropped, or
  duplicated because the integration cannot safely determine Hostaway's
  intended value.
- **FR-051**: Listing verification MUST follow the gated ladder in order.
  Step 0 asks Hostaway support for authoritative `PUT /v1/listings/{id}`
  semantics. Step 1 captures a complete pre-write snapshot of the target
  object outside git and verifies that the write payload needed to reconstruct
  the target is reconstructable from that snapshot using allowlisted writable
  fields and normalization rules. A raw `deepcopy` of a `GET` response is not a
  valid restore payload. All later object comparisons MUST compare
  canonicalized complete snapshots, not only unrelated fields or addressed
  custom-field values. The canonicalizer MUST define and test normalization or
  exclusion for server-managed volatile fields such as update timestamps before
  the comparison can be used as evidence. Step 2 inspects the generated
  dry-run payload and sends no mutation.
- **FR-052**: Step 3 MUST run a disposable task canary before listing
  mutation: create a throwaway Hostaway task, send a partial
  `PUT /v1/tasks/{id}`, verify unrelated task fields survive, and delete the
  task. Tasks are disposable, do not sync to sales channels, and
  `HostawayApiClient.update_task` already targets `PUT /v1/tasks/{id}`. A
  task canary result is indicative, not conclusive, for listing semantics
  because Hostaway may implement task and listing updates in different
  controllers.
- **FR-053**: Steps 4 and 5 are listing mutations and MUST NOT run without a
  separate explicit owner decision and either a disposable listing or the
  allowlisted restore path from FR-051. Step 4 performs the no-op self-write
  and expects an empty whole-object diff. Step 5 writes the sentinel value,
  verifies exactly one field changed, restores the original value, and verifies
  the target matches the pre-write snapshot exactly. Restore-on-failure MUST be
  attempted automatically using the allowlisted restore payload. If no
  allowlisted restore path exists for the target, the ladder MUST stop at
  Step 3 and listing writes MUST remain disabled. If partial listing
  verification fails and a full-object strategy is selected, Step 4 and Step 5
  MUST be repeated with the reconstructed full-object payload before listing
  writes are enabled.
- **FR-054**: Verification evidence for each completed ladder step MUST be
  recorded in `specs/007-custom-field-support/live-verification.md` with
  redacted values before any write-safety gate is enabled. Steps 0 through 3
  are authorized now; Steps 4 and 5 require the separate explicit owner
  decision required by FR-053.
- **FR-055**: The production reservation `doorCode` evidence MAY be recorded
  for the verified Hostaway account/config entry as top-level merge evidence,
  but it MUST NOT by itself enable reservation custom-field writes. The
  existing `hostaway.set_door_code` handler sends a partial
  `PUT /v1/reservations/{id}` containing only `doorCode` plus optional
  `doorCodeVendor` and `doorCodeInstruction`, through
  `HostawayApiClient.update_reservation`. Owner-provided external account
  history MAY be recorded separately as empirical evidence, but this repository
  does not substantiate a v0.4.0 production release or no-data-loss history.
  The residual gap MUST be recorded honestly: this does not prove that
  reservation `customFieldValues` specifically round-trips. With zero
  reservation custom variables in the owner's account today, the evidence
  documents only the residual gap for top-level built-in fields.
  Reservation `customFieldValues` no-op, sentinel, and restore evidence, or an
  authoritative Hostaway contract covering `customFieldValues`, MUST be
  recorded for the account before `reservation_no_clobber_verified` is enabled.
  Other Hostaway accounts/config entries MUST remain disabled until their own
  account-bound evidence is recorded.

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
  fetches and caches definitions on the
  `custom_field_definitions_scan_interval` option, defaulting to 15 minutes
  and enforcing the integration's one-minute minimum interval.
- **Listing Custom-Field Sensor**: A dynamic per-listing, per-field diagnostic
  sensor whose state is one listing custom variable value. The stable entity
  key is based on `custom_` plus slugified `varName` when the definition is
  known before first observation, appending `_<customFieldId>` when needed to
  disambiguate key collisions, with `custom_field_<customFieldId>` as the
  fallback key for unresolved values. Fallback-key sensors keep that entity key
  if a later definitions refresh resolves their metadata.
- **Reservation**: Existing entity, extended to carry a collection of custom
  field values for the selected reservation under a `custom_fields` mapping
  keyed by numeric-id-derived `custom_field_<customFieldId>` entries.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of non-task custom variables — hidden fields included —
  for listings represented by listing entities and for the reservation
  currently represented by the reservation sensor are visible on the specified
  Home Assistant entity surfaces within one poll interval of being set in the
  Hostaway dashboard. Custom fields for listings or reservations not
  represented by an entity are available through
  `hostaway.get_custom_field_values` according to FR-024 and FR-025, but are
  not required to create or update an entity.
- **SC-002**: A newly appearing listing custom variable is added as a new
  per-field sensor under either its resolved key or its unresolved fallback key
  within one listing poll after its value is present. When the sensor is first
  added under an unresolved fallback key because its definition was not already
  cached, the same sensor retains that key and updates its resolved metadata
  within one listing poll after the next successful definitions coordinator
  refresh, without requiring a Home Assistant restart.
- **SC-003**: Setting a single custom variable via `hostaway.set_custom_field`
  leaves 100% of that object's other custom variables and all built-in fields
  unchanged, verified by an empty-diff protocol for each target type that needs
  live verification. The verifier MUST first perform a no-op self-write of one
  populated custom variable's current value and confirm the whole-object diff
  is empty. It MUST then write a distinct sentinel value and confirm exactly
  one field changed, then restore the original value and confirm the object
  matches the pre-write snapshot exactly using a canonicalized complete-snapshot
  comparison. This verification does not require any minimum number of
  populated custom variables: the no-op empty-diff check uses the entire
  object, including every built-in field and every present or absent custom
  variable, as the control group. A deviation anywhere in the object is a
  failure. When the live object has only one populated custom variable, the
  evidence MUST record that live endpoint preservation of additional populated
  custom values could not be observed, and it MUST NOT enable any listing
  write strategy for custom-field preservation. Multi-entry
  preservation MUST be proven either by live evidence with multiple populated
  custom values or by an authoritative Hostaway contract; automated tests still
  cover only the local merge/payload builder. A single-entry no-op/sentinel
  result may remain evidence for built-in-field preservation and exact restore
  behavior.
- **SC-004**: An automation author can write a custom variable using `varName`,
  without knowing any numeric id, when the `varName` is unique for the target
  object type; ambiguous same-object-type `varName`s require `customFieldId`.
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
- **SC-008**: The user-facing documentation includes one explicit statement in
  the custom-variable service documentation and one explicit statement in the
  `set_door_code` documentation that `doorCode` is a built-in field, not a
  custom variable, and that `hostaway.set_door_code` is the service that writes
  it.
- **SC-009**: The existing integration regression test suite passes unchanged
  after this feature is added, including the existing `set_door_code` service
  tests and existing listing/reservation sensor-state tests. Existing entity
  states and attributes asserted by those tests MUST remain unchanged except
  for the new `custom_fields` collection on the reservation sensor.

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
  contain spaces and may change. Numeric ids are appended only when multiple
  listing definitions would otherwise claim the same key.
- Values with no matching definition are keyed by their numeric id so that data
  is never silently dropped.
- Hostaway's reservation update endpoint appears to honour top-level partial
  payloads, as supported by the existing `update_reservation` door-code
  implementation and owner-provided external account history. This repository's
  release history does not itself substantiate any v0.4.0 production claim, so
  that history must be recorded as external evidence if used. This provides
  top-level merge evidence only; it does not enable reservation custom-field
  writes. FR-055 requires reservation `customFieldValues` no-op/sentinel/restore
  evidence or an authoritative Hostaway contract before the reservation write
  gate can be enabled.
- Listing update behaviour is unverified. FR-035 requires the verification
  ladder to pass before relying on a listing partial payload, and the
  implementation must also carry a full-object payload strategy so listing
  writes can choose a safer strategy when evidence requires it.
- A read-modify-write merge is performed immediately before each write rather
  than relying on possibly stale coordinator data. Strategies that can
  overwrite concurrent dashboard edits made after that read remain disabled
  unless Hostaway exposes a way to detect those edits.
- Read and write services follow the integration's existing user-facing service
  conventions, including clear documentation and response support where a
  payload is returned.
- Services accept an optional `config_entry_id` where needed so multi-account
  users can target a specific Hostaway account.
- Task-type custom fields, custom field definition management, and writable
  per-field entities are explicitly out of scope and may be revisited in a
  later feature.
