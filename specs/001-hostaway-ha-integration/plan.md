# Implementation Plan: Hostaway Home Assistant Integration

**Branch**: `001-hostaway-ha-integration` | **Date**: 2025-07-14 | **Spec**: `specs/001-hostaway-ha-integration/spec.md`
**Input**: Feature specification from `specs/001-hostaway-ha-integration/spec.md`

## Summary

Build a Home Assistant custom integration for the Hostaway property management
platform API. The integration authenticates via OAuth 2.0 Client Credentials
Grant, exposes listing and reservation data as sensor entities, and provides
services for door code management and reservation retrieval. Architecture
mirrors the Guesty sister project (`../guesty`): isolated API client layer with
token management, DataUpdateCoordinators for polling, config flow with listing
selection, and sensor platform with entity descriptions.

## Technical Context

**Language/Version**: Python 3.x (aligned with HA minimum per `manifest.json`)
**Primary Dependencies**: httpx (HTTP client), voluptuous (schema
validation), homeassistant (core HA framework)
**Storage**: Home Assistant config entry storage (credential persistence,
token caching)
**Testing**: pytest (with pytest-homeassistant-custom-component fixtures)
**Target Platform**: Home Assistant (any hardware: RPi to server)
**Project Type**: Home Assistant custom integration (HACS-distributed)
**Performance Goals**: Non-blocking async I/O; polling within rate
limits (15 req/10s per IP, 20 req/10s per account)
**Constraints**: Must not block HA event loop; must respect Hostaway
rate limits; 1-second post-token-generation delay required
**Scale/Scope**: <100 listings, <1000 reservations per poll cycle;
2 coordinators + 2 services

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle                        | Status  | Notes                    |
| -------------------------------- | ------- | ------------------------ |
| I. Code Quality & Testing (TDD)  | ✅ PASS | TDD Red-Green-Refactor   |
| II. API Client Design            | ✅ PASS | Isolated, DI, mockable   |
| III. Atomic Commits              | ✅ PASS | Phased atomic commits    |
| IV. Licensing & Attribution      | ✅ PASS | SPDX + REUSE.toml        |
| V. Pre-Commit Integrity          | ✅ PASS | .pre-commit-config.yaml  |
| VI. Agent Co-Authorship & DCO    | ✅ PASS | Co-authored + Signed-off |
| VII. User Experience Consistency | ✅ PASS | Config/options per HA    |
| VIII. Performance Requirements   | ✅ PASS | Async, rate-limit        |
| IX. Phased Development           | ✅ PASS | 5 phases, testable       |
| X. Security & Credential Mgmt    | ✅ PASS | Token in config entry    |

**Gate Result**: PASS — No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/001-hostaway-ha-integration/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── services.yaml    # HA service definitions
│   └── api-endpoints.md # Hostaway API endpoints used
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── __init__.py          # Integration setup, service registration
├── api/
│   ├── __init__.py      # Package init
│   ├── auth.py          # Token manager (OAuth 2.0 Client Credentials)
│   ├── client.py        # HTTP client with retry/backoff
│   ├── const.py         # API constants (URLs, limits, fields)
│   ├── exceptions.py    # Exception hierarchy
│   └── models.py        # Data transfer objects (Listing, Reservation, Token)
├── config_flow.py       # Multi-step config flow + options flow
├── const.py             # Integration constants (DOMAIN, config keys)
├── coordinator.py       # DataUpdateCoordinators (listings, reservations)
├── entity.py            # Base entity class
├── sensor.py            # Sensor platform (listing + reservation sensors)
├── services.yaml        # Service schema definitions
├── strings.json         # UI strings
├── translations/
│   └── en.json          # English translations
├── manifest.json        # HA component manifest
└── brand/               # Brand assets (icons/logos)

tests/
├── __init__.py
├── conftest.py          # Shared fixtures
├── api/
│   ├── __init__.py
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_exceptions.py
│   └── test_models.py
├── test_config_flow.py
├── test_coordinator.py
├── test_init.py
└── test_sensor.py
```

**Structure Decision**: Follows the Guesty sister project architecture exactly.
Single custom component under `custom_components/hostaway/` with an isolated
`api/` sub-package. Tests mirror the source structure.

## Complexity Tracking

> No violations to justify. Design follows established patterns from the Guesty
> sister project without additional complexity.
