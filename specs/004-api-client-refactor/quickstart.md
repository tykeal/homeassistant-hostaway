# Quickstart: API Client Complexity Refactor

**Feature**: 004-api-client-refactor **Date**: 2026-06-15

## Prerequisites

- Python ≥3.14.2
- uv (dependency manager)
- All dev dependencies installed: `uv sync --group dev`
- Pre-commit hooks installed: `pre-commit install`

## Development Environment Setup

```bash
# Ensure you're on the feature branch
git checkout feat/004-api-client-refactor

# Install dependencies
uv sync --group dev

# Verify all tests pass before refactoring
uv run pytest --tb=short -q
# Expected: 317 passed
```

## Implementation Steps

### Step 1: Create `redaction.py`

Create `custom_components/hostaway/api/redaction.py` containing:

- SPDX license header
- `# aislop-ignore-file ai-slop/hallucinated-import` directive
- All redaction constants (`_MAX_RESPONSE_BODY_LOG`, `_REDACTED`,
  `_SENSITIVE_KEY_TOKENS`, `_CONTROL_CHAR_RE`, `_SENSITIVE_KEY_PATTERN`,
  `_TEXT_REDACT_RE`, `_BEARER_RE`)
- All redaction functions (`_is_sensitive_key`, `_redact_sensitive`,
  `_redact_plain_text`, `_sanitize_for_log`, `_safe_response_body`)
- 403-body classifier (`_AUTH_403_PHRASES`, `_is_auth_403_body`)

### Step 2: Update `client.py` imports

Replace the removed constants/functions with imports from redaction:

```python
from custom_components.hostaway.api.redaction import (
    _is_auth_403_body,
    _safe_response_body,
)
```

Remove `import json` and `import re` (no longer needed in client.py).

### Step 3: Decompose `_request()`

Extract three private methods on `HostawayApiClient`:

- `_handle_403(...)` — 403 classification and auth-retry
- `_handle_429(...)` — rate-limit retry with backoff
- `_handle_server_error(...)` — 5xx retry with backoff

### Step 4: Consolidate domain methods

Reduce repetition in paginated fetch methods and mutation methods via shared
private helper patterns to achieve <400 line target.

## Verification Commands

```bash
# Run full test suite (must pass with zero modifications)
uv run pytest --tb=short -q
# Expected: 317 passed

# Verify line counts
wc -l custom_components/hostaway/api/client.py
# Expected: <400

wc -l custom_components/hostaway/api/redaction.py
# Expected: <400 (targeting ~185)

# Check _request() line count
python - <<'PY'
from pathlib import Path

lines = (
    Path("custom_components/hostaway/api/client.py")
    .read_text()
    .splitlines()
)
start = next(
    i
    for i, line in enumerate(lines)
    if line.startswith("    async def _request")
)
end = next(
    (
        i
        for i in range(start + 1, len(lines))
        if lines[i].startswith("    def ")
        or lines[i].startswith("    async def ")
    ),
    len(lines),
)
print(end - start)
PY
# Expected: <80

# Run linting
uv run ruff check custom_components/hostaway/api/
uv run ruff format --check custom_components/hostaway/api/

# Run type checking
uv run mypy custom_components/hostaway/api/

# Run docstring coverage
uv run interrogate -v custom_components/hostaway/api/

# Run pre-commit on changed files
pre-commit run --files custom_components/hostaway/api/client.py \
  custom_components/hostaway/api/redaction.py
```

## Atomic Commit Plan

```bash
# Commit 1: Extract redaction module
git add custom_components/hostaway/api/redaction.py
git add custom_components/hostaway/api/client.py
git commit -s -m "Refactor: Extract redaction helpers into dedicated module

Move redaction constants, regex patterns, sanitization functions,
and 403-body classification logic from client.py into redaction.py.
No behavioral changes — all 317 tests pass unmodified.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

# Commit 2: Decompose _request() into handler methods
git add custom_components/hostaway/api/client.py
git commit -s -m "Refactor: Decompose _request() into per-status handlers

Extract _handle_403(), _handle_429(), and _handle_server_error()
private methods from the monolithic _request() method. Reduces
_request() to <80 lines while preserving identical behavior.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

# Commit 3: Consolidate domain method patterns
git add custom_components/hostaway/api/client.py
git commit -s -m "Refactor: Consolidate repetitive domain method patterns

Reduce domain method line count via shared helper patterns.
Achieves <400 line target for client.py. All 317 tests pass.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Key Constraints

- **Zero test modifications** — all 317 tests must pass as-is
- **Zero public API changes** — `__init__.py` exports unchanged
- **One-way dependency** — `redaction.py` must NOT import from `client.py`
- **Pre-commit must pass** — ruff, mypy, interrogate, reuse-tool, etc.
- **Conventional commits** — capitalized type prefix, sign-off, co-author
