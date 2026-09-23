<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Research: Custom Field Support

**Feature**: 007-custom-field-support
**Date**: 2026-09-22
**Status**: Complete

## R-001: Hostaway custom field definitions endpoint

**Decision**: Fetch definitions from
`GET /v1/customFields?limit=500&offset=<offset>` and ignore `task`
definitions after parsing.

**Rationale**: Hostaway documents `GET /v1/customFields` with optional
`objectType=reservation|listing|task`, default limit 100, and maximum limit
500. Fetching all object types with the maximum page size gives one cache per
account and avoids separate listing/reservation definition requests. Filtering
out `task` locally satisfies the feature scope without needing additional API
calls.

**Implementation notes**:

- Definition fields are `id`, `accountId`, `name`, `varName`,
  `possibleValues`, `type`, `objectType`, `isPublic`, and `sortOrder`.
- Known types are `text`, `textarea`, `number`, and `dropdown`.
- `possibleValues` is JSON for dropdown fields. Normalize it to a list of
  allowed values and use an empty list for all non-dropdown types.
- Malformed definition records are skipped with a warning so the remaining
  cache remains usable.

**Alternatives considered**:

- Fetch per object type: rejected because it doubles normal definitions
  traffic without changing the cache semantics.
- Fetch definitions on service calls: rejected by FR-005 and FR-047; this
  would create unnecessary latency and request amplification.

## R-002: `includeResources=1` value retrieval

**Decision**: Every listing or reservation read that may expose custom field
values must include `includeResources=1`.

**Rationale**: Hostaway returns empty `customFieldValues` unless
`includeResources=1` is present. This applies to coordinator page reads and to
direct service reads performed by `get_custom_field_values` and
`set_custom_field`.

**Implementation notes**:

- Add `includeResources=1` to listing pagination and direct listing reads.
- Add `includeResources=1` to `fetch_reservation_items` for coordinator reads.
- Add direct `GET /v1/listings/{id}?includeResources=1` and
  `GET /v1/reservations/{id}?includeResources=1` reads for
  `target_id`-only read services and for write services before merging.
- Do not satisfy reservation `target_id` lookups by requiring `listing_id` or
  scanning all selected listings; direct reads keep the service contract and
  rate budget bounded.
- Hidden fields (`isPublic=0`) are included under the same flag and must not
  be filtered out.

## R-003: API module separation

**Decision**: Add `custom_components/hostaway/api/custom_fields.py` and keep it
free of Home Assistant imports.

**Rationale**: `api/client.py` is focused on authenticated request transport;
adding custom-field parsing, definitions pagination, merge, and validation
would mix responsibilities that belong in a focused data-handling module.
Constitution II requires the Hostaway API client to remain a clean abstraction
layer, and the existing `api/reservations.py` helper module establishes the
local pattern: keep endpoint-specific data handling in focused,
Home-Assistant-independent modules and use the main client for authenticated
request transport. This also mirrors Guesty's library-extractable
custom-fields design where the APIs permit the same user-facing behavior.

**Implementation notes**:

- Define narrow protocols for `_request_results`, `_request`, or mutation
  calls if that keeps the module independent and testable.
- Keep HA errors, coordinators, services, and entity-registry access out of
  the API module.
- Add only thin delegates to `HostawayApiClient` if callers need a stable
  public method; otherwise inject the client into the custom-fields module.

## R-004: Read-modify-write instead of scoped writes

**Decision**: Implement custom-field writes as no-clobber read-modify-write
operations over Hostaway's whole-object update endpoints.

**Rationale**: Hostaway has no scoped endpoint equivalent to Guesty's
`PUT /listings/{id}/custom-fields`. The documented write surfaces are
`PUT /v1/listings/{id}` and `PUT /v1/reservations/{id}`, both of which accept
`customFieldValues`. A service that sends only the target value without
preserving the rest risks deleting unrelated custom variables or built-in
fields.

**Implementation notes**:

- Read the object immediately before each write with `includeResources=1`.
- Parse presentation values separately from raw merge values.
- Treat only a present `customFieldValues` list as mergeable. A present empty
  list is genuinely empty; missing, `null`, or non-list `customFieldValues`
  must fail closed before any mutation.
- Preserve unresolved values and raw malformed entries exactly as read.
- If a malformed raw entry cannot be included unchanged in the outgoing
  payload, fail the write instead of silently dropping it.
- If a malformed raw entry carries the addressed `customFieldId`, fail closed
  before the mutation. Replacing it could discard raw data, while appending a
  new entry beside it could leave Hostaway to choose between duplicates.
- If more than one raw entry carries the addressed `customFieldId`, fail closed
  before the mutation instead of choosing one or appending another.
- Use per-entry, per-target `asyncio.Lock` objects to serialize Home
  Assistant writes to the same listing or reservation.

**Alternatives considered**:

- Assume partial `PUT` is always safe: rejected by FR-035 for listings.
- Drop malformed custom-field entries: rejected by FR-019 and FR-032 because
  it violates the no-clobber guarantee.

## R-005: Write safety verification gates

**Decision**: Make live no-clobber verification an early blocking
implementation task for each writable target type before relying on that
target's writes.

**Rationale**: The existing `update_reservation` service successfully sends
partial reservation payloads, but that is not evidence for listing update
semantics. Hostaway may treat listing updates as full replacements. The
feature cannot risk erasing listing fields while trying to set one custom
variable.

**Listing verification**:

1. Select a real listing with at least three populated custom fields and
   representative built-in fields. Prefer a disposable test listing.
2. Read it with `includeResources=1` and store a complete private rollback
   snapshot. Only redacted summaries may be logged or committed.
3. Send a partial `PUT /v1/listings/{id}` payload containing only the merged
   `customFieldValues` for a harmless value change.
4. Re-read with `includeResources=1`.
5. Assert the target value changed and every unrelated custom field and
   visible built-in field stayed byte-for-byte equivalent.
6. Roll back using the complete private snapshot if any unexpected mutation is
   detected; otherwise restore the harmless target value if necessary.

**Fallback if verification fails**: Listing writes must fail closed with an
actionable error while reads and reservation writes continue. Listing writes
must remain disabled for this feature unless a separate safe endpoint or full
payload strategy is specified, tested against live data, and shown to preserve
every visible built-in field.

**Reservation verification**:

1. Select a real reservation with at least three populated custom fields and
   at least one visible built-in field such as `doorCode`.
2. Read it with `includeResources=1` and store a complete private rollback
   snapshot. Only redacted summaries may be logged or committed.
3. Send a merged `PUT /v1/reservations/{id}` payload containing one harmless
   custom-field value change.
4. Re-read with `includeResources=1`.
5. Assert the target value changed and every unrelated custom field and
   visible built-in field stayed byte-for-byte equivalent.
6. Roll back using the complete private snapshot if any unexpected mutation is
   detected; otherwise restore the harmless target value if necessary.

**Executable gates**: The implementation must carry default-false
`listing_partial_put_verified` and `reservation_no_clobber_verified` flags in
per-entry `CustomFieldWriteSafetyGates`. Each target type rejects before
reading or mutating while its flag is false. A flag may become true only in an
implementation change that records the corresponding successful verification.

## R-006: Definition coordinator behavior

**Decision**: Use a dedicated per-config-entry
`HostawayCustomFieldsCoordinator` with configurable polling.

**Rationale**: Definitions are account-level metadata that change
infrequently. A coordinator avoids per-field and per-object definition fetches,
keeps account caches isolated, and lets listing/reservation refreshes continue
when definitions temporarily fail.

**Implementation notes**:

- Option key: `custom_field_definitions_scan_interval`.
- Default: 15 minutes.
- Minimum: existing `MIN_SCAN_INTERVAL` of one minute.
- Definitions failures raise `UpdateFailed` for the definitions coordinator
  only; listing and reservation coordinators continue using values and fallback
  numeric keys.
- The initial definitions refresh must be non-blocking or catch
  `UpdateFailed` during config entry setup. A temporary definitions outage must
  not abort setup because FR-007 requires listing and reservation data to load
  with fallback numeric keys.
- Track `last_refresh_succeeded` and `last_refresh_error` separately from the
  cached definitions. Reads may keep using stale definitions after a refresh
  failure so entity presentation remains stable, but writes must fail closed
  whenever the latest definitions refresh failed, even when the stale cache is
  non-empty. This satisfies FR-031 by preventing writes from resolving fields
  against definitions that are no longer known-current.

## R-007: Entity key allocation and restart stability

**Decision**: Persist listing custom-field key allocation in entity unique IDs
and seed each per-listing allocator from the Home Assistant entity registry on
startup/reload.

**Rationale**: The spec requires first-come-first-served allocation, suffixing
for collisions, fallback keys for unresolved values, no renaming of existing
entities, and stability across Home Assistant restarts. In-memory maps alone
cannot distinguish a previously allocated unsuffixed key from a newly
introduced collision after restart. Entity registry entries are the durable HA
state available to integrations.

**Algorithm summary**:

- Entity unique IDs include both `customFieldId` and the allocated key.
- On startup/reload, scan registry entries for the config entry and listing to
  rebuild `customFieldId -> key` and reserve every existing key.
- Existing mappings always win, even if definitions later change or collide.
- New resolved values use `custom_<slugified varName>` when the slug is unique
  among listing definitions, or `custom_<slug>_<customFieldId>` when the
  current definition set contains duplicates or slug collisions.
- New unresolved values use `custom_field_<customFieldId>`.
- Candidate collisions append `_<customFieldId>` instead of overwriting.
- Service read responses use the same allocator without persisting keys for
  fields that have `value: null` and no existing sensor.

**Alternatives considered**:

- Recompute keys from definitions only: rejected because a later collision
  would rename or remap entities after restart.
- Store mappings only in coordinator memory: rejected because HA restart would
  lose the first-come allocation history.

## R-008: Service registration and response support

**Decision**: Add a `services/custom_fields.py` handler module and register
three services through the existing `ServiceDefinition` table.

**Rationale**: The services package already uses focused handler modules,
central schemas, and a table-driven registration loop. Following that pattern
keeps registration consistent and prevents `services/__init__.py` from
becoming another monolith.

**Service support**:

- `hostaway.get_custom_fields`: `SupportsResponse.ONLY`
- `hostaway.get_custom_field_values`: `SupportsResponse.ONLY`
- `hostaway.set_custom_field`: `SupportsResponse.OPTIONAL`

All three reuse the existing fail-closed `_resolve_entry_data` helper so
multi-account behavior exactly matches the spec.

## R-009: Value validation

**Decision**: Locally validate known Hostaway field types before writes and
pass unknown future types through to Hostaway.

**Rationale**: Local validation catches actionable user mistakes before a
mutating request, while pass-through for unknown future types preserves forward
compatibility.

**Rules**:

- `text` and `textarea`: value must be a string.
- `number`: value must be int or float, but never bool.
- Numeric identifiers, including definition ids, value-entry `customFieldId`s,
  service `customFieldId`, and `target_id`, must also reject bool even though
  Python treats bool as int.
- `dropdown`: value must be present in normalized `possibleValues`.
- `value: null`: accepted as an explicit clear and bypasses type validation.
- Unknown type: skip local type validation and let Hostaway accept or reject.

## R-010: Rate-limit budget

**Decision**: Add no extra requests to normal listing/reservation polling
beyond the existing page reads with `includeResources=1`; add one definitions
coordinator cycle per configured interval.

**Rationale**: Hostaway's limit is 200 requests per 10 seconds per account and
per IP. The integration already fetches reservations sequentially by selected
listing. Custom fields should increase payload size, not poll request count.

**Budget**:

- Listings: same page count as today, with `includeResources=1`.
- Reservations: same per-listing page count as today, with
  `includeResources=1`.
- Definitions: at most `max(1, floor(definition_count / 500) + 1)` requests
  per definitions interval, stopping earlier when pagination indicates done.
- Services: direct reads and writes are user-triggered and use the existing
  retry/rate-limit behavior.
