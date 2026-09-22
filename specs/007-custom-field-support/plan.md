<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Implementation Plan: Custom Field Support

**Branch**: `007-custom-field-plan` | **Date**: 2026-09-22 |
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
collection, preserve unresolved and malformed raw entries, and submit the
merged collection through the existing whole-object `PUT` endpoint. Listing
write support is gated by an early empirical verification task proving that a
partial `PUT /v1/listings/{id}` preserves omitted built-in fields; if that
verification fails, listing writes fail closed until a safe full-payload
strategy is implemented.

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
`custom_components/hostaway/api/custom_fields.py`; every file stays under
aislop's 400-line cap; writes must preserve raw malformed custom field value
records and built-in fields; multi-account services fail closed without
`config_entry_id`

**Scale/Scope**: Account-level custom field definitions for `listing` and
`reservation` object types, dynamic listing custom-field sensors, reservation
custom-field attributes, three services, and tests for malformed data,
concurrency, response schemas, and no-clobber write behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

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

**Post-Phase 1 re-check**: PASS. The design keeps Hostaway API logic
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
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── __init__.py                    # Add definitions coordinator setup/shutdown
├── config_flow.py                 # Add definitions scan interval option
├── config_options.py              # New options-flow helpers if needed for cap
├── const.py                       # Add custom-field option/service constants
├── coordinator.py                 # Add HostawayCustomFieldsCoordinator
├── api/
│   ├── client.py                  # Add thin delegates only if line budget allows
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
adding methods to `api/client.py`, which is already 371 lines and would exceed
the 400-line aislop cap. The module receives a `HostawayApiClient` or narrow
request protocol by dependency injection and contains no Home Assistant
imports, matching the existing `api/reservations.py` separation and Guesty's
library-extractable R-005 decision. Home Assistant service handlers live in a
new `services/custom_fields.py` module so `services/__init__.py` remains
table-driven.

## Phase Overview

### Phase 0: API safety verification and research lock-in

- Write a real-account verification task that reads a listing with at least
  three populated custom fields and representative built-in fields, sends a
  partial `PUT /v1/listings/{id}` containing only a harmless custom-field merge
  candidate, then re-reads the listing with `includeResources=1`.
- Treat this task as blocking for listing writes. It must prove omitted
  built-in fields and unrelated custom values remain unchanged before any
  listing write path can be enabled.
- If partial listing PUT is destructive, implement reservation writes and read
  support, but make listing writes fail closed until a safe full payload can be
  built from the current listing snapshot and proven no-clobber.
- Write a real-account verification task for reservation writes that reads a
  reservation with at least three populated reservation custom fields and at
  least one built-in field such as `doorCode`, sends a merged
  `customFieldValues` update for one harmless custom field, then re-reads the
  reservation with `includeResources=1`.
- Treat this task as blocking for reservation writes. It must prove the target
  custom field changed while every unrelated reservation custom field and each
  visible built-in reservation field remain unchanged.

### Phase 1: API models, parsing, and includeResources reads

- Add `HostawayCustomFieldDefinition`, parsed value records, unresolved value
  records, raw malformed value preservation, and response-shaping helpers.
- Add paginated `GET /v1/customFields` retrieval with `limit=500`, optional
  `objectType`, and filtering that ignores `task`.
- Update listing and reservation reads to include `includeResources=1` so
  `customFieldValues` is populated.
- Add direct target reads for `GET /v1/listings/{id}?includeResources=1` and
  `GET /v1/reservations/{id}?includeResources=1` so services can satisfy
  `target_id`-only read and write contracts without requiring `listing_id` or
  scanning account-wide collections.
- Mirror `parse_reservations` by skipping malformed presentation records with
  warnings while preserving their raw entries for write merges.

### Phase 2: Definitions coordinator and options flow

- Add `custom_field_definitions_scan_interval`, default 15 minutes, minimum
  one minute, as a required integer minutes option in the existing options
  flow.
- Create a per-config-entry `HostawayCustomFieldsCoordinator` that fetches and
  caches definitions without blocking listing or reservation coordinator
  refreshes when definitions fail.
- Store the coordinator in `hass.data[DOMAIN][entry.entry_id]` and shut it
  down during unload.

### Phase 3: Entity surfaces and deterministic key allocation

- Add a restart-stable listing custom-field key allocator seeded from the Home
  Assistant entity registry and the current listing definitions.
- Create one diagnostic listing sensor per present listing custom field value
  without changing the seven existing listing diagnostic sensors.
- Add `custom_fields` to reservation sensor attributes, keyed by
  `custom_field_<customFieldId>`, returning `{}` when no values exist.
- Add runtime discovery for new listing custom field values observed by the
  listings coordinator.

### Phase 4: Read services

- Register `hostaway.get_custom_fields` with `SupportsResponse.ONLY`.
- Register `hostaway.get_custom_field_values` with `SupportsResponse.ONLY`.
- Reuse `_resolve_entry_data` so services select the sole entry or fail closed
  with `config_entry_id required when multiple entries exist`.
- Shape responses exactly as specified, including defined fields with
  `value: null` and unresolved values without definition metadata.

### Phase 5: Write service and no-clobber merge

- Register `hostaway.set_custom_field` with `SupportsResponse.OPTIONAL`.
- Validate exactly one of `customFieldId` or `varName`; require definitions for
  all writes; fail ambiguous same-object-type `varName` resolutions.
- Serialize concurrent Home Assistant writes through per-entry, per-target
  `asyncio.Lock` instances.
- Read the current object with `includeResources=1`, merge only the addressed
  value into the raw `customFieldValues`, preserve unresolved and raw malformed
  entries, and submit the safe payload.
- Refresh or patch affected local coordinator data after success so entities
  reflect the new value before the next scheduled poll.

### Phase 6: Documentation and validation

- Update `services.yaml` for all three custom-field services, their selectors,
  response schemas, hidden-field behavior, task-field exclusion, and the
  built-in `doorCode` distinction.
- Update `set_door_code` documentation to state that it writes built-in
  reservation fields, not custom variables.
- Run targeted tests, then the required full test and ruff commands.

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

## Line Budget

Current relevant file sizes:

| File | Lines | Plan |
|------|------:|------|
| `api/client.py` | 371 | Avoid adding custom-field logic here. |
| `api/reservations.py` | 142 | Small includeResources change only. |
| `coordinator.py` | 183 | Add coordinator or split if near 400. |
| `services/__init__.py` | 154 | Table entries only. |
| `services/helpers.py` | 216 | Reuse entry resolver; avoid bloat. |
| `services/schemas.py` | 237 | Add schemas; split if near 400. |
| `sensor/__init__.py` | 96 | Add wiring carefully or extract helpers. |
| `sensor/listing.py` | 140 | Keep existing diagnostics unchanged. |
| `sensor/reservation.py` | 180 | Minimal wiring; helpers own shaping. |
| `sensor/helpers.py` | 189 | Add only small attribute hook. |
| `config_flow.py` | 424 | Split before adding the new option. |
| `config_options.py` | new | Move options-flow helpers here if needed. |

Because `config_flow.py` already exceeds aislop's cap, implementation tasks
must first split options-flow helpers into a new module and reduce
`config_flow.py` below the cap before adding
`custom_field_definitions_scan_interval`. Do not add more lines to
`api/client.py`.

Allocator tests must cover duplicate `varName` values, slug collisions,
fallback-key collisions, a second-order collision where the first
`_<customFieldId>` suffix is already reserved, and restart/reload
reconstruction from entity-registry unique IDs.

## Complexity Tracking

No constitution violations are planned. The only elevated risk is Hostaway's
whole-object listing update endpoint. That risk is addressed by the explicit
blocking FR-035 verification and the fail-closed fallback for listing writes.
