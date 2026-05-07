<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Hostaway Home Assistant Integration

**Input**: Design documents from
`/specs/001-hostaway-ha-integration/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅,
data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: TDD is mandatory per project constitution. Every unit
of production code MUST be preceded by a failing test. Tests are
written first in each phase, then implementation makes them pass.

**Organization**: Tasks are grouped by implementation phase from
plan.md to enable incremental delivery. Each phase delivers an
independently testable increment. User stories from spec.md are
mapped to the phase where their prerequisites exist.

## Format: `ID [P] [Story] Description`

- **ID**: Plain task identifier (e.g., `T001`, `T002`)
- **[P]** *(optional)*: Can run in parallel (different files,
  no dependencies on incomplete tasks)
- **[Story]** *(optional)*: Which user story this task belongs
  to (e.g., `[US1]`, `[US2]`). Present only in user story
  phases; omitted for Setup and Foundational phases.
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/hostaway/` (HA custom
  component)
- **API layer**: `custom_components/hostaway/api/`
  (library-extractable, zero HA imports)
- **Tests**: `tests/` (HA-level),
  `tests/api/` (API layer unit tests)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create project directory structure, package
scaffolding, CI configuration, and manifest files

- [x] T001 Create directory structure with `__init__.py`
  packages for `custom_components/hostaway/`,
  `custom_components/hostaway/api/`, `tests/`, and
  `tests/api/` per plan.md project structure
- [x] T002 [P] Create integration manifest in
  `custom_components/hostaway/manifest.json`: domain
  `hostaway`, `config_flow: true`,
  `iot_class: cloud_polling`, HA version requirement,
  requirements `httpx>=0.27`, codeowners `@tykeal`,
  documentation URL
- [x] T003 [P] Verify and update existing `hacs.json`:
  confirm name is `Hostaway`, `render_readme: true`, and
  homeassistant minimum version matches manifest.json
- [x] T004 [P] Create `pyproject.toml` with project metadata,
  test dependencies (`pytest`, `pytest-asyncio`,
  `pytest-homeassistant-custom-component`, `respx`), ruff
  config, mypy config, and interrogate config
- [x] T005 [P] Update existing `.pre-commit-config.yaml` to
  include any missing hooks needed for implementation:
  ruff, ruff-format, mypy, interrogate, yamllint,
  markdownlint, reuse-tool, gitlint, codespell, and
  validate-pyproject
- [x] T006 [P] Verify existing `.gitlint` enforces
  title-max-length 50, body-max-line-length 72,
  conventional commits, and signed-off-by; update if
  needed
- [x] T007 [P] Update existing `REUSE.toml` with license
  annotations for new file patterns added by this feature
  (Apache-2.0 for source, CC-BY-4.0 for docs/specs)
- [x] T008 [P] Verify existing GitHub Actions workflows
  (e.g., `build-test.yaml`, `validate.yaml`) cover lint,
  type-check, and test matrix; extend or add workflow if
  gaps exist

**Checkpoint**: Project skeleton compiles, pre-commit hooks
pass, CI pipeline runs successfully

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Exception hierarchy, API constants, DTOs, and
shared test fixtures — required by ALL user stories

**⚠️ CRITICAL**: No user story work can begin until this
phase is complete

### Tests for Foundational

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T009 [P] Write exception hierarchy tests in
  `tests/api/test_exceptions.py`: verify
  `HostawayApiError` base class, `HostawayAuthError`,
  `HostawayRateLimitError` (with `retry_after` attr),
  `HostawayConnectionError`, `HostawayResponseError`
  inheritance chain and string representation
- [x] T010 [P] Write `AccessToken` frozen dataclass tests in
  `tests/api/test_models.py`: creation with valid data,
  frozen immutability, `expires_at` computation,
  `is_expired` with and without buffer,
  `seconds_until_ready` enforcing 1s post-generation
  delay, `to_dict`/`from_dict` round-trip serialization,
  validation (empty token raises, negative expiry raises,
  naive datetime raises)
- [x] T011 [P] Write `HostawayListing` and
  `HostawayReservation` dataclass tests in
  `tests/api/test_models.py`: creation from API response
  dict with camelCase→snake_case mapping, validation of
  required fields, optional field handling (None defaults),
  `from_api_response()` class method tests

### Implementation for Foundational

- [x] T012 [P] Implement exception hierarchy in
  `custom_components/hostaway/api/exceptions.py`:
  `HostawayApiError` base with `message` attr;
  `HostawayAuthError`; `HostawayRateLimitError` with
  `retry_after: float | None`;
  `HostawayConnectionError`; `HostawayResponseError` —
  all with SPDX header, docstrings, type annotations
- [x] T013 [P] Implement API constants in
  `custom_components/hostaway/api/const.py`: `TOKEN_URL`
  (`https://api.hostaway.com/v1/accessTokens`), `BASE_URL`
  (`https://api.hostaway.com`), `DEFAULT_TIMEOUT` (30s),
  `TOKEN_READY_DELAY` (1.0s), `MAX_RETRIES` (3),
  `INITIAL_BACKOFF` (1.0), `BACKOFF_MULTIPLIER` (2.0),
  `MAX_BACKOFF` (30.0), `RATE_LIMIT_PER_IP` (15/10s),
  `RATE_LIMIT_PER_ACCOUNT` (20/10s),
  `DEFAULT_PAGE_LIMIT` (100), `GRANT_TYPE`
  (`client_credentials`), `SCOPE` (`general`)
- [x] T014 [P] Implement `AccessToken` frozen dataclass in
  `custom_components/hostaway/api/models.py`:
  `access_token`, `token_type`, `expires_in`, `issued_at`
  fields; `expires_at` and `is_expired(buffer_seconds)`
  computed properties; `seconds_until_ready` property
  enforcing 1s delay; `to_dict()`/`from_dict()`
  serialization per data-model.md
- [x] T015 [P] Implement `HostawayListing` dataclass in
  `custom_components/hostaway/api/models.py`: all fields
  per data-model.md with `from_api_response(data: dict)`
  class method performing camelCase→snake_case mapping
- [x] T016 [P] Implement `HostawayReservation` dataclass in
  `custom_components/hostaway/api/models.py`: all fields
  per data-model.md with `from_api_response(data: dict)`
  class method performing camelCase→snake_case mapping
- [x] T017 Create public API surface exports in
  `custom_components/hostaway/api/__init__.py`: export all
  exception classes, `AccessToken`, `HostawayListing`,
  `HostawayReservation`, and constants needed by consumers
- [x] T018 Create shared test fixtures in
  `tests/conftest.py`: mock token response factory, common
  test constants (fake client ID/secret), mock listing and
  reservation API response factories,
  `httpx.AsyncClient` fixture with `respx` mocking

**Checkpoint**: Foundation ready — all DTOs, exceptions, and
constants available for user story implementation

---

## Phase 3: API Client Layer (US1, US2)

**Purpose**: Token management with 1s delay enforcement, HTTP
client with retry/backoff, and connection testing

**Goal**: Authenticate with Hostaway, manage tokens, handle
rate limits and errors transparently

### Tests for API Client

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T019 [P] [US2] Write token acquisition unit tests in
  `tests/api/test_auth.py`: successful token acquisition
  via `get_token()`, 1-second post-generation delay
  enforcement (FR-003), cached token reuse on second call,
  `HostawayAuthError` on 401 invalid credentials,
  `HostawayConnectionError` on network failure/timeout,
  `HostawayResponseError` on malformed token response —
  all using `respx` to mock HTTP
- [x] T020 [P] [US2] Write token refresh and persistence
  tests in `tests/api/test_auth.py`: proactive refresh
  when token nears expiry, `invalidate()` clears cached
  token, concurrent `get_token()` calls share single HTTP
  request via asyncio.Lock, `seed_token()` for startup
  loading (config entry persistence deferred to Phase 4)
- [x] T021 [P] [US2] Write HTTP client core tests in
  `tests/api/test_client.py`: successful GET/PUT requests
  with auth header, 429 response triggers exponential
  backoff retry (FR-006), max 3 retries then
  `HostawayRateLimitError`, 403 triggers token refresh
  and single retry, 404 raises `HostawayResponseError`,
  5xx triggers retry with backoff, network error raises
  `HostawayConnectionError`
- [x] T022 [P] [US2] Write pagination tests in
  `tests/api/test_client.py`: offset-based pagination for
  listings (FR-012), cursor-based pagination with afterId
  for reservations (FR-012), handles empty result set,
  handles single page result
- [x] T023 [P] [US1] Write `test_connection` tests in
  `tests/api/test_client.py`: successful connection test
  validates credentials, auth failure propagates
  `HostawayAuthError`, connection failure propagates
  `HostawayConnectionError`

### Implementation for API Client

- [x] T024 [US2] Implement `HostawayTokenManager` in
  `custom_components/hostaway/api/auth.py`: constructor
  accepting `client_id`, `client_secret`, `http_client`
  (`httpx.AsyncClient`); `get_token()` with POST to token
  endpoint using `client_credentials` grant and `general`
  scope; 1-second delay enforcement after acquisition
  (FR-003); in-memory caching with expiry check (FR-002);
  `asyncio.Lock` for concurrent access; `invalidate()`
  method; `seed_token()` for startup loading; maps HTTP
  401→`HostawayAuthError`, network→`HostawayConnectionError`,
  malformed→`HostawayResponseError`
- [x] T025 [US2] Implement `HostawayApiClient` in
  `custom_components/hostaway/api/client.py`: constructor
  accepting `token_manager`, `http_client`, optional
  `base_url`; `_request()` helper adding Bearer auth
  header; exponential backoff with jitter on 429 (FR-005,
  FR-006); token refresh on 403; retry on 5xx; pagination
  helpers: `get_listings_page(offset, limit)`,
  `get_reservations_page(listing_id, after_id, limit)`,
  `get_all_listings()`, `get_all_reservations(listing_id)`;
  `update_reservation(reservation_id, data)` for PUT;
  `test_connection()` method
- [x] T026 Update public exports in
  `custom_components/hostaway/api/__init__.py` to include
  `HostawayTokenManager` and `HostawayApiClient`

**Checkpoint**: API client fully functional — can authenticate,
paginate, retry, and test connections

---

## Phase 4: Config Flow and Integration Setup (US2)

**Purpose**: Multi-step config flow (credentials → listing
selection), options flow for polling intervals, and
integration lifecycle management

**Goal**: Users can add and configure the integration through
the Home Assistant UI (FR-004, FR-011)

### Tests for Config Flow

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T027 [P] [US2] Write config flow `step_user` tests in
  `tests/test_config_flow.py`: successful credential entry
  proceeds to listing selection, invalid credentials show
  `invalid_auth` error, connection failure shows
  `cannot_connect` error, duplicate `client_id` triggers
  `already_configured` abort (FR-004)
- [x] T028 [P] [US2] Write config flow `step_listings` tests
  in `tests/test_config_flow.py`: all active listings
  displayed as selectable options, at least one listing
  must be selected, successful selection creates config
  entry with credentials and selected_listings (FR-004)
- [x] T029 [P] [US2] Write options flow tests in
  `tests/test_config_flow.py`: form displays current
  intervals, valid intervals accepted (min 1 minute),
  invalid intervals show error, updated intervals stored
  in config entry options (FR-008, FR-010, FR-011)
- [x] T030 [P] [US2] Write `async_setup_entry` and
  `async_unload_entry` tests in `tests/test_init.py`:
  setup creates `httpx.AsyncClient`,
  `HostawayTokenManager`, `HostawayApiClient` in
  `hass.data[DOMAIN]`; setup loads persisted token;
  unload closes HTTP client and removes data; setup
  failure raises `ConfigEntryNotReady`

### Implementation for Config Flow

- [x] T031 [P] [US2] Create HA-level constants in
  `custom_components/hostaway/const.py`: `DOMAIN =
  "hostaway"`, `CONF_CLIENT_ID`, `CONF_CLIENT_SECRET`,
  `CONF_SELECTED_LISTINGS`, `CONF_SCAN_INTERVAL`,
  `CONF_RESERVATION_SCAN_INTERVAL`,
  `DEFAULT_SCAN_INTERVAL` (5), `MIN_SCAN_INTERVAL` (1),
  `DEFAULT_RESERVATION_SCAN_INTERVAL` (2),
  `PLATFORMS: list[Platform]` (sensor)
- [x] T032 [P] [US2] Create localized UI strings in
  `custom_components/hostaway/strings.json` and
  `custom_components/hostaway/translations/en.json`:
  config flow steps `user` and `listings` with field
  descriptions, options flow step with interval fields,
  error messages (`invalid_auth`, `cannot_connect`,
  `unknown`), abort reasons (`already_configured`)
- [x] T033 [US2] Implement config flow in
  `custom_components/hostaway/config_flow.py`:
  `HostawayConfigFlow(ConfigFlow, domain=DOMAIN)` with
  `async_step_user` for credential entry and validation,
  `async_step_listings` for listing selection populated
  from API; `HostawayOptionsFlow` with interval
  configuration; maps errors to UI messages; sets
  `unique_id` from `client_id` for duplicate detection
  (FR-004, FR-011)
- [x] T034 [US2] Implement entry lifecycle in
  `custom_components/hostaway/__init__.py`:
  `async_setup_entry` creates HTTP client, token manager,
  API client, loads persisted token, stores in
  `hass.data[DOMAIN][entry.entry_id]`, forwards to
  platforms; `async_unload_entry` closes client, removes
  data; token persistence via config entry data updates
  (FR-002)

**Checkpoint**: Users can add and configure the Hostaway
integration via config flow with listing selection and
adjustable polling intervals

---

## Phase 5: Data Coordinators and Entities (US3, US4)

**Purpose**: DataUpdateCoordinators for listings and
reservations, sensor entities exposing property data

**Goal**: Listing and reservation data visible as HA sensor
entities with configurable polling (FR-007 through FR-010)

### Tests for Coordinators and Entities

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [x] T035 [P] [US3] Write ListingsCoordinator tests in
  `tests/test_coordinator.py`: successful data fetch
  returns `dict[int, HostawayListing]`, configurable poll
  interval from options (FR-008), API error sets
  coordinator last_update_success=False, retains last
  data on transient failure (FR-016)
- [x] T036 [P] [US4] Write ReservationsCoordinator tests in
  `tests/test_coordinator.py`: successful fetch returns
  `dict[int, list[HostawayReservation]]` sorted by
  check_in, configurable poll interval from options
  (FR-010), handles pagination for large reservation
  sets, only fetches for selected listings
- [x] T037 [P] [US3] Write listing sensor entity tests in
  `tests/test_sensor.py`: entity creation per listing
  with correct unique_id (account_id + listing_id +
  attribute), entity_id follows
  `sensor.hostaway_<listing>_<attribute>` convention
  (FR-007), state updates when coordinator data changes,
  entity becomes unavailable when listing removed
- [x] T038 [P] [US4] Write reservation sensor entity tests
  in `tests/test_sensor.py`: entity per reservation with
  guest_name as state, attributes include check_in,
  check_out, status, door_code fields (FR-009), new
  reservation creates new entity, status change updates
  entity state

### Implementation for Coordinators and Entities

- [x] T039 [US3] Implement `HostawayListingsCoordinator` in
  `custom_components/hostaway/coordinator.py`:
  `DataUpdateCoordinator` subclass, `_async_update_data`
  calls `api_client.get_all_listings()`, filters to
  selected listings, returns `dict[int, HostawayListing]`,
  update interval from `config_entry.options` (FR-008,
  FR-016)
- [x] T040 [US4] Implement `HostawayReservationsCoordinator`
  in `custom_components/hostaway/coordinator.py`:
  `DataUpdateCoordinator` subclass, `_async_update_data`
  fetches reservations for each selected listing with
  pagination, returns
  `dict[int, list[HostawayReservation]]` sorted by
  check_in, update interval from options (FR-010, FR-012)
- [x] T041 [US3] Implement base entity class in
  `custom_components/hostaway/entity.py`:
  `HostawayEntity(CoordinatorEntity)` with device_info,
  unique_id generation from account_id + entity-specific
  immutable IDs per FR-007
- [x] T042 [US3] Implement listing sensor descriptions and
  platform in `custom_components/hostaway/sensor.py`:
  `SensorEntityDescription` for listing attributes
  (status, base_price, bedrooms, bathrooms, max_guests),
  `HostawayListingSensor` class, `async_setup_entry`
  creating sensors per listing per description (FR-007)
- [x] T043 [US4] Implement reservation sensor in
  `custom_components/hostaway/sensor.py`:
  `HostawayReservationSensor` class with guest_name as
  state, extra_state_attributes for check_in, check_out,
  status, door_code, door_code_vendor,
  door_code_instruction, num_guests (FR-009)
- [x] T044 Wire coordinators into entry setup in
  `custom_components/hostaway/__init__.py`: create both
  coordinators in `async_setup_entry`, call
  `async_config_entry_first_refresh()`, store in
  `hass.data`, forward to sensor platform; handle options
  update listener for interval changes

**Checkpoint**: Listing and reservation data visible as
sensor entities with configurable polling intervals

---

## Phase 6: Services and Events (US1, US5)

**Purpose**: Service registration for door code management
and reservation retrieval with event firing

**Goal**: Automations can set door codes and retrieve
reservation data programmatically (FR-013, FR-014, FR-015)

### Tests for Services

> **NOTE: Write these tests FIRST, ensure they FAIL before
> implementation**

- [ ] T045 [P] [US1] Write `set_door_code` service tests in
  `tests/test_services.py`: successful door code update
  calls PUT with correct payload, validates
  reservation_id is positive integer, validates door_code
  is non-empty, optional fields omitted when None,
  404 raises ServiceValidationError, 429 retries with
  backoff, expired token refreshes transparently
  (FR-013, FR-015)
- [ ] T046 [P] [US5] Write `get_reservations` service tests
  in `tests/test_services.py`: successful call fires
  `hostaway_reservations_retrieved` event with snake_case
  payload, validates listing_id is positive integer,
  empty reservation list fires event with empty list,
  listing_name resolved from coordinator cache
  (FR-014, FR-015)

### Implementation for Services

- [ ] T047 [US1] Implement `set_door_code` service handler
  in `custom_components/hostaway/__init__.py` (or
  dedicated `services.py`): validate parameters per
  FR-015, build payload with only non-None optional
  fields, call `api_client.update_reservation()`, map
  404→ServiceValidationError (FR-013)
- [ ] T048 [US5] Implement `get_reservations` service
  handler: validate listing_id, call
  `api_client.get_all_reservations(listing_id)`, resolve
  listing_name from ListingsCoordinator, fire
  `hostaway_reservations_retrieved` event with snake_case
  payload per FR-014
- [ ] T049 Create `services.yaml` in
  `custom_components/hostaway/services.yaml`: define
  `set_door_code` and `get_reservations` schemas per
  contracts/services.md
- [ ] T050 Register services once per integration in
  `async_setup` in `custom_components/hostaway/__init__.py`:
  register both service handlers with voluptuous schema
  validation; use a domain-level setup guard so services
  persist across multiple config entries and are only
  removed when the last entry unloads

**Checkpoint**: Both services functional — door codes can be
set via automation, reservations retrievable on demand

---

## Phase 7: Polish, Documentation, and Integration Tests

**Purpose**: End-to-end validation, documentation, brand
assets, and release preparation

### Tests

- [ ] T051 [P] Write integration tests in
  `tests/test_integration.py`: full lifecycle test
  (config flow → coordinator refresh → sensor creation →
  service call), verify entity naming conventions, verify
  unique_id stability across restarts
- [ ] T052 [P] Write edge case tests: token expiry during
  coordinator refresh triggers transparent refresh,
  listing deleted in Hostaway marks sensor unavailable,
  pagination with >100 reservations, concurrent service
  calls don't deadlock

### Documentation and Assets

- [ ] T053 [P] Create `README.md` with installation
  instructions (HACS + manual), configuration guide,
  entity documentation, service documentation, and
  automation examples
- [ ] T054 [P] Create brand assets in
  `custom_components/hostaway/brand/`: icon.png and
  logo.png per HA custom component conventions
- [ ] T055 [P] Create `CHANGELOG.md` with initial release
  entry documenting all features

### Final Validation

- [ ] T056 Run full test suite with coverage report, verify
  all tests pass and coverage meets project requirements
- [ ] T057 Run complete pre-commit hook suite, verify all
  checks pass including ruff, mypy, interrogate, reuse,
  markdownlint, and codespell
- [ ] T058 Validate HACS compatibility: verify manifest
  structure, confirm integration loads in HA dev
  environment

**Checkpoint**: Integration is release-ready — all tests
pass, documentation complete, HACS-compatible

---

## Dependency Graph

```text
T001 ─┬─► T002-T008 (parallel scaffolding)
       │
       ▼
T009-T011 (foundation tests, parallel)
       │
       ▼
T012-T018 (foundation impl, parallel)
       │
       ├─► T019-T023 (API client tests, parallel)
       │       │
       │       ▼
       │   T024-T026 (API client impl)
       │       │
       ├───────┤
       │       ▼
       │   T027-T030 (config flow tests, parallel)
       │       │
       │       ▼
       │   T031-T034 (config flow impl)
       │       │
       │       ▼
       │   T035-T038 (coordinator/entity tests)
       │       │
       │       ▼
       │   T039-T044 (coordinator/entity impl)
       │       │
       │       ▼
       │   T045-T046 (service tests, parallel)
       │       │
       │       ▼
       │   T047-T050 (service impl)
       │       │
       │       ▼
       └──► T051-T058 (polish, parallel where marked)
```

## FR Traceability Matrix

| FR     | Tasks                     | Phase |
| ------ | ------------------------- | ----- |
| FR-001 | T019, T024                | 3     |
| FR-002 | T020, T024, T034          | 3, 4  |
| FR-003 | T010, T014, T019, T024    | 2, 3  |
| FR-004 | T027, T028, T033          | 4     |
| FR-005 | T021, T025                | 3     |
| FR-006 | T021, T025                | 3     |
| FR-007 | T037, T041, T042          | 5     |
| FR-008 | T035, T039                | 5     |
| FR-009 | T038, T043                | 5     |
| FR-010 | T036, T040                | 5     |
| FR-011 | T029, T033                | 4     |
| FR-012 | T022, T025, T040          | 3, 5  |
| FR-013 | T045, T047                | 6     |
| FR-014 | T046, T048                | 6     |
| FR-015 | T045, T046, T047, T048    | 6     |
| FR-016 | T035, T039                | 5     |
