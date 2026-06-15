# Research: API Client Complexity Refactor

**Feature**: 004-api-client-refactor
**Date**: 2026-06-15
**Status**: Complete

## Research Tasks

### RT-01: Module Extraction Pattern for Python

**Question**: What is the best practice for extracting functions from one
module into a sibling module within the same Python package?

**Decision**: Extract all redaction/logging helpers as module-level functions
into `redaction.py` within the same `custom_components/hostaway/api/` package.
Import them into `client.py` using the project's established fully-qualified
import style.

**Rationale**:

- Keeps the dependency unidirectional (`client.py` → `redaction.py`); no
  circular imports possible since `redaction.py` has zero imports from the
  api package.
- Module-level functions (not class methods) are the correct choice because
  the redaction helpers are pure functions with no class state dependency.
- The existing `__init__.py` does NOT export redaction helpers (they are
  underscore-prefixed private functions), so the public API surface remains
  unchanged.
- `redaction.py` will include `_safe_response_body` as the only function
  that depends on `httpx.Response`, making the coupling explicit and minimal.

**Alternatives considered**:

- `utils.py` catch-all — rejected; `redaction.py` is a focused,
  single-responsibility module name that communicates intent.
- Sub-package `api/redaction/` — rejected; over-engineering for ~180 lines.
- Relative imports (`from .redaction import ...`) — rejected; the existing
  codebase consistently uses fully-qualified absolute imports throughout.

---

### RT-02: Method Decomposition Strategy for `_request()`

**Question**: How should the `_request()` method be decomposed to maintain
the retry loop structure while extracting per-status handler logic?

**Decision**: Extract three private methods on `HostawayApiClient`:

1. `_handle_403(method, path, params, json, response, _retried_auth)` —
   handles 403 classification, logging, token invalidation, and auth-retry.
   Returns an `httpx.Response` on successful retry; raises on failure.
2. `_handle_429(response, attempt, backoff)` — calculates rate-limit delay,
   logs warning, sleeps. Returns updated backoff value; raises
   `HostawayRateLimitError` at max retries.
3. `_handle_server_error(response, attempt, backoff)` — calculates 5xx
   retry delay, logs warning, sleeps. Returns updated backoff; raises
   `HostawayConnectionError` at max retries.

**Rationale**:

- Keeps `_request()` as the single orchestration point — callers unchanged.
- Each handler encapsulates exactly one HTTP status concern.
- The recursive `_request()` call for 403 auth-retry lives naturally in
  `_handle_403` since it needs all original request parameters.
- `_handle_429` and `_handle_server_error` handle their own `asyncio.sleep`
  calls, keeping the retry loop body minimal.

**Alternatives considered**:

- Strategy/dispatch pattern with handler classes — rejected; over-engineering
  for 3 simple cases within one private method.
- Extracting the entire retry loop into a decorator — rejected; the loop has
  complex state (backoff accumulator, attempt counter, token refresh) that
  doesn't map to a clean decorator interface.
- `match/case` on status code — complementary but insufficient for line
  reduction alone; the handler bodies are what consume lines.

---

### RT-03: Line Count Feasibility Analysis

**Question**: Will the proposed extraction achieve the <400 line target for
`client.py` and <80 line target for `_request()`?

**Decision**: Both targets are achievable with the following combined strategy:

1. **Redaction extraction** (−170 lines)
2. **Domain method consolidation** via shared private helpers (−150-200 lines)
3. **`_request()` decomposition** (achieves <80 target for that method)

**Analysis — Current `client.py` (890 lines)**:

| Section | Lines | After refactor |
|---------|-------|----------------|
| SPDX header + imports | 38 | ~30 (remove `json`, `re`; add redaction import) |
| Redaction constants + functions + 403-classifier | 170 | 0 (moved to `redaction.py`) |
| Class def + `__init__` | 35 | ~35 |
| Domain methods (12 methods) | 362 | ~200 (consolidated via helpers) |
| `_request()` | 165 | ~65 (dispatches to handlers) |
| Handler methods (new) | 0 | ~60 (`_handle_403/429/server_error`) |
| `_parse_response` + `_extract_results` | 55 | ~55 |
| Module-level retry utilities | 62 | ~62 |
| **Total** | **890** | **~370** ✅ |

**Domain method consolidation strategy**:

- `create_task`, `update_task`, `update_reservation` share identical
  response-parsing logic → extract `_mutate(method, path, json)` helper
- `get_all_listings`, `get_all_reservations`, `get_tasks` share pagination
  loop logic → extract `_paginate_*` helpers or use concise inline patterns
- Docstrings shortened to minimum that satisfies interrogate (purpose line +
  Args/Returns/Raises without verbose prose descriptions)

**`_request()` post-decomposition** (~65 lines):

- Signature + docstring: ~20 lines
- URL + backoff init: 2 lines
- Retry loop header: 1 line
- Token acquisition: 1 line
- HTTP request try/except (network error): ~12 lines
- Status dispatch (403/404/429/5xx/non-success): ~15 lines
- Return + fallback: 4 lines
- Total: ~55-65 lines ✅ under 80

**`redaction.py` estimate** (~185 lines):

- SPDX header + aislop directive: 4 lines
- Imports (`re`, `json`, `httpx`, typing): 8 lines
- Constants + regex definitions: 33 lines
- `_is_sensitive_key`: 4 lines
- `_redact_sensitive`: 18 lines
- `_redact_plain_text`: 10 lines
- `_sanitize_for_log`: 4 lines
- `_safe_response_body`: 49 lines
- `_AUTH_403_PHRASES` + `_is_auth_403_body`: 35 lines
- Blank lines + comments: ~20 lines
- Total: ~185 lines ✅ well under 400

---

### RT-04: Import Graph and Circular Dependency Prevention

**Question**: How do we ensure no circular imports between `client.py` and
`redaction.py`?

**Decision**: Strict one-way dependency: `client.py` imports from
`redaction.py`. The new module imports ONLY from stdlib (`re`, `json`,
`logging`) and `httpx` (for the `Response` type in `_safe_response_body`).

**Rationale**:

- `redaction.py` has no knowledge of `HostawayApiClient` or any other
  api-package symbols.
- The `_is_auth_403_body` function takes a plain `str` argument (the
  already-extracted body text), not a response object — this was a
  deliberate design choice in the original code that enables clean
  extraction.
- `_safe_response_body` takes an `httpx.Response` directly, which is a
  third-party type, not a project type — no coupling to client internals.

**Verification**: After extraction, `redaction.py`'s imports will be:

```python
import json
import logging
import re
from typing import Any

import httpx
```

No imports from `custom_components.hostaway.*` — zero coupling risk.

---

### RT-05: TDD Applicability for Pure Refactoring

**Question**: Does the constitution's TDD requirement (Red-Green-Refactor)
apply to a behavior-preserving refactoring?

**Decision**: TDD's "write a failing test first" does not apply to pure
refactoring where behavior is unchanged. The 317 existing tests serve as
the "green" baseline. The refactoring follows the "Refactor" phase of TDD:
restructure code while keeping all tests green.

**Rationale**:

- Constitution Section I states: "Red-Green-Refactor cycle is strictly
  enforced: 1. Write a failing test. 2. Implement minimum code. 3. Refactor
  while keeping all tests green."
- This feature IS step 3 — pure refactoring of existing, tested code.
- No new behavior is introduced, so no new tests are needed.
- The existing 317 tests provide the regression safety net.

**Validation**: Run `pytest` after each atomic commit to confirm all tests
remain green throughout the refactoring process.

---

## Summary of Findings

All research questions are resolved. Key findings:

1. **Extraction target**: 170 lines move to `redaction.py` (constants,
   regex patterns, 5 helper functions, 403-classifier)
2. **Line budget**: <400 target requires domain method consolidation in
   addition to redaction extraction; achievable via shared helpers and
   concise docstrings
3. **`_request()` target**: <80 lines achievable by extracting 3 handler
   methods that own their own sleep/raise logic
4. **No circular imports**: `redaction.py` depends only on stdlib + httpx
5. **TDD**: Existing 317 tests serve as refactoring safety net; no new
   tests required for behavior-preserving changes
