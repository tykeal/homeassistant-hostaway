<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Tasks: Custom Field Support

**Input**: Design documents from `/specs/007-custom-field-support/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
contracts/ ✅, quickstart.md ✅

**Tests**: Required for every production behavior by Constitution Principle I.
Write failing tests first, confirm they fail for the intended reason, then
implement the minimum code and refactor with tests green.

**Organization**: Tasks are grouped by phase and user story so each increment
has a testable checkpoint. The write path has two blocking safety evidence
gates that must remain default-off until the matching evidence task passes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story or cross-cutting area the task supports
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/hostaway/`
- **API module**: `custom_components/hostaway/api/custom_fields.py`
- **Services**: `custom_components/hostaway/services/`
- **Sensors**: `custom_components/hostaway/sensor/`
- **Tests**: `tests/`
- **Docs**: `custom_components/hostaway/services.yaml`, `CHANGELOG.md`

---

## Phase 1: Setup and Guardrails

**Purpose**: Create safe extension points, quality guardrails, and the
default-off write-gate objects before any user story work starts.

**Phase Exit Rule**: New files exist with SPDX headers, no Home Assistant
imports are present in `api/custom_fields.py`, and
`uvx --from aislop==0.12.0 aislop ci` remains at the configured 100/100 score.

- [x] T001 Run `uvx --from aislop==0.12.0 aislop ci` from the repository root and record in the implementation notes that the repository scores 100/100 with zero errors and zero warnings; do not add a file-level line-count rule to `.aislop/config.yml`
- [x] T002 Create `custom_components/hostaway/api/custom_fields.py` with SPDX header, aislop ignore marker (`# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages`), module docstring, zero Home Assistant imports, narrow client protocols, and placeholder dataclasses/helpers for definitions, values, collection state, write gates, locks, and generations
- [x] T003 [P] Create `custom_components/hostaway/services/custom_fields.py` with SPDX header, aislop ignore marker, module docstring, and placeholder handlers for `hostaway.get_custom_fields`, `hostaway.get_custom_field_values`, and `hostaway.set_custom_field`
- [x] T004 [P] Create `custom_components/hostaway/sensor/custom_fields.py` with SPDX header, aislop ignore marker, module docstring, and placeholder listing custom-field allocator/sensor classes
- [x] T005 [P] Add the custom-field options-flow extension point in `custom_components/hostaway/config_flow.py`; create `custom_components/hostaway/config_options.py` only if extracting shared options helpers improves separation of concerns, and do not touch the credential-entry flow for this feature
- [x] T006 [P] Add test module skeletons with SPDX headers, aislop ignore markers, and module docstrings in `tests/api/test_custom_fields.py`, `tests/sensor/test_custom_fields.py`, `tests/services/__init__.py`, and `tests/services/test_custom_fields.py`
- [x] T007 Write failing tests in `tests/api/test_custom_fields.py` for `CustomFieldWriteSafetyGates` defaulting `listing_partial_put_verified = False` and `reservation_no_clobber_verified = False`, then add the defaults in `custom_components/hostaway/api/custom_fields.py`; do not add any code path that can override them yet

**Checkpoint**: Setup complete — extension modules exist, `config_flow.py` has
room for options work, and executable write gates are present but default-off.

---

## Phase 2: Foundational API Models and Reads

**Purpose**: Build the Home-Assistant-independent custom-field API layer that
all sensors, services, and safety verifications depend on.

**Phase Exit Rule**: API parsing, definition pagination, direct reads, and
`includeResources=1` behavior are covered by tests and do not depend on Home
Assistant imports.

**⚠️ CRITICAL**: No entity, service, or write-path work can begin until this
phase is complete.

### Tests for Foundational API

> **NOTE: Write these tests FIRST and verify they fail before implementation.**

- [x] T008 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` for `HostawayCustomFieldDefinition.from_api_dict`, covering valid listing/reservation definitions, ignored `task` definitions, hidden `isPublic=0` fields, dropdown `possibleValues`, unknown future types, malformed records skipped with warnings, and bool-safe id rejection
- [x] T009 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` for `HostawayCustomFieldValue` and `HostawayCustomFieldCollection`, covering present values, explicit `value: None`, present empty `[]`, missing `customFieldValues`, `null`, non-list invalid state, malformed entries skipped for presentation, raw malformed entry preservation, and `tests/api/test_models.py` model-factory mapping of parsed presentation values plus raw collections while preserving existing listing/reservation field behavior
- [x] T010 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` for duplicate raw entries and addressed malformed-entry detection, including malformed raw entries with the addressed `customFieldId`
- [x] T011 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` for paginated `GET /v1/customFields?limit=500&offset=...`, stopping by pagination metadata or short page, and filtering out `task` definitions
- [x] T012 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` proving listing page reads, normal `HostawayApiClient.get_all_listings()` / `_paginate_offset` listing coordinator pages, reservation page reads, direct listing reads, and direct reservation reads all include `includeResources=1` on every page/request
- [x] T013 [P] [API] Write failing tests in `tests/api/test_custom_fields.py` proving reservation pagination stays keyed off raw reservation ids when `includeResources=1` is added

### Implementation for Foundational API

- [x] T014 [API] Implement bool-safe integer validation helpers in `custom_components/hostaway/api/custom_fields.py` for definition ids, value-entry `customFieldId`, service `customFieldId`, and `target_id`; reject `True` and `False` everywhere identifiers are expected
- [x] T015 [API] Implement `HostawayCustomFieldDefinition` parsing and response-shaping helpers in `custom_components/hostaway/api/custom_fields.py`
- [x] T016 [API] Implement `HostawayCustomFieldValue` and `HostawayCustomFieldCollection` in `custom_components/hostaway/api/custom_fields.py`, preserving raw entries and distinguishing `present`, `missing`, `null`, and `invalid`
- [x] T017 [API] Implement duplicate-id and addressed-malformed-entry preflight checks in `custom_components/hostaway/api/custom_fields.py`; fail closed before any merge can produce a `PUT` payload
- [x] T018 [API] Implement paginated custom-field definition retrieval in `custom_components/hostaway/api/custom_fields.py` using `limit=500`, offset pagination, rate-limited client transport, and warning-only skips for malformed definitions
- [x] T019 [API] Add direct single-object read helpers for `GET /v1/listings/{id}?includeResources=1` and `GET /v1/reservations/{id}?includeResources=1` without reusing list-only `_request_results`
- [x] T020 [API] Update listing pagination in `custom_components/hostaway/api/client.py` with the smallest possible transport-only change so `get_all_listings()` and the `_paginate_offset` path request `includeResources=1` on every page
- [x] T021 [API] Update `custom_components/hostaway/api/reservations.py` so reservation page reads request `includeResources=1` and pagination remains keyed from raw response items
- [x] T022 [API] Extend listing and reservation models in `custom_components/hostaway/api/models.py` to carry parsed presentation values and raw custom-field collections without changing existing built-in fields

**Checkpoint**: Foundational API ready — direct and paginated reads expose raw
and parsed custom-field data with `includeResources=1`, malformed records do
not fail coordinator parsing, and all API tests pass.

---

## Phase 3: Definitions Coordinator and Options

**Purpose**: Cache account-scoped definitions, expose a configurable refresh
interval, and preserve stale definitions for reads while blocking writes after
refresh failures.

**Phase Exit Rule**: Config entry setup never fails solely because definitions
refresh failed, stale definitions remain usable for reads, and writes can
detect the latest refresh failure.

### Tests for Definitions Infrastructure

- [x] T023 [P] [Coordinator] Write failing tests in `tests/test_config_flow.py` for `custom_field_definitions_scan_interval` options-flow defaults, required integer-minute selector, 15-minute default, one-minute minimum, and matching `strings.json` / `translations/en.json` labels
- [x] T024 [P] [Coordinator] Write failing tests in `tests/test_init.py` proving `hass.data[DOMAIN][entry.entry_id]` remains the existing dict, receives new dedicated keys for `custom_fields_coordinator`, `custom_field_write_safety`, write locks, and write generations, shuts the definitions coordinator down on unload, and keeps two config entries' runtime definitions isolated
- [x] T025 [P] [Coordinator] Write failing tests in `tests/test_coordinator.py` for a definitions coordinator that starts with `last_refresh_succeeded = False`, sets it true after success, stores `last_refresh_error` after failure, and retains stale cached definitions for reads
- [x] T026 [P] [Coordinator] Write failing tests in `tests/test_init.py` proving definitions refresh failure does not abort config entry setup or listing/reservation coordinator refreshes, and that listing/reservation refreshes never call `GET /v1/customFields` per listing, reservation, or field
- [x] T027 [P] [Coordinator] Write failing service-level tests in `tests/services/test_custom_fields.py` proving writes reject with `custom field definitions refresh failed; writes are disabled until the next successful refresh` whenever the most recent definitions refresh failed, even if stale definitions are cached; reads must still use stale cached definitions without extra definition requests

### Implementation for Definitions Infrastructure

- [x] T028 [Coordinator] Add `custom_field_definitions_scan_interval` constants in `custom_components/hostaway/const.py`, defaulting to 15 minutes with the existing minimum scan interval
- [x] T029 [Coordinator] Implement the options-flow field in `custom_components/hostaway/config_flow.py`; use `custom_components/hostaway/config_options.py` only if T005 chose to extract shared options helpers
- [x] T030 [Coordinator] Add matching options-flow strings to `custom_components/hostaway/strings.json` and `custom_components/hostaway/translations/en.json`
- [x] T031 [Coordinator] Implement `HostawayCustomFieldsCoordinator` in `custom_components/hostaway/coordinator.py`, keeping all definition retrieval isolated to the coordinator interval
- [x] T032 [Coordinator] Wire `HostawayCustomFieldsCoordinator` into `custom_components/hostaway/__init__.py` under a new key inside `hass.data[DOMAIN][entry.entry_id]` without replacing that mapping, and explicitly call its shutdown method from the unload path alongside the listing and reservation coordinators before the per-entry data is popped
- [x] T033 [Coordinator] Store `CustomFieldWriteSafetyGates`, per-target write locks, and write-generation registry under dedicated keys in the same per-entry runtime dict

**Checkpoint**: Definitions infrastructure complete — reads can use cached or
stale definitions, setup survives definition outages, and write services have
the state required to fail closed.

---

## Phase 4: Blocking Live Verification Gates

**Purpose**: Schedule and execute the listing verification ladder and
reservation evidence recording before either write target type can be enabled.

**Phase Exit Rule**: The implementation records explicit target-specific
evidence for each enabled write gate, or keeps that target type's write gate
disabled with an actionable rejection path. These tasks must happen before
Phase 8 enables any `hostaway.set_custom_field` mutation for the corresponding
target type.

**⚠️ BLOCKING**: Listing writes depend on the full T038 ladder. Reservation
writes depend on reservation `customFieldValues` no-op/sentinel/restore
evidence or an authoritative Hostaway contract. Do not flip either gate and do
not send a write for that target type until its required evidence is recorded.

- [x] T034 [P] [Safety] Write failing handler tests in `tests/services/test_custom_fields.py` proving `async_handle_set_custom_field` rejects `target_type: listing` before target reads, merges, or `PUT` while `listing_partial_put_verified` is false; call the handler directly because the service schema and registration arrive in Phases 6 and 8. T096 extends this baseline gate coverage for explicit payload-strategy state before T093 changes dispatch semantics.
- [x] T035 [P] [Safety] Write failing handler tests in `tests/services/test_custom_fields.py` proving `async_handle_set_custom_field` rejects `target_type: reservation` before target reads, merges, or `PUT` while `reservation_no_clobber_verified` is false; call the handler directly because the service schema and registration arrive in Phases 6 and 8
- [x] T036 [Safety] Implement target-type gate checks in `custom_components/hostaway/services/custom_fields.py` with user-facing rejection messages that say custom-field writes are disabled until target-specific safety evidence is recorded, before any read, merge, or mutation call
- [x] T037 [Safety] Write mocked tests for live-verification safety utilities in `tests/api/test_custom_fields.py` and `tests/scripts/test_verify_custom_field_writes.py`, covering the production merge/partial-payload builder, outgoing partial `PUT` top-level keys exactly equal to `{"customFieldValues"}`, no committed credentials, redaction, private snapshot storage outside git, restore-on-failure, and no-mutation-on-preflight-error paths; implement the production partial-payload builder in `custom_components/hostaway/api/custom_fields.py`, then add executable helper `scripts/verify_custom_field_writes.py` with SPDX header as a thin wrapper that imports that builder instead of hand-rolling payloads; it must use disposable/test objects, read with `includeResources=1`, capture complete private restorable snapshots outside git, log only redacted summaries, mutate one harmless custom field, re-read, compare all unrelated custom values and visible built-in fields, and restore values per quickstart.md. T096 upgrades this verification to canonicalized complete-snapshot comparison before the new protocol can be used for enablement.
- [ ] T096 [API] Write failing tests for the full-object payload builder, per-target writable-field allowlists and normalization rules, rejection of unsafe GET-response deep copies, per-target strategy selection, explicit payload-strategy gate state, the selected concurrent-edit condition (documented outside-guarantee semantics or conditional/version-check rejection when that documentation is not the chosen gate), `--snapshot` mode, allowlisted restore-payload reconstruction, canonicalized complete-snapshot comparison with server-managed volatile-field normalization, account-bound reservation evidence, reservation fail-closed behavior until `customFieldValues` no-op/sentinel/restore or authoritative contract evidence exists, task-canary mode, and multi-entry custom-value preservation beyond the single live variable case in `tests/api/test_custom_fields.py` and `tests/scripts/test_verify_custom_field_writes.py` before implementing T093 through T095
- [ ] T094 [Safety] Add `--snapshot` mode to `scripts/verify_custom_field_writes.py` so the verifier captures a complete target snapshot outside git, validates that an allowlisted restore payload is reconstructable before any mutation, refuses to use raw GET-response deep copies as restore payloads, and records only redacted summaries in `specs/007-custom-field-support/live-verification.md`
- [ ] T095 [Safety] Add task-canary mode to `scripts/verify_custom_field_writes.py` so it can create a disposable Hostaway task, send partial `PUT /v1/tasks/{id}`, verify unrelated task fields survive, and delete the task while reporting that task results are indicative rather than conclusive for listing semantics
- [ ] T038 [Safety] Run the authorized zero-risk and negligible-risk listing verification ladder after CI is green: Step 0 ask Hostaway support for authoritative `PUT /v1/listings/{id}` semantics (owner action, zero risk); Step 1 capture a complete listing snapshot and verify an allowlisted restore payload is reconstructable without deep-copying the GET response (read-only, zero risk); Step 2 inspect the generated dry-run payload from `scripts/verify_custom_field_writes.py` (zero risk, because dry-run is the default); Step 3 run the disposable task canary by creating a throwaway task, sending partial `PUT /v1/tasks/{id}`, verifying unrelated task fields survive, and deleting the task (negligible risk and indicative only). Record redacted evidence in `specs/007-custom-field-support/live-verification.md`. Do not enable `listing_partial_put_verified` yet; Step 4 listing no-op self-write with an expected empty whole-object diff and Step 5 listing sentinel write/verify/restore to snapshot require a disposable listing or the allowlisted restore path plus a separate explicit owner decision before they run. If the live listing has only one populated custom variable, record that additional populated custom-value preservation was not observed and do not enable the partial custom-field preservation strategy until live multi-entry evidence or an authoritative Hostaway contract exists. If partial listing verification fails and a full-object listing strategy is selected instead, repeat Steps 4 and 5 with the reconstructed full-object payload, record that full-object evidence, and either document concurrent external dashboard edits after the pre-write read as outside the guarantee or require Hostaway conditional/version detection before enabling listing writes.
- [ ] T039 [Safety] Record the reservation `doorCode` implementation evidence in `specs/007-custom-field-support/live-verification.md` and bind any owner-provided external production history to the verified Hostaway account/config entry as top-level merge evidence only. The evidence must cite `custom_components/hostaway/services/reservation_handlers.py` sending only `doorCode` plus optional `doorCodeVendor` and `doorCodeInstruction` through `HostawayApiClient.update_reservation`, state that this repository does not substantiate any v0.4.0 production release or no-data-loss history, and record the residual gap that reservation `customFieldValues` round-tripping is not yet proven. Do not set `reservation_payload_strategy` or enable `reservation_no_clobber_verified` until reservation `customFieldValues` no-op/sentinel/restore evidence or an authoritative Hostaway contract covers that payload. Other Hostaway accounts/config entries remain disabled until their own account-bound evidence is recorded.

**Checkpoint**: Live safety status known — each target type is either verified
and explicitly enabled by evidence, or remains disabled with tests proving the
fail-closed path.

---

## Phase 5: User Story 1 - Read Custom Variables from Sensors (P1) 🎯 MVP

**Goal**: Listing custom variables appear as dynamic per-field diagnostic
sensors, and reservation custom variables appear under the existing
reservation sensor's `custom_fields` attribute.

**Independent Test**: Populate listing and reservation custom fields in
fixtures, refresh coordinators, and verify listing sensors plus reservation
attributes match the spec without changing existing entity behavior.

### Tests for User Story 1

- [ ] T040 [P] [US1] Write failing tests in `tests/sensor/test_custom_fields.py` for the listing key allocator covering unique `varName`, duplicate `varName`, slug collisions, fallback `custom_field_<customFieldId>` keys, fallback collisions, second-order suffix collisions, persisted entity-registry mappings, no renaming after restart/reload, and the runtime transition where an unresolved fallback-key sensor later resolves metadata without renaming or creating a second sensor
- [ ] T041 [P] [US1] Write failing tests in `tests/sensor/test_custom_fields.py` proving dynamic listing custom-field sensors are created only for present values, hidden fields are included, unresolved values use fallback metadata, resolved values expose `customFieldId`, `varName`, `name`, `type`, `possibleValues`, `value`, and `resolved: true`, and cleared existing values remain present with native value `None`
- [ ] T042 [P] [US1] Write failing tests in `tests/sensor/test_custom_fields.py` proving new listing custom-field values observed at runtime create new sensors without a Home Assistant restart
- [ ] T043 [P] [US1] Write failing tests in `tests/sensor/test_listing.py` proving the seven existing listing diagnostics (`listing_id`, `external_name`, `status`, `base_price`, `bedrooms`, `bathrooms`, `max_guests`) remain unchanged and do not gain custom-variable attributes
- [ ] T044 [P] [US1] Write failing tests in `tests/sensor/test_reservation.py` proving reservation sensors add `custom_fields`, use `custom_field_<customFieldId>` keys, include resolved human-readable metadata, include unresolved entries without definition metadata, expose `{}` when empty, and keep all existing reservation attributes unchanged
- [ ] T045 [P] [US1] Write failing tests in `tests/api/test_custom_fields.py` proving malformed custom value records log warnings that exclude raw custom-field data and values, skip only presentation, preserve raw merge data, and never fail listing or reservation coordinator refreshes

### Implementation for User Story 1

- [ ] T046 [US1] Implement `ListingCustomFieldKeyAllocation` in `custom_components/hostaway/sensor/custom_fields.py`, seeding from entity-registry unique IDs and allocating from one shared namespace per listing
- [ ] T047 [US1] Implement `HostawayListingCustomFieldSensor` in `custom_components/hostaway/sensor/custom_fields.py` with diagnostic metadata for resolved and unresolved fields
- [ ] T048 [US1] Wire dynamic listing custom-field sensor discovery into `custom_components/hostaway/sensor/__init__.py` without changing the existing seven listing sensor descriptions
- [ ] T049 [US1] Extend reservation attribute shaping in `custom_components/hostaway/sensor/helpers.py` so `custom_fields` is always present and follows FR-014 through FR-018
- [ ] T050 [US1] Run `uv run pytest tests/sensor/ -x -q` and confirm listing custom-field sensors, runtime discovery, reservation `custom_fields`, and existing sensor regressions pass before starting read services

**Checkpoint**: Sensor read surface complete — custom values are visible on
entities within one poll interval, dynamic listing sensors are restart-stable,
and existing listing/reservation behavior remains unchanged except for the new
reservation `custom_fields` attribute.

---

## Phase 6: User Story 2 - Read Custom Variables from Services (P1)

**Goal**: Automations can retrieve cached definitions and target-specific
custom values through response-returning services.

**Independent Test**: Call `hostaway.get_custom_fields` and
`hostaway.get_custom_field_values` against fixtures for listing and
reservation targets and verify exact response envelopes.

### Tests for User Story 2

- [ ] T051 [P] [US2] Write failing schema tests in `tests/services/test_custom_fields.py` for `SERVICE_GET_CUSTOM_FIELDS_SCHEMA` and `SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA`, including bool-safe `target_id` rejection and target type limited to `listing` / `reservation`
- [ ] T052 [P] [US2] Write failing tests in `tests/services/test_custom_fields.py` proving `get_custom_fields` is registered with `SupportsResponse.ONLY`, uses `_resolve_entry_data`, returns exactly `{"custom_fields": [...]}`, each entry contains exactly `customFieldId`, `varName`, `name`, `type`, `objectType`, `possibleValues`, `isPublic`, and `sortOrder`, includes listing and reservation definitions, ignores task definitions, and returns `{"custom_fields": []}` for an empty cache
- [ ] T053 [P] [US2] Write failing tests in `tests/services/test_custom_fields.py` proving missing `config_entry_id` fails closed with `config_entry_id required when multiple entries exist` before any read when multiple entries are loaded, and explicit `config_entry_id` selection for two loaded entries returns only that entry's definitions/metadata so accounts cannot label or validate each other's data
- [ ] T054 [P] [US2] Write failing tests in `tests/services/test_custom_fields.py` proving `get_custom_field_values` is registered with `SupportsResponse.ONLY`, performs direct target reads with `includeResources=1`, returns exactly `{"custom_fields": {...}}`, resolved entries contain exactly `customFieldId`, `varName`, `name`, `type`, `possibleValues`, `value`, and `resolved`, unresolved entries contain exactly `customFieldId`, `value`, and `resolved`, defined-but-unset fields include `value: None`, and empty results return `{"custom_fields": {}}`
- [ ] T055 [P] [US2] Write failing tests in `tests/services/test_custom_fields.py` proving listing value-service response keys reuse persisted sensor keys, reserve other persisted keys, allocate unset fields in non-mutating mode without creating entities, and suffix collisions deterministically
- [ ] T056 [P] [US2] Write failing tests in `tests/services/test_custom_fields.py` proving inaccessible or missing listings/reservations raise clear errors naming the target type and id

### Implementation for User Story 2

- [ ] T057 [US2] Add schemas for all three custom-field services to `custom_components/hostaway/services/schemas.py`, including `SERVICE_SET_CUSTOM_FIELD_SCHEMA`
- [ ] T058 [US2] Implement `async_handle_get_custom_fields` in `custom_components/hostaway/services/custom_fields.py`
- [ ] T059 [US2] Implement `async_handle_get_custom_field_values` in `custom_components/hostaway/services/custom_fields.py`, using direct reads, cached definitions, and the listing allocator in non-mutating mode
- [ ] T060 [US2] Register `hostaway.get_custom_fields` and `hostaway.get_custom_field_values` in `custom_components/hostaway/services/__init__.py` with `SupportsResponse.ONLY`

**Checkpoint**: Read services complete — definitions and values return the
exact envelopes from the spec, multi-account resolution fails closed, and
direct target reads do not require scanning selected listings.

---

## Phase 7: User Story 4 - Address Fields by Name or ID (P2)

**Goal**: Shared resolution and validation supports both human-readable
`varName` and numeric `customFieldId` addressing before write implementation.

**Independent Test**: Resolve the same field by unique `varName` and by
numeric id, and verify ambiguous or unknown identifiers fail before mutation.

### Tests for User Story 4

- [ ] T061 [P] [US4] Write failing tests in `tests/api/test_custom_fields.py` for definition lookup by `customFieldId`, object-type scoping, unknown ids, duplicate same-object-type `varName`, duplicate cross-object-type `varName`, slug-identical names, and unknown `varName`
- [ ] T062 [P] [US4] Write failing tests in `tests/services/test_custom_fields.py` proving `SERVICE_SET_CUSTOM_FIELD_SCHEMA` and `hostaway.set_custom_field` validation require a present `value` key, accept explicit `value: None` as clear, reject omitted `value` before reads or writes, limit `target_type` to `listing` / `reservation`, and reject missing identifiers, both identifiers, boolean `customFieldId`, boolean `target_id`, unknown ids, unknown `varName`, and ambiguous same-object-type `varName` before any write
- [ ] T063 [P] [US4] Write failing tests in `tests/services/test_custom_fields.py` proving `text` and `textarea` require strings, `number` accepts non-bool numbers and rejects booleans/non-numbers, `dropdown` accepts only normalized `possibleValues`, `value: None` clears and bypasses type validation, and unknown future field types pass through to Hostaway

### Implementation for User Story 4

- [ ] T064 [US4] Implement definition indexes and `resolve_var_name` / id lookup helpers in `custom_components/hostaway/api/custom_fields.py`, scoped by `listing` and `reservation`, so service-layer tests can exercise the same API helper through the handler
- [ ] T065 [US4] Implement shared set-service identifier validation in `custom_components/hostaway/services/custom_fields.py`, requiring exactly one of `varName` or `customFieldId`
- [ ] T066 [US4] Implement local value validation in `custom_components/hostaway/api/custom_fields.py` for known field types, with boolean-safe number handling and dropdown `possibleValues` checks

**Checkpoint**: Field addressing complete — all resolution and validation
failures happen before target reads or any mutating request.

---

## Phase 8: User Story 3 - Write Without Clobbering Others (P1)

**Goal**: `hostaway.set_custom_field` writes exactly one verified target
custom variable while preserving every unaddressed custom value, malformed raw
entry, and visible built-in field.

**Independent Test**: With the relevant safety evidence gate enabled, call
`hostaway.set_custom_field` for one listing or reservation value and confirm
the target changed, every other custom value and built-in field is unchanged,
and the local entity surface updates immediately.

**Gate Dependencies**: Listing write enablement depends on T038. Reservation
write enablement depends on T039. If either verification fails, keep that
target type disabled and complete only the fail-closed behavior for it.

### Tests for User Story 3

- [ ] T067 [P] [US3] Write failing tests in `tests/api/test_custom_fields.py` for read-modify-write merge preserving unaddressed values, unresolved values, raw malformed entries, Hostaway order where practical, explicit `value: None` clears, per-target payload strategy selection, partial outgoing `PUT` payload top-level keys exactly equal to `{"customFieldValues"}`, and full-object payloads that include only fields proven safe and reconstructable from the pre-write snapshot
- [ ] T068 [P] [US3] Write failing tests in `tests/api/test_custom_fields.py` proving write merge fails closed when `customFieldValues` is missing, `null`, or non-list; a present `[]` is accepted as genuinely empty
- [ ] T069 [P] [US3] Write failing tests in `tests/api/test_custom_fields.py` proving write merge fails closed when the addressed id has a malformed raw entry or duplicate raw entries, and sends no `PUT`
- [ ] T070 [P] [US3] Write failing tests in `tests/services/test_custom_fields.py` proving `set_custom_field` is registered with `SupportsResponse.OPTIONAL` and returns exactly `target_type`, `target_id`, `customFieldId`, `varName`, `addressed_by`, and `result: success` when a response is requested
- [ ] T071 [P] [US3] Write failing tests in `tests/services/test_custom_fields.py` proving `set_custom_field` uses `_resolve_entry_data`, fails closed for missing `config_entry_id` with multiple entries before field resolution or reads, rejects writes after a failed definitions refresh, and does not use stale definitions for writes
- [ ] T072 [P] [US3] Write failing tests in `tests/services/test_custom_fields.py` proving per-entry/per-target locks serialize concurrent Home Assistant writes and each successful write reads current data after the previous write
- [ ] T073 [P] [US3] Write failing tests in `tests/services/test_custom_fields.py` proving Hostaway API errors, inaccessible targets, and failed pre-write reads raise clear Home Assistant errors and do not publish local success state
- [ ] T074 [P] [US3] Write failing tests in `tests/sensor/test_custom_fields.py` and `tests/sensor/test_reservation.py` proving successful writes patch local listing sensor or reservation attribute data immediately, keep cleared listing sensors present, do not require an entity to exist for service success, and prevent an in-flight listing or reservation refresh that captured a pre-write generation from overwriting the post-write custom-field value

### Implementation for User Story 3

- [ ] T093 [API] Implement a full-object payload builder in `custom_components/hostaway/api/custom_fields.py` alongside the existing `build_custom_field_values_payload`, with per-target writable-field allowlists, normalization rules, explicit rejection of unsafe GET-response deep copies, and explicit per-target-type strategy selection state so listing and reservation writes can choose partial or full-object payloads based on recorded verification evidence without representing full-object fallback as partial-PUT verification
- [ ] T075 [US3] Integrate the no-clobber merge helpers from T037 and the full-object strategy from T093 into the write dispatch path in `custom_components/hostaway/api/custom_fields.py`, using raw current `customFieldValues` read immediately before the write, preserving unaddressed raw entries unchanged, and selecting the payload strategy per target type from recorded evidence
- [ ] T076 [US3] Implement per-entry/per-target lock acquisition and write-generation advancement in `custom_components/hostaway/services/custom_fields.py`
- [ ] T077 [US3] Register `hostaway.set_custom_field` in `custom_components/hostaway/services/__init__.py` with `SERVICE_SET_CUSTOM_FIELD_SCHEMA` and `SupportsResponse.OPTIONAL`, then implement listing write dispatch in `custom_components/hostaway/services/custom_fields.py` only after the full T038 ladder passes or retain the fail-closed disabled path if it fails or stops after the currently authorized steps
- [ ] T078 [US3] Implement reservation write dispatch in `custom_components/hostaway/services/custom_fields.py` only after reservation `customFieldValues` no-op/sentinel/restore evidence or an authoritative Hostaway contract is recorded, or retain the fail-closed disabled path
- [ ] T079 [US3] Implement local coordinator/entity patching and write-generation publish suppression after successful writes so represented listing sensors and reservation attributes reflect the new value before the next poll and stale in-flight refreshes cannot overwrite it
- [ ] T080 [US3] Write and pass negative coverage in `tests/sensor/test_custom_fields.py` proving `hostaway.set_custom_field` never creates writable text, number, or select entities for custom variables and the integration has no custom-field definition create/update/delete code path

**Checkpoint**: Write service complete for each verified target type — writes
are no-clobber, gated by target-specific safety evidence, fail closed on every
unsafe raw-data shape, and update local state immediately after success.

---

## Phase 9: User Story 5 - Documentation and UX Clarity (P3)

**Goal**: Users can discover the new services and understand that built-in
fields such as `doorCode` are not custom variables.

**Independent Test**: Review service docs and HA strings; confirm service
descriptions, selectors, response schemas, hidden-field behavior, task-field
exclusion, and built-in/custom distinction are present.

### Tests for User Story 5

- [ ] T081 [P] [US5] Write failing documentation assertions in `tests/services/test_custom_fields.py` that parse `custom_components/hostaway/services.yaml` and require all three custom-field services, exact response envelopes, `varName` or `customFieldId` addressing, `config_entry_id` behavior, hidden-field reads, task-field exclusion, and `value: null` clearing
- [ ] T082 [P] [US5] Write failing documentation assertions in `tests/services/test_custom_fields.py` proving `hostaway.set_door_code` documentation explicitly states it writes built-in reservation fields, not custom variables, and cross-references custom-field services

### Implementation for User Story 5

- [ ] T083 [US5] Update `custom_components/hostaway/services.yaml` for `hostaway.get_custom_fields`, `hostaway.get_custom_field_values`, and `hostaway.set_custom_field`, including selectors, response examples, fail-closed multi-account behavior, and hidden/task field notes
- [ ] T084 [US5] Update existing `hostaway.set_door_code` documentation in `custom_components/hostaway/services.yaml` to state that `doorCode`, `doorCodeVendor`, and `doorCodeInstruction` are built-in reservation fields and not custom variables
- [ ] T085 [US5] Audit the current Home Assistant service documentation pattern and make the result independently verifiable: either add required user-facing service strings/translations to `custom_components/hostaway/strings.json` and `custom_components/hostaway/translations/en.json`, or add an assertion in `tests/services/test_custom_fields.py` proving `custom_components/hostaway/services.yaml` is the repository's complete service documentation source for these services

**Checkpoint**: Documentation complete — users can distinguish built-in
Hostaway fields from custom variables and can use every new service from the
documented schemas.

---

## Phase 10: Polish, Validation, and Release Notes

**Purpose**: Verify the whole feature, preserve the configured quality gates,
and prepare atomic implementation PR commits.

**Phase Exit Rule**: Full tests, linting, quickstart checks, pre-commit, CI,
`uvx --from aislop==0.12.0 aislop ci`, and live-verification evidence are
green or any disabled write target is explicitly documented.

- [ ] T086 [P] Run `uvx --from aislop==0.12.0 aislop ci` and confirm the repository still scores 100/100 with zero errors and zero warnings under the configured `.aislop/config.yml`; do not add a file-level line-count rule
- [ ] T087 [P] Run targeted tests from quickstart.md: `uv run pytest tests/api/test_custom_fields.py -x -q`, `uv run pytest tests/sensor/test_custom_fields.py -x -q`, `uv run pytest tests/services/test_custom_fields.py -x -q`, and `uv run pytest tests/test_config_flow.py -x -q -k custom_field`
- [ ] T088 Run full validation with `uv run pytest tests/ -x -q` and `uv run ruff check custom_components/ tests/`
- [ ] T089 Run `uv run pre-commit run --all-files` and fix markdownlint, codespell, REUSE, mypy, interrogate, and aislop issues without bypassing hooks
- [ ] T090 Add a separate `Docs(changelog):` implementation-PR commit updating `CHANGELOG.md` for custom field support, including any target type that remains disabled by failed or incomplete safety evidence
- [ ] T091 Add a separate atomic `Docs(tasks):` implementation-PR commit that flips completed checkboxes in `specs/007-custom-field-support/tasks.md`; do not bundle checkbox updates with code or changelog commits
- [ ] T092 Re-run quickstart.md user-facing checks for sensors, read services, write gates, successful verified writes, built-in/custom documentation, and existing `set_door_code` regression coverage

**Checkpoint**: Feature ready for implementation PR review — validation is
green, configured quality gates pass, changelog and task checkbox updates are
in separate commits, and live write safety status is documented.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Foundational API)**: Depends on Phase 1. Blocks all user stories.
- **Phase 3 (Coordinator and Options)**: Depends on Phase 2 models and
  definition fetch helpers. Blocks service and entity read surfaces.
- **Phase 4 (Live Verification Gates)**: Depends on Phase 2 direct reads and
  Phase 3 gate storage. Blocks write enablement per target type.
- **Phase 5 (US1 Sensors)**: Depends on Phases 2 and 3.
- **Phase 6 (US2 Read Services)**: Depends on Phases 2, 3, and Phase 5
  allocator behavior for listing response keys.
- **Phase 7 (US4 Addressing)**: Depends on Phases 2 and 3. Blocks write
  service mutation paths.
- **Phase 8 (US3 Writes)**: Depends on Phases 2, 3, 4, and 7. Listing writes
  require T038; reservation writes require T039.
- **Phase 9 (US5 Documentation)**: Can start after schemas are stable, final
  check after Phases 6 through 8.
- **Phase 10 (Polish)**: Depends on all included feature phases.

### User Story Dependencies

- **US1 (P1 sensors)**: Needs foundational API and definitions coordinator.
- **US2 (P1 read services)**: Needs foundational API, definitions
  coordinator, and listing allocator non-mutating response behavior.
- **US3 (P1 writes)**: Needs field resolution, value validation, write gates,
  target-specific safety evidence for each enabled target type, and
  no-clobber merge helpers.
- **US4 (P2 addressing)**: Can be built before writes and supplies write
  resolution logic.
- **US5 (P3 docs)**: Depends on final service schemas and write-gate outcomes.

### Within Each Phase

1. Write failing tests first.
2. Implement only enough code for the phase.
3. Refactor while preserving green targeted tests.
4. Run the phase checkpoint validation before starting dependent phases.
5. Do not perform manual FR-035 or SC-003 live mutations until automated CI is
   green for the implementation branch.

### Parallel Opportunities

- T003–T006 can run in parallel after T002.
- T008–T013 cover independent API behaviors, but they all mention
  `tests/api/test_custom_fields.py`; implement them sequentially unless the
  implementer first splits them into separate test modules.
- T023–T027 can run in parallel across options, setup, coordinator, and
  service gate checks.
- T034–T035 are independent behaviors but share
  `tests/services/test_custom_fields.py`; run sequentially unless split.
- T040–T045 can run in parallel only where they target different files; tasks
  sharing `tests/sensor/test_custom_fields.py` should be sequential or split.
- T051–T056 are read-service behaviors in one test module; run sequentially
  unless split into separate files.
- T061–T063 can run in parallel only after splitting same-file service tests,
  otherwise run same-file edits sequentially.
- T067–T074 can run in parallel only across distinct API, service, and sensor
  files; tasks sharing one file should be sequential or split first.
- T081–T082 can run in parallel for custom-field and door-code documentation.

---

## Batched Example: Foundational API Tests

```bash
Task T008: "Write definition parsing tests"
Task T009: "Write value collection state tests"
Task T010: "Write duplicate and addressed malformed-entry tests"
Task T011: "Write definition pagination tests"
Task T012: "Write includeResources direct and paged read tests"
Task T013: "Write reservation raw-id pagination tests"
```

These tasks should be implemented sequentially unless the test file is split
beforehand; they are listed together to show the complete foundational batch.

## Batched Example: Sensor Read Surface

```bash
Task T040: "Write listing allocator stability tests"
Task T041: "Write listing custom-field sensor tests"
Task T042: "Write runtime discovery tests"
Task T043: "Write existing listing diagnostics regression tests"
Task T044: "Write reservation custom_fields attribute tests"
Task T045: "Write malformed value presentation tests"
```

Only tasks touching different files are parallel-safe; same-file sensor tests
must be sequenced or split to avoid merge conflicts.

---

## Implementation Strategy

### MVP First

1. Complete Phases 1 through 3 so definitions and value reads are safe.
2. Complete Phase 5 for sensor visibility.
3. Complete Phase 6 for read services.
4. **STOP and VALIDATE**: targeted API, sensor, service, and config-flow tests
   pass; existing listing/reservation behavior remains compatible.

### Write Enablement

1. Complete Phase 4 gate tests and target-specific evidence tasks before
   enabling any write target.
2. Complete Phase 7 addressing and validation.
3. Complete Phase 8 write implementation only for target types whose required
   listing ladder evidence or reservation `customFieldValues` evidence passed.
   Keep any failed target type disabled with the documented fail-closed path.

### Finalization

1. Complete Phase 9 documentation.
2. Complete Phase 10 validation and pre-commit.
3. Use atomic implementation PR commits:
   - implementation commit(s), with `Closes #195` on the code commit that
     completes the feature;
   - separate `Docs(changelog):` commit;
   - separate `Docs(tasks):` commit for checkbox flips.

---

## Notes

- [P] tasks = different files or independent behavior and no incomplete
  dependency.
- The write gates are intentionally executable, default-off, and per target
  type. A successful listing verification must not enable reservation writes,
  and a successful reservation verification must not enable listing writes.
- Reads may use stale cached definitions after a definitions refresh failure;
  writes must fail closed until the next successful definitions refresh.
- Treat `customFieldValues: []` as genuinely empty, but fail closed for missing,
  `null`, or non-list `customFieldValues` before any write.
- Preserve raw malformed entries in write payloads whenever they are
  unaddressed; fail closed if the addressed id is malformed or duplicated.
- Do not add custom-field business logic to `api/client.py` beyond thin
  transport-only changes such as `includeResources=1`.
- Do not modify `spec.md`, `plan.md`, `research.md`, `data-model.md`,
  `quickstart.md`, contracts, or checklist artifacts during implementation
  unless a separate spec-fix task and PR are explicitly approved.
