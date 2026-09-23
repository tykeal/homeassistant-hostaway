<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Implementation Plan: Custom Field Support

**Branch**: `007-custom-field-support` | **Date**: 2026-09-22 |
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from
`specs/007-custom-field-support/spec.md`

## Summary

Add Hostaway custom field support for listing and reservation reads, read
services, and service-only writes. The implementation will add a
Home-Assistant-independent `api/custom_fields.py` module for definitions,
custom value parsing, value validation, direct object reads with
`includeResources=1`, and read-modify-write payload preparation. Home
Assistant wiring will add a dedicated per-entry definitions coordinator, a
configurable definitions polling interval, dynamic per-field listing sensors,
reservation `custom_fields` attributes, and the three required services:
`hostaway.set_custom_field`, `hostaway.get_custom_fields`, and
`hostaway.get_custom_field_values`.

Hostaway does not expose scoped custom-field write endpoints. Writes therefore
must read the current listing or reservation with `includeResources=1`, merge
exactly one addressed custom value into the raw current `customFieldValues`
collection, preserve unresolved and malformed raw entries, and submit a
payload selected per target type through the existing whole-object `PUT`
endpoint. The API layer must provide both the partial `customFieldValues`
payload builder and a full-object payload builder so each endpoint can use the
safest strategy supported by recorded evidence. The merge must distinguish an
empty list from missing, null, or non-list
`customFieldValues`; malformed collections abort before any `PUT` so existing
values are not silently cleared. A malformed entry for the addressed
`customFieldId` also aborts before any `PUT`, because replacing it could drop
raw data and appending beside it could create an ambiguous duplicate. Listing
and reservation write support are gated by default-off executable safety
flags. Listing writes require live partial-PUT verification, and reservation
writes may use documented production `doorCode` evidence for top-level merge
semantics until reservation custom variables exist. Until the matching flag is
enabled by a verification commit, the service rejects that target type before
any read, merge, or `PUT`.

## Technical Context

**Language/Version**: Python 3.14.2+ per `requires-python` in
`pyproject.toml`

**Primary Dependencies**: Home Assistant >= 2026.5.4, httpx 0.28.1,
voluptuous

**Storage**: Home Assistant config entries, options, entity registry, and
runtime coordinator caches; no new external storage

**Testing**: pytest with pytest-asyncio and
pytest-homeassistant-custom-component; ruff for linting

**Target Platform**: Home Assistant custom integration for Hostaway

**Project Type**: HACS-style Home Assistant custom component

**Performance Goals**: Zero additional API requests per normal
listing/reservation poll beyond adding `includeResources=1`; definitions poll
defaults to 15 minutes and uses Hostaway's maximum 500-item page size; all
requests remain under Hostaway's 200 requests / 10 seconds rate limit

**Constraints**: All I/O async; no Home Assistant imports in
`custom_components/hostaway/api/custom_fields.py`;
`uvx --from aislop==0.12.0 aislop ci` must keep the repository at the
configured 100/100 score; writes must preserve raw malformed custom field value
records and built-in fields; multi-account services fail closed without
`config_entry_id`

**Scale/Scope**: Account-level custom field definitions for `listing` and
`reservation` object types, dynamic listing custom-field sensors, reservation
custom-field attributes, three services, and tests for malformed data,
concurrency, response schemas, and no-clobber write behavior

## Constitution Check

*GATE: Must pass before research. Re-check after design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality & Testing | PASS | Tasks must follow TDD: API models/parsers, key allocator, service schemas, write merge, and sensor behavior get failing tests first. |
| II. API Client Design | PASS | Add `api/custom_fields.py` with zero HA imports; keep direct HTTP mechanics in the existing `HostawayApiClient`. |
| III. Atomic Commit Discipline | PASS | Implement in phased commits; task-list updates remain separate from code commits. |
| IV. Licensing & Attribution | PASS | New source/test files get SPDX headers; markdown files carry SPDX block comments. |
| V. Pre-Commit Integrity | PASS | No hook bypass; local pytest and ruff remain required before commit. |
| VI. Agent Co-Authorship & DCO | PASS | Commits use `git commit -s` and the required co-author trailer. |
| VII. UX Consistency | PASS | Services follow existing Hostaway selector, response, and error patterns. |
| VIII. Performance Requirements | PASS | Definitions coordinator prevents per-object definition fetches; write locks serialize per-object mutations. |
| IX. Phased Development | PASS | Blocking API verification precedes listing write implementation. |
| X. Security & Credentials | PASS | No credential handling changes; custom field values may be sensitive and must not be logged. |

**Gate Result**: PASS. No constitution violations.

**Post-design re-check**: PASS. The design keeps Hostaway API logic
library-extractable, preserves current entities and services, and documents
the listing partial-PUT verification gate before listing writes can ship.

## Project Structure

### Documentation (this feature)

```text
specs/007-custom-field-support/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── hostaway-custom-fields-api.md
├── checklists/
│   └── requirements.md
└── tasks.md                     # Output from /speckit.tasks
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── __init__.py                    # Add definitions coordinator setup/shutdown
├── config_flow.py                 # Add definitions scan interval option
├── config_options.py              # Optional options-flow helper module
├── const.py                       # Add custom-field option/service constants
├── strings.json                   # Add options/service translations
├── translations/
│   └── en.json                    # Add options/service translations
├── coordinator.py                 # Add HostawayCustomFieldsCoordinator
├── api/
│   ├── client.py                  # Add thin transport delegates only if needed
│   ├── custom_fields.py           # New HA-independent custom field module
│   ├── models.py                  # Add custom field DTOs and raw value storage
│   └── reservations.py            # Add includeResources=1 to reservation reads
├── sensor/
│   ├── __init__.py                # Runtime custom-field sensor discovery
│   ├── custom_fields.py           # New key allocator and listing CF sensor
│   ├── helpers.py                 # Add reservation custom field attributes
│   └── listing.py                 # Existing 7 diagnostics unchanged
└── services/
    ├── __init__.py                # Register three new services
    ├── custom_fields.py           # New service handlers
    ├── helpers.py                 # Reuse fail-closed entry resolution
    └── schemas.py                 # Add custom-field service schemas

tests/
├── api/
│   ├── test_custom_fields.py      # New API parser/client tests
│   └── test_models.py             # Extend listing/reservation custom values
├── sensor/
│   ├── test_custom_fields.py      # New allocation and dynamic sensor tests
│   ├── test_listing.py            # Existing diagnostics unchanged
│   └── test_reservation.py        # Extend attributes tests
├── services/
│   └── test_custom_fields.py      # New service schema/handler tests
└── test_config_flow.py            # Extend options flow coverage
```

**Structure Decision**: Use a new `api/custom_fields.py` module instead of
adding custom-field parsing, definitions pagination, merge, and validation
logic to `api/client.py`. The module receives a `HostawayApiClient` or narrow
request protocol by dependency injection and contains no Home Assistant
imports, matching the existing `api/reservations.py` separation and Guesty's
library-extractable R-005 decision. Home Assistant service handlers live in a
new `services/custom_fields.py` module so `services/__init__.py` remains
table-driven.

## Phase Overview

### Phase 1: Setup and guardrails

- Create the API, service, sensor, and test extension points with SPDX
  headers and zero Home Assistant imports in `api/custom_fields.py`.
- Add executable write-gate objects that default to disabled for both target
  types.
- Verify the repository still passes the configured
  `uvx --from aislop==0.12.0 aislop ci` score gate; no file-level line-count
  rule is configured.

### Phase 2: API models, parsing, and includeResources reads

- Add `HostawayCustomFieldDefinition`, parsed value records, unresolved value
  records, raw malformed value preservation, and response-shaping helpers.
- Validate every integer identifier with a bool-safe integer check. Definition
  ids, value-entry `customFieldId`s, service `customFieldId`, and `target_id`
  accept real integers only; `True` and `False` must be rejected even though
  Python's `bool` subclasses `int`.
- Add paginated `GET /v1/customFields` retrieval with `limit=500`, optional
  `objectType`, and filtering that ignores `task`.
- Update listing and reservation reads to include `includeResources=1` so
  `customFieldValues` is populated.
- Add direct target reads for `GET /v1/listings/{id}?includeResources=1` and
  `GET /v1/reservations/{id}?includeResources=1` so services can satisfy
  `target_id`-only read and write contracts without requiring `listing_id` or
  scanning account-wide collections.
- Add a validated single-object response helper or delegate for direct target
  reads; existing `_request_results` accepts only list-valued `result` payloads
  and must not be reused for single listing/reservation responses.
- Mirror `parse_reservations` by skipping malformed presentation records with
  warnings while preserving their raw entries for write merges.

### Phase 3: Definitions coordinator and options flow

- Add `custom_field_definitions_scan_interval`, default 15 minutes, minimum
  one minute, as a required integer minutes option in the existing options
  flow.
- Create a per-config-entry `HostawayCustomFieldsCoordinator` that fetches and
  caches definitions without blocking listing or reservation coordinator
  refreshes when definitions fail.
- Track the latest definitions refresh result independently from cached data.
  The coordinator starts with `last_refresh_succeeded = False`, sets it true
  only after a successful refresh, and sets it false with `last_refresh_error`
  on any later failed refresh while retaining the prior cache for reads.
- Do not let the definitions coordinator's initial refresh abort config entry
  setup. Run a non-blocking initial refresh or catch `UpdateFailed`, retain an
  empty/stale definitions cache, and allow listing/reservation data to load
  with fallback numeric keys as required by FR-007.
- Read surfaces may continue using retained stale definitions after a failed
  refresh so entity names and attributes do not churn. Write services must
  reject all mutations whenever `last_refresh_succeeded` is false, even if the
  retained cache is non-empty, with `custom field definitions refresh failed;
  writes are disabled until the next successful refresh`.
- Store the coordinator under a new dedicated key inside the existing
  `hass.data[DOMAIN][entry.entry_id]` runtime mapping. `__init__.py` already
  stores `token_manager`, `api_client`, `listings_coordinator`, and
  `reservations_coordinator` in that dict; the custom-field definitions
  coordinator must be added alongside those keys without replacing or
  reassigning the mapping.
- Do not add a separate teardown path for the definitions coordinator. Extend
  the existing unload path that pops `hass.data[DOMAIN][entry.entry_id]` in
  `__init__.py` and shuts down the listing and reservation coordinators, so the
  new coordinator is torn down with the same per-entry runtime data.
- Add user-facing labels and descriptions for the new options-flow field in
  `strings.json` and `translations/en.json`.

### Phase 4: Blocking safety evidence gates

- Write a real-account listing verification ladder. Step 0 asks Hostaway
  support for authoritative `PUT` semantics. Step 1 captures a complete target
  snapshot and proves it is reconstructable before mutation. Step 2 inspects
  the dry-run payload generated by the verification script. Step 3 creates a
  disposable task canary, sends partial `PUT /v1/tasks/{id}`, verifies
  unrelated task fields survive, and deletes the task; this is indicative, not
  conclusive, for listing semantics because Hostaway may use different
  controllers. Steps 0 through 3 are authorized first. Step 4 performs a
  listing no-op self-write and expects an empty whole-object diff. Step 5
  writes a distinct listing sentinel value, verifies exactly one field changed,
  restores the original value, and verifies the object matches the pre-write
  snapshot exactly. Steps 4 and 5 require a separate explicit owner decision.
- Treat the complete ladder as blocking for listing writes. It must prove
  omitted built-in fields and unrelated custom values remain unchanged before
  any listing write path can rely on partial `PUT`.
- Keep the listing write safety gate off by default. The implementation state
  is `listing_partial_put_verified = False` in the
  `CustomFieldWriteSafetyGates` object stored at
  `hass.data[DOMAIN][entry.entry_id]["custom_field_write_safety"]`. Listing
  writes reject with a user-facing message that listing custom-field writes
  are disabled until the verification ladder records passing evidence while it
  remains false. It may be flipped on only by an implementation change that
  records the successful FR-035 evidence.
- If partial listing PUT is destructive, keep listing writes disabled or switch
  to a verified full-object payload strategy for this feature. Restore on
  failure must be attempted automatically, but the evidence must document that
  restoration depends on cleared built-in fields being writable.
- Record production reservation evidence from the existing `set_door_code`
  service before enabling reservation writes. The handler sends a partial
  `PUT /v1/reservations/{id}` with only `doorCode` plus optional
  `doorCodeVendor` and `doorCodeInstruction` through
  `HostawayApiClient.update_reservation`, and that behavior has shipped since
  v0.4.0 with no reported reservation data loss. This supports top-level merge
  semantics for the reservation endpoint. It does not prove
  `customFieldValues` round-tripping; with zero reservation custom variables in
  the owner's account today, the current clobber surface is limited to
  built-in fields covered by the door-code evidence.
- Treat documented reservation production evidence as sufficient to enable the
  reservation gate when recorded in `live-verification.md`.
- Keep the reservation write safety gate off by default. The implementation
  state is `reservation_no_clobber_verified = False` in the same
  `CustomFieldWriteSafetyGates` object. Reservation writes reject with a
  user-facing message that reservation custom-field writes are disabled until
  accepted production or live evidence is recorded while it remains false. It
  may be flipped on only by an implementation change that records the
  successful FR-055 evidence.

### Phase 5: Entity surfaces and deterministic key allocation

- Add a restart-stable listing custom-field key allocator seeded from the Home
  Assistant entity registry and the current listing definitions.
- Create one diagnostic listing sensor per present listing custom field value
  without changing the seven existing listing diagnostic sensors.
- Add `custom_fields` to reservation sensor attributes, keyed by
  `custom_field_<customFieldId>`, returning `{}` when no values exist.
- Add runtime discovery for new listing custom field values observed by the
  listings coordinator.

### Phase 6: Read services

- Register `hostaway.get_custom_fields` with `SupportsResponse.ONLY`.
- Register `hostaway.get_custom_field_values` with `SupportsResponse.ONLY`.
- Reuse `_resolve_entry_data` so services select the sole entry or fail closed
  with `config_entry_id required when multiple entries exist`.
- Shape responses exactly as specified, including defined fields with
  `value: null` and unresolved values without definition metadata.

### Phase 7: Addressing and validation

- Validate exactly one of `customFieldId` or `varName`; boolean
  `customFieldId` and boolean `target_id` inputs are invalid identifiers, not
  integers. Require definitions for all writes; fail ambiguous
  same-object-type `varName` resolutions. Also require
  `definitions_coordinator.last_refresh_succeeded` to be true. A stale cache
  retained after a failed refresh is available for reads only and must not be
  used for write resolution.
- Add shared definition lookup helpers scoped by object type.
- Validate known field types before writes: strings for text-like fields,
  non-boolean numbers for number fields, and declared values for dropdowns.
  Pass unknown future field types through to Hostaway for server validation.

### Phase 8: Write service and no-clobber merge

- Register `hostaway.set_custom_field` with `SupportsResponse.OPTIONAL`.
- Load `CustomFieldWriteSafetyGates` from
  `hass.data[DOMAIN][entry.entry_id]["custom_field_write_safety"]` before any
  target read or merge. The gates are seeded from default-false implementation
  constants for `listing_partial_put_verified` and
  `reservation_no_clobber_verified`; tests must assert both target types reject
  while their gates are off.
- Reject `target_type: listing` with a clear user-facing message that listing
  custom-field writes are disabled until the verification ladder records
  passing evidence when `listing_partial_put_verified` is false.
- Reject `target_type: reservation` with a clear user-facing message that
  reservation custom-field writes are disabled until accepted production or
  live evidence is recorded when `reservation_no_clobber_verified` is false.
- Serialize concurrent Home Assistant writes through per-entry, per-target
  `asyncio.Lock` instances.
- Add a shared per-target write generation registry. Coordinators capture the
  generation before refresh and, before publishing, must not overwrite a target
  whose generation advanced during the refresh. They either merge the
  post-write custom-field override into the refreshed object or keep the
  previous post-write target until a later fresh refresh.
- Read the current object with `includeResources=1`, merge only the addressed
  value into the raw `customFieldValues`, preserve unresolved and raw malformed
  entries, and submit the safe payload selected for that target type.
  Automated tests must assert partial payloads contain exactly one top-level
  key, `customFieldValues`, while full-object payloads contain only fields
  proven reconstructable from the pre-write snapshot.
- Treat a present empty `customFieldValues: []` list as genuinely empty, but
  fail closed when the current object omits `customFieldValues`, returns it as
  `null`, or returns any non-list value. The parsed
  `HostawayCustomFieldCollection.state` must retain those distinctions as
  `present`, `missing`, `null`, or `invalid`; only `present` may enter the
  write merge. In all other states the service must raise a clear error and
  send no `PUT`, because treating the malformed collection as empty would
  clear every existing custom value on the Hostaway object.
- Before merging, scan the raw `customFieldValues` collection for every entry
  that carries the addressed `customFieldId`, including malformed entries that
  have an id but no `value` or otherwise do not match the expected entry
  shape. If any malformed raw entry targets the addressed id, fail closed
  before `PUT` with a clear malformed-entry error; do not replace it, append
  beside it, or drop it. If more than one raw entry targets that id, fail
  closed before `PUT`; do not append another duplicate and do not drop any raw
  entry. A deterministic replacement policy may be added only with explicit
  tests and evidence that Hostaway handles duplicates predictably.
- Refresh or patch affected local coordinator data after success so entities
  reflect the new value before the next scheduled poll.

### Phase 9: Documentation and UX clarity

- Update `services.yaml` for all three custom-field services, their selectors,
  response schemas, hidden-field behavior, task-field exclusion, and the
  built-in `doorCode` distinction.
- Update `set_door_code` documentation to state that it writes built-in
  reservation fields, not custom variables.

### Phase 10: Polish, validation, and release notes

- Run targeted tests, then the required full test and ruff commands.
- Run `uvx --from aislop==0.12.0 aislop ci` and keep the configured 100/100
  score with zero errors and zero warnings.
- Add changelog and task-checkbox commits separately during the implementation
  PR.

## Entity Key Allocation Algorithm

The implementation must persist the allocation in the entity registry rather
than relying only on in-memory order. Each listing custom-field entity uses a
unique ID that contains both the Hostaway custom field id and the allocated
key, for example:

```text
{entry.unique_id}_{listing_id}_custom_field_{customFieldId}_{allocated_key}
```

At startup and reload, the allocator scans the entity registry for this
config-entry/listing prefix, reconstructs `customFieldId -> allocated_key`, and
reserves every persisted key in the listing's shared custom-field namespace.
Those persisted mappings always win and are reused for sensors and service
responses, which prevents renaming after a restart, reload, user entity rename,
or later definition collision.

For a new observed listing value with no persisted mapping:

1. Build the listing definition slug index from cached `listing` definitions.
   Slugs use Home Assistant's `slugify(varName)`.
2. If the value has a definition and its slug is unique in the current
   listing definition set, use candidate `custom_<slug>`.
3. If the value has a definition but the slug collides with another listing
   definition before this field is allocated, use
   `custom_<slug>_<customFieldId>`.
4. If the value has no definition, use candidate
   `custom_field_<customFieldId>`.
5. If the candidate is already reserved by another custom field in this
   listing namespace, append `_<customFieldId>` to the base candidate.
6. If that appended key is also reserved by another field, append a
   deterministic numeric disambiguator:
   `<base>_<customFieldId>_2`, `<base>_<customFieldId>_3`, and so on until an
   unreserved key is found. Persisted mappings always win.
7. Persist the final key by creating the sensor with the unique ID above and
   suggested object id `hostaway_<listing_slug>_<allocated_key>`.

The value read service uses the same allocator in non-mutating mode. It seeds
from persisted entity-registry keys, reserves them, and computes response keys
for defined but unset `value: null` entries without creating sensors or
persisting new mappings.

## Implementation Sizing Notes

Current relevant file sizes:

| File | Lines | Plan |
|------|------:|------|
| `api/client.py` | 371 | Keep custom-field business logic out. |
| `api/reservations.py` | 142 | Small includeResources change only. |
| `api/models.py` | 421 | Extend models surgically; no split required. |
| `coordinator.py` | 183 | Add coordinator here if it remains cohesive. |
| `services/__init__.py` | 154 | Table entries only. |
| `services/helpers.py` | 216 | Reuse entry resolver; avoid bloat. |
| `services/schemas.py` | 237 | Add schemas here if it remains cohesive. |
| `sensor/__init__.py` | 96 | Add wiring carefully or extract helpers. |
| `sensor/listing.py` | 140 | Keep existing diagnostics unchanged. |
| `sensor/reservation.py` | 180 | Minimal wiring; helpers own shaping. |
| `sensor/helpers.py` | 189 | Add only small attribute hook. |
| `config_flow.py` | 424 | Add options carefully or extract for clarity. |
| `config_options.py` | new | Optional helper module if extraction helps. |

These counts are sizing context only. The repository's actual
`.aislop/config.yml` enforces `ci.failBelow: 100`; it does not define a
file-level line-count limit. Keep modules cohesive, use the new custom-field
modules for separation of concerns, and run
`uvx --from aislop==0.12.0 aislop ci` to preserve the configured score gate.
Do not add custom-field business logic to `api/client.py`.

Allocator tests must cover duplicate `varName` values, slug collisions,
fallback-key collisions, a second-order collision where the first
`_<customFieldId>` suffix is already reserved, and restart/reload
reconstruction from entity-registry unique IDs.

## Complexity Tracking

No constitution violations are planned. The elevated risks are Hostaway's
whole-object listing update endpoint and the reservation endpoint's
custom-field no-clobber behavior. Those risks are addressed by explicit,
default-off executable gates for FR-035 and FR-055. Writes remain disabled per
target type until the corresponding listing ladder evidence or accepted
reservation production evidence is recorded and its gate is enabled.
