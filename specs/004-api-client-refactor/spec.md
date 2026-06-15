# Feature Specification: API Client Complexity Refactor

**Feature Branch**: `feat/004-api-client-refactor`
**Created**: 2026-06-15
**Status**: Draft
**Input**: Refactor api/client.py to reduce complexity — extract redaction/logging helpers into a new module and decompose `_request()` into smaller handler methods.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Maintainer Works on Smaller, Focused Files (Priority: P1)

A developer working on the Hostaway integration opens `client.py` and finds a file under 400 lines that contains only the API client class and its domain methods. Redaction and logging concerns live in a dedicated `redaction.py` module. The developer can reason about HTTP request logic without scrolling through unrelated string-manipulation code.

**Why this priority**: The primary goal of this feature is reducing file size below the 400-line aislop threshold so automated quality checks pass.

**Independent Test**: Can be verified by counting the lines in `client.py` after refactoring and confirming it is under 400 lines, while all 317 existing tests still pass.

**Acceptance Scenarios**:

1. **Given** the refactored codebase, **When** a developer opens `client.py`, **Then** the file contains fewer than 400 lines
2. **Given** the refactored codebase, **When** a developer opens `redaction.py`, **Then** it contains all redaction/logging helper functions and related constants previously in `client.py`
3. **Given** the refactored codebase, **When** the full test suite is run, **Then** all 317 tests pass without modification

---

### User Story 2 - Maintainer Reads a Concise `_request()` Method (Priority: P2)

A developer reviewing the retry/error-handling logic in `_request()` finds a method under 80 lines that clearly shows the request flow: make the request, then dispatch to per-status handlers for 403, 429, and 5xx responses. The detailed handling logic for each status lives in focused private methods.

**Why this priority**: The second aislop violation (`complexity/function-too-long` max 80 lines) requires decomposing `_request()` into handler methods.

**Independent Test**: Can be verified by counting lines in `_request()` after refactoring and confirming it is under 80 lines, while behavior remains identical.

**Acceptance Scenarios**:

1. **Given** the refactored `_request()` method, **When** a developer reads it, **Then** the method body spans fewer than 80 lines
2. **Given** the refactored class, **When** a 403 response is received, **Then** `_handle_403()` is invoked and produces identical behavior to the current inline logic
3. **Given** the refactored class, **When** a 429 response is received, **Then** `_handle_429()` is invoked and produces identical behavior to the current inline logic
4. **Given** the refactored class, **When** a 5xx response is received, **Then** `_handle_server_error()` is invoked and produces identical behavior to the current inline logic

---

### User Story 3 - Automated Quality Gate Passes (Priority: P3)

A CI pipeline running aislop against the codebase no longer flags `complexity/file-too-large` or `complexity/function-too-long` for the API client module. The team can merge PRs without complexity waivers.

**Why this priority**: This is the observable outcome that validates the refactoring achieved its purpose, but the actual work is covered by P1 and P2.

**Independent Test**: Can be verified by running the aislop linter and confirming no complexity violations are reported for `client.py`.

**Acceptance Scenarios**:

1. **Given** the refactored codebase, **When** aislop runs, **Then** no `complexity/file-too-large` violation is reported for `client.py`
2. **Given** the refactored codebase, **When** aislop runs, **Then** no `complexity/function-too-long` violation is reported for any function in `client.py`
3. **Given** the new `redaction.py` file, **When** aislop runs, **Then** no `complexity/file-too-large` violation is reported (file is well under 400 lines)

---

### Edge Cases

- What happens when `redaction.py` functions are imported by `client.py`? The import path must be correct (`custom_components.hostaway.api.redaction`) and the `# aislop-ignore-file ai-slop/hallucinated-import` directive must be present in the new file.
- What happens when `_handle_403()` needs to recursively call `_request()`? The handler must have access to the method's parameters and must correctly pass `_retried_auth=True` on the retry call.
- What happens when circular imports could occur? The new `redaction.py` must not import from `client.py`; the dependency is one-way (`client.py` → `redaction.py`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST extract all redaction/logging helper functions (`_safe_response_body`, `_redact_sensitive`, `_redact_plain_text`, `_sanitize_for_log`, `_is_sensitive_key`) into a new `custom_components/hostaway/api/redaction.py` module
- **FR-002**: System MUST extract all redaction-related constants and regex patterns (`_MAX_RESPONSE_BODY_LOG`, `_REDACTED`, `_SENSITIVE_KEY_TOKENS`, `_CONTROL_CHAR_RE`, `_SENSITIVE_KEY_PATTERN`, `_TEXT_REDACT_RE`, `_BEARER_RE`) into `redaction.py`
- **FR-003**: System MUST extract the `_AUTH_403_PHRASES` tuple and `_is_auth_403_body()` function into `redaction.py`
- **FR-004**: System MUST decompose `_request()` by extracting a `_handle_403()` private method on `HostawayApiClient` that encapsulates 403 classification and auth-retry logic
- **FR-005**: System MUST decompose `_request()` by extracting a `_handle_429()` private method on `HostawayApiClient` that encapsulates rate-limit retry logic
- **FR-006**: System MUST decompose `_request()` by extracting a `_handle_server_error()` private method on `HostawayApiClient` that encapsulates 5xx retry logic
- **FR-007**: System MUST retain all domain methods, `_parse_response()`, `_extract_results()`, and module-level retry utilities (`_jittered_delay`, `_calculate_backoff`, `_parse_retry_after`, `_is_server_error`) in `client.py`
- **FR-008**: System MUST include an SPDX license header and `# aislop-ignore-file ai-slop/hallucinated-import` directive in the new `redaction.py` file
- **FR-009**: System MUST NOT change any public API surface — all existing callers and imports continue to work without modification
- **FR-010**: System MUST pass all 317 existing tests without any test modifications
- **FR-011**: The refactored `client.py` MUST be under 400 lines total
- **FR-012**: The refactored `_request()` method MUST be under 80 lines
- **FR-013**: The new `redaction.py` MUST be well under 400 lines total

### Key Entities

- **HostawayApiClient**: The HTTP client class that remains in `client.py` with all domain methods and a simplified `_request()` that dispatches to per-status handlers
- **Redaction module**: New `redaction.py` containing all logging sanitization functions, sensitive-key detection, regex patterns, and 403-body classification logic
- **Per-status handlers**: New private methods (`_handle_403`, `_handle_429`, `_handle_server_error`) extracted from `_request()` to reduce its line count

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `client.py` contains fewer than 400 lines after refactoring (currently 890 lines)
- **SC-002**: `_request()` method contains fewer than 80 lines after refactoring (currently ~165 lines)
- **SC-003**: All 317 existing tests pass without modification
- **SC-004**: No automated complexity linter violations are reported for `client.py` or `redaction.py`
- **SC-005**: `redaction.py` is well under 400 lines (expected ~180-200 lines)
- **SC-006**: Zero public API changes — all external callers and imports remain unchanged

## Assumptions

- The existing test suite (317 tests) provides sufficient coverage to validate behavioral equivalence after refactoring
- The `# aislop-ignore-file ai-slop/hallucinated-import` directive is an established project convention for in-repo imports
- The SPDX header format follows the existing pattern: `# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>` and `# SPDX-License-Identifier: Apache-2.0`
- Python 3.14 is the target runtime as specified in the project configuration
- The `redaction.py` module only needs to export functions used by `client.py`; no other modules currently depend on the redaction helpers
- Module-level retry utility functions (`_jittered_delay`, `_calculate_backoff`, `_parse_retry_after`, `_is_server_error`) remain in `client.py` because they are tightly coupled to the request flow and do not contribute significantly to line count
