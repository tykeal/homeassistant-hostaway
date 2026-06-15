# Data Model: API Client Complexity Refactor

**Feature**: 004-api-client-refactor
**Date**: 2026-06-15

## Overview

This feature is a pure structural refactoring — no new entities, fields,
or state transitions are introduced. The data model documents the module
boundaries and function signatures that define the extraction contract.

## Module: `redaction.py`

### Extracted Constants

| Name | Type | Purpose |
|------|------|---------|
| `_MAX_RESPONSE_BODY_LOG` | `int` | Max chars to include in log body (500) |
| `_REDACTED` | `str` | Replacement sentinel (`"<redacted>"`) |
| `_SENSITIVE_KEY_TOKENS` | `tuple[str, ...]` | Key substrings that signal secrets |
| `_CONTROL_CHAR_RE` | `re.Pattern` | Regex matching ASCII control characters |
| `_SENSITIVE_KEY_PATTERN` | `str` | Joined token pattern for regex building |
| `_TEXT_REDACT_RE` | `re.Pattern` | Key/value redaction regex for plain text |
| `_BEARER_RE` | `re.Pattern` | Bearer token detection regex |
| `_AUTH_403_PHRASES` | `tuple[str, ...]` | Known auth-failure phrases in 403 bodies |

### Extracted Functions

| Function | Signature | Returns | Purpose |
|----------|-----------|---------|---------|
| `_is_sensitive_key` | `(key: str) -> bool` | `bool` | Detect secret-bearing field names |
| `_redact_sensitive` | `(value: Any) -> Any` | `Any` | Recursively redact sensitive values in dicts/lists |
| `_redact_plain_text` | `(text: str) -> str` | `str` | Pattern-based redaction for non-JSON text |
| `_sanitize_for_log` | `(text: str) -> str` | `str` | Escape CR/LF, strip control chars |
| `_safe_response_body` | `(response: httpx.Response, max_len: int = 500) -> str` | `str` | Safe, redacted body excerpt for logging |
| `_is_auth_403_body` | `(body: str) -> bool` | `bool` | Classify 403 body as auth vs permission |

### Dependencies

```
redaction.py imports:
  - json (stdlib)
  - logging (stdlib)
  - re (stdlib)
  - typing.Any (stdlib)
  - httpx (third-party)
```

## Module: `client.py` (Refactored)

### Class: `HostawayApiClient`

**Unchanged public interface** — all existing methods, signatures, and
behaviors preserved exactly.

### New Private Methods (extracted from `_request()`)

| Method | Signature | Returns | Raises |
|--------|-----------|---------|--------|
| `_handle_403` | `(self, method: str, path: str, *, params, json, response, _retried_auth: bool) -> httpx.Response` | `httpx.Response` (retry result) | `HostawayAuthError`, `HostawayReservationLockedError` |
| `_handle_429` | `(self, response: httpx.Response, attempt: int, backoff: float) -> float` | Updated backoff value | `HostawayRateLimitError` |
| `_handle_server_error` | `(self, response: httpx.Response, attempt: int, backoff: float) -> float` | Updated backoff value | `HostawayConnectionError` |

### Handler Method Behaviors

**`_handle_403`**:

1. If `_retried_auth` is True → raise `HostawayAuthError` immediately
2. Read body via `_safe_response_body(response)`
3. If body is NOT auth-related → log debug, raise `HostawayReservationLockedError`
4. If body IS auth-related → log warning, invalidate token, recursively
   call `self._request(...)` with `_retried_auth=True`, return response

**`_handle_429`**:

1. If `attempt >= MAX_RETRIES` → raise `HostawayRateLimitError`
2. Calculate delay via `_calculate_backoff(backoff, response)`
3. Log warning with delay and attempt info
4. `await asyncio.sleep(delay)`
5. Return `min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)`

**`_handle_server_error`**:

1. If `attempt >= MAX_RETRIES` → raise `HostawayConnectionError`
2. Calculate delay via `_jittered_delay(backoff)`
3. Log warning with status code, delay, and attempt info
4. `await asyncio.sleep(delay)`
5. Return `min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)`

## Relationships

```
client.py ──imports──► redaction.py
    │                      │
    │                      ├── _safe_response_body (used by _handle_403)
    │                      └── _is_auth_403_body  (used by _handle_403)
    │
    ├── HostawayApiClient
    │       ├── domain methods (call _request)
    │       ├── _request (dispatches to handlers)
    │       ├── _handle_403 (uses redaction imports)
    │       ├── _handle_429 (uses module-level _calculate_backoff)
    │       ├── _handle_server_error (uses module-level _jittered_delay)
    │       ├── _parse_response
    │       └── _extract_results
    │
    └── Module-level utilities (remain in client.py)
            ├── _parse_retry_after
            ├── _calculate_backoff
            ├── _jittered_delay
            └── _is_server_error
```

## Validation Rules

- **No public API changes**: `__init__.py` exports are unmodified
- **No import changes for callers**: `HostawayApiClient` stays in `client.py`
- **One-way dependency**: `redaction.py` MUST NOT import from `client.py`
- **All 317 tests pass unmodified**: behavioral equivalence guaranteed
