<!-- markdownlint-disable MD013 -->

# Tasks: API Client Complexity Refactor

**Input**: Design documents from `/specs/004-api-client-refactor/`
**Prerequisites**: plan.md (required), spec.md (required), research.md,
data-model.md, quickstart.md

**Tests**: Not applicable — this is a behavior-preserving refactoring. The
existing 317 tests serve as the regression safety net (per research.md RT-05).
No new tests are required.

**Organization**: Tasks are grouped by user story to enable independent
implementation and verification of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `custom_components/hostaway/api/` for source, `tests/` for
  tests
- All paths relative to repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify baseline and prepare the branch for refactoring

- [ ] T001 Verify all 317 tests pass on current branch with
      `uv run pytest --tb=short -q`
- [ ] T002 Record baseline line counts:
      `wc -l custom_components/hostaway/api/client.py` (expect 890)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the new `redaction.py` module that US1 and US2 will depend
on

**⚠️ CRITICAL**: User Story 1 cannot complete until this module exists and
imports work correctly

- [ ] T003 Create `custom_components/hostaway/api/redaction.py` with SPDX header
      (`# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>`
      and `# SPDX-License-Identifier&#58; Apache-2.0`), module docstring, and
      `# aislop-ignore-file ai-slop/hallucinated-import` directive
- [ ] T004 Add imports to `custom_components/hostaway/api/redaction.py`: `json`,
      `logging`, `re`, `typing.Any`, `httpx`
- [ ] T005 Extract constants `_MAX_RESPONSE_BODY_LOG`, `_REDACTED`,
      `_SENSITIVE_KEY_TOKENS`, `_CONTROL_CHAR_RE`, `_SENSITIVE_KEY_PATTERN`,
      `_TEXT_REDACT_RE`, `_BEARER_RE` from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T006 Extract function `_is_sensitive_key(key: str) -> bool` from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T007 Extract function `_redact_sensitive(value: Any) -> Any` from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T008 Extract function `_redact_plain_text(text: str) -> str` from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T009 Extract function `_sanitize_for_log(text: str) -> str` from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T010 Extract function
      `_safe_response_body(response: httpx.Response, max_len: int = 500) -> str`
      from `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T011 Extract `_AUTH_403_PHRASES` tuple and
      `_is_auth_403_body(body: str) -> bool` function from
      `custom_components/hostaway/api/client.py` into
      `custom_components/hostaway/api/redaction.py`
- [ ] T012 Update `custom_components/hostaway/api/client.py` to import
      `_is_auth_403_body` and `_safe_response_body` from
      `custom_components.hostaway.api.redaction`
- [ ] T013 Remove extracted constants, functions, and unused imports
      (`import json`, `import re`) from
      `custom_components/hostaway/api/client.py`
- [ ] T014 Run full test suite (`uv run pytest --tb=short -q`) and confirm all
      317 tests pass after redaction extraction

**Checkpoint**: Foundation ready — `redaction.py` exists with all extracted
helpers, `client.py` imports from it, and all tests pass

---

## Phase 3: User Story 1 — Maintainer Works on Smaller, Focused Files (Priority: P1) 🎯 MVP

**Goal**: Reduce `client.py` from 890 lines to under 400 lines by completing the
redaction extraction (Phase 2) and consolidating repetitive domain method
patterns

**Independent Test**: Verify `wc -l custom_components/hostaway/api/client.py`
reports fewer than 400 lines,
`wc -l custom_components/hostaway/api/redaction.py` reports fewer than 400
lines, and all 317 tests pass

### Implementation for User Story 1

- [ ] T015 [US1] Identify repetitive response-parsing patterns across domain
      methods (`create_task`, `update_task`, `update_reservation`) in
      `custom_components/hostaway/api/client.py` and extract a shared
      `_mutate()` private helper method
- [ ] T016 [US1] Identify repetitive pagination patterns across domain methods
      (`get_all_listings`, `get_all_reservations`, `get_tasks`) in
      `custom_components/hostaway/api/client.py` and consolidate into concise
      inline patterns or shared helpers
- [ ] T017 [US1] Shorten domain method docstrings in
      `custom_components/hostaway/api/client.py` to minimum that satisfies
      interrogate (purpose line + Args/Returns/Raises without verbose prose)
- [ ] T018 [US1] Verify `custom_components/hostaway/api/client.py` is under 400
      lines with `wc -l`
- [ ] T019 [US1] Verify `custom_components/hostaway/api/redaction.py` is under
      400 lines with `wc -l`
- [ ] T020 [US1] Run full test suite (`uv run pytest --tb=short -q`) and confirm
      all 317 tests pass after domain consolidation
- [ ] T021 [US1] Run linting
      (`uv run ruff check custom_components/hostaway/api/`) and confirm zero
      violations
- [ ] T022 [US1] Run type checking
      (`uv run mypy custom_components/hostaway/api/`) and confirm zero errors
- [ ] T023 [US1] Run docstring coverage
      (`uv run interrogate -v custom_components/hostaway/api/`) and confirm 100%
      coverage

**Checkpoint**: `client.py` is under 400 lines, `redaction.py` is under 400
lines, all tests pass — User Story 1 complete

---

## Phase 4: User Story 2 — Maintainer Reads a Concise `_request()` Method (Priority: P2)

**Goal**: Decompose the 165-line `_request()` method into a dispatcher under 80
lines plus three focused per-status handler methods

**Independent Test**: Verify `_request()` spans fewer than 80 lines and all 317
tests pass with identical behavior

### Implementation for User Story 2

- [ ] T024 [US2] Extract
      `_handle_403(self, method: str, path: str, *, params, json, response, _retried_auth: bool) -> httpx.Response`
      private method in `custom_components/hostaway/api/client.py` encapsulating
      403 classification, logging, token invalidation, and auth-retry logic
- [ ] T025 [US2] Extract
      `_handle_429(self, response: httpx.Response, attempt: int, backoff: float) -> float`
      private method in `custom_components/hostaway/api/client.py` encapsulating
      rate-limit delay calculation, warning log, and sleep
- [ ] T026 [US2] Extract
      `_handle_server_error(self, response: httpx.Response, attempt: int, backoff: float) -> float`
      private method in `custom_components/hostaway/api/client.py` encapsulating
      5xx retry delay, warning log, and sleep
- [ ] T027 [US2] Refactor `_request()` in
      `custom_components/hostaway/api/client.py` to dispatch to `_handle_403()`,
      `_handle_429()`, and `_handle_server_error()` instead of inline logic
- [ ] T028 [US2] Verify `_request()` method is under 80 lines using
      `awk '/^    async def _request/,/^    (async )?def [a-z_]/' custom_components/hostaway/api/client.py | wc -l`
- [ ] T029 [US2] Run full test suite (`uv run pytest --tb=short -q`) and confirm
      all 317 tests pass after `_request()` decomposition
- [ ] T030 [US2] Run linting
      (`uv run ruff check custom_components/hostaway/api/`) and confirm zero
      violations
- [ ] T031 [US2] Run type checking
      (`uv run mypy custom_components/hostaway/api/`) and confirm zero errors

**Checkpoint**: `_request()` is under 80 lines, handler methods work correctly,
all tests pass — User Story 2 complete

---

## Phase 5: User Story 3 — Automated Quality Gate Passes (Priority: P3)

**Goal**: Confirm the aislop linter reports zero complexity violations for the
refactored modules

**Independent Test**: Run aislop and verify no `complexity/file-too-large` or
`complexity/function-too-long` violations

### Implementation for User Story 3

- [ ] T032 [US3] Verify no `complexity/file-too-large` violation for
      `custom_components/hostaway/api/client.py` (under 400 lines confirmed)
- [ ] T033 [US3] Verify no `complexity/function-too-long` violation for any
      function in `custom_components/hostaway/api/client.py` (all methods under
      80 lines)
- [ ] T034 [US3] Verify no `complexity/file-too-large` violation for
      `custom_components/hostaway/api/redaction.py` (under 400 lines confirmed)
- [ ] T035 [US3] Run pre-commit hooks on both files:
      `pre-commit run --files custom_components/hostaway/api/client.py custom_components/hostaway/api/redaction.py`

**Checkpoint**: All automated quality gates pass — User Story 3 complete

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, commit hygiene, and documentation

- [ ] T036 [P] Verify `custom_components/hostaway/api/__init__.py` exports are
      unchanged (no public API surface changes)
- [ ] T037 [P] Verify one-way dependency: confirm `redaction.py` does NOT import
      from `client.py` or any other `custom_components.hostaway.api.*` module
- [ ] T038 Run full pre-commit suite on all changed files
- [ ] T039 Create atomic commit 1: Extract redaction module (T003–T014) with
      conventional commit message and sign-off
- [ ] T040 Create atomic commit 2: Decompose `_request()` into handler methods
      (T024–T031) with conventional commit message and sign-off
- [ ] T041 Create atomic commit 3: Consolidate domain method patterns
      (T015–T023) with conventional commit message and sign-off
- [ ] T042 Run quickstart.md validation commands end-to-end to confirm all
      verification steps pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — verify baseline immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — creates `redaction.py` module
  that BLOCKS user stories
- **User Story 1 (Phase 3)**: Depends on Phase 2 completion (needs redaction
  extraction done)
- **User Story 2 (Phase 4)**: Depends on Phase 2 completion; can run in parallel
  with US1 if domain consolidation doesn't conflict with `_request()`
  decomposition, but sequential is safer
- **User Story 3 (Phase 5)**: Depends on US1 AND US2 (validates their combined
  result)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Phase 2 (redaction extraction) — domain
  consolidation can start after extraction
- **User Story 2 (P2)**: Depends on Phase 2 (redaction extraction) — handler
  methods use redaction imports; recommended to complete after US1 to avoid
  merge conflicts in same file
- **User Story 3 (P3)**: Depends on US1 + US2 — validation story that confirms
  combined results

### Within Each User Story

- Extraction/consolidation before verification
- Verify line counts before running test suite
- Test suite must pass before linting/type checks
- All checks green before declaring story complete

### Parallel Opportunities

- T003–T004 (SPDX header + imports) can be written as one file creation
- T005–T011 (constant/function extraction) are sequential moves from one file to
  another but can be done in a single editing pass
- T036–T037 (Polish phase verifications) can run in parallel
- US1 and US2 could theoretically be parallelized since they modify different
  sections of `client.py`, but sequential execution is recommended to avoid
  conflicts

---

## Parallel Example: Phase 2 (Foundational)

```bash
# All extractions target the same two files (client.py → redaction.py),
# so they are best done sequentially in a single editing pass:
# T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014

# However, T036 and T037 (Polish) can run in parallel:
Task: "Verify __init__.py exports unchanged"
Task: "Verify one-way dependency in redaction.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (verify baseline)
2. Complete Phase 2: Foundational (extract redaction module)
3. Complete Phase 3: User Story 1 (consolidate domain methods)
4. **STOP and VALIDATE**: `client.py` under 400 lines, all 317 tests pass
5. Commit 1 (extraction) + Commit 3 (consolidation) can be delivered as MVP

### Incremental Delivery

1. Phase 1 + Phase 2 → Foundation ready, tests still pass
2. Add User Story 1 → `client.py` < 400 lines → Commit 1 + 3 (MVP!)
3. Add User Story 2 → `_request()` < 80 lines → Commit 2
4. Add User Story 3 → Validate quality gates → No code changes needed
5. Each story preserves all 317 tests without modification

### Recommended Execution Order

Since all changes target the same file (`client.py`), the safest execution order
is:

1. **Phase 2** (T003–T014): Extract `redaction.py` — reduces `client.py` by ~170
   lines
2. **Phase 4 / US2** (T024–T031): Decompose `_request()` — this is cleanest to
   do on the smaller file before consolidation changes method ordering
3. **Phase 3 / US1** (T015–T023): Consolidate domain methods — final reduction
   to <400 lines
4. **Phase 5 / US3** (T032–T035): Validate quality gates
5. **Phase 6** (T036–T042): Polish, commits, final verification

### Atomic Commit Mapping

| Commit   | Tasks     | Message                                                     |
| -------- | --------- | ----------------------------------------------------------- |
| Commit 1 | T003–T014 | `Refactor: Extract redaction helpers into dedicated module` |
| Commit 2 | T024–T031 | `Refactor: Decompose _request() into per-status handlers`   |
| Commit 3 | T015–T023 | `Refactor: Consolidate repetitive domain method patterns`   |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- This is a behavior-preserving refactoring: NO new tests, NO public API changes
- All 317 existing tests must pass after EVERY phase — verify continuously
- Commit after each logical group (3 atomic commits planned)
- Stop at any checkpoint to validate independently
- Avoid: changing behavior, modifying tests, breaking public API, circular
  imports
