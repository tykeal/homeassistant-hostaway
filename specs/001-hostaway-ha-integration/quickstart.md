# Quickstart: Hostaway Home Assistant Integration

## Prerequisites

- Python 3.x (version per `manifest.json`)
- `uv` package manager installed
- Home Assistant development environment (or `pytest-homeassistant-custom-component`)
- Hostaway account with API access (client_id and client_secret)

## Initial Setup

```bash
# Clone and enter the repository
cd /path/to/homeassistant-hostaway

# Install dependencies
uv sync

# Verify tooling works
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
uv run mypy custom_components/ tests/
```

## Project Layout

```text
custom_components/hostaway/     # Integration source code
├── api/                        # Isolated API client layer
│   ├── auth.py                 # Token management
│   ├── client.py               # HTTP client with retry logic
│   ├── const.py                # API URLs and constants
│   ├── exceptions.py           # Error hierarchy
│   └── models.py               # Data transfer objects
├── __init__.py                 # Entry point, service registration
├── config_flow.py              # UI config flow
├── coordinator.py              # DataUpdateCoordinators
├── const.py                    # Integration constants
├── sensor.py                   # Sensor platform
└── manifest.json               # HA component metadata

tests/                          # Test suite (mirrors source)
├── api/                        # API layer unit tests
├── conftest.py                 # Shared fixtures
└── test_*.py                   # Integration-level tests
```

## Development Workflow (TDD)

```bash
# 1. Write a failing test
uv run pytest tests/api/test_auth.py -x -q  # RED

# 2. Implement minimum code to pass
uv run pytest tests/api/test_auth.py -x -q  # GREEN

# 3. Refactor
uv run pytest tests/ -x -q                  # Still GREEN

# 4. Lint and type-check
uv run ruff check custom_components/ tests/
uv run mypy custom_components/ tests/

# 5. Commit atomically
git add <files>
git commit -s -m "Feat(api): Add token manager

Implement OAuth 2.0 Client Credentials flow with token
caching and 1-second post-generation delay.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Key Patterns (from Guesty sister project)

### API Client Layer

- `HostawayTokenManager`: handles token acquisition, caching, refresh
- `HostawayApiClient`: authenticated HTTP with backoff/retry
- All HTTP via injected `httpx.AsyncClient` (testable via DI)
- Exceptions: `HostawayApiError` → `HostawayAuthError`,
  `HostawayRateLimitError`, etc.

### DataUpdateCoordinators

- `ListingsCoordinator`: polls listings at configurable interval
- `ReservationsCoordinator`: polls reservations, groups by listing_id

### Config Flow

- Step 1: Credentials → validate via token acquisition
- Step 2: Listing selection → multi-select from fetched listings
- Options: Adjust polling intervals post-setup

### Services

- `hostaway.set_door_code`: Updates door code on a reservation
- `hostaway.get_reservations`: Fetches reservations, fires event

## Running Tests

```bash
# All tests
uv run pytest tests/ -x -q

# Specific module
uv run pytest tests/api/test_client.py -v

# With coverage
uv run pytest tests/ --cov=custom_components/hostaway --cov-report=term-missing
```

## Pre-Commit Checks

```bash
# Run all hooks manually
pre-commit run --all-files

# Hooks run automatically on git commit
# If they fail: fix, stage, commit again (never --no-verify)
```

## Key Differences from Guesty

| Aspect          | Guesty                    | Hostaway                   |
| --------------- | ------------------------- | -------------------------- |
| Token endpoint  | `open-api.guesty.com/...` | `api.hostaway.com/v1/...`  |
| Expiry trigger  | HTTP 401                  | HTTP 403                   |
| Post-token wait | None                      | 1 second mandatory         |
| Pagination      | offset-based              | cursor-based (`afterId`)   |
| Rate limits     | Not documented            | 15/10s (IP), 20/10s (acct) |
| Scope           | `open-api`                | `general`                  |
| Boolean format  | Standard JSON             | Integer (0/1)              |
| Listing ID type | string                    | integer                    |
| Door code       | Via custom fields API     | Native reservation fields  |
