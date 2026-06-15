<!-- markdownlint-disable MD013 -->

# Implementation Plan: API Client Complexity Refactor

**Branch**: `feat/004-api-client-refactor` | **Date**: 2026-06-15 | **Spec**:
specs/004-api-client-refactor/spec.md **Input**: Feature specification from
`specs/004-api-client-refactor/spec.md`

## Summary

Refactor `custom_components/hostaway/api/client.py` (currently 890 lines) to
reduce complexity below aislop thresholds: extract redaction/logging helpers and
constants into a new `redaction.py` module, and decompose the 165-line
`_request()` method into smaller per-status handler methods (`_handle_403`,
`_handle_429`, `_handle_server_error`). The refactoring is behavior-preserving —
all 317 existing tests must pass without modification.

## Technical Context

**Language/Version**: Python ≥3.14.2 **Primary Dependencies**: httpx 0.28.1,
Home Assistant ≥2026.5.4 **Storage**: N/A (stateless HTTP client) **Testing**:
pytest ≥8.0, pytest-asyncio ≥0.25, pytest-cov ≥6.0, respx ≥0.22,
pytest-homeassistant-custom-component ≥0.13.339 **Target Platform**: Home
Assistant custom component (Linux/generic) **Project Type**: Library (custom
integration API client) **Performance Goals**: Must not block HA event loop;
async throughout **Constraints**: `client.py` < 400 lines, `_request()` < 80
lines, `redaction.py` < 400 lines, zero public API changes **Scale/Scope**:
Single module extraction + method decomposition within existing 890-line file

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

| Principle                           | Status  | Notes                                                                                                                          |
| ----------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------ |
| I. Code Quality & Testing           | ✅ PASS | TDD not applicable to pure refactoring (tests already exist and must remain green); all linting/type checks will be maintained |
| II. API Client Design               | ✅ PASS | Refactoring improves isolation; no behavioral changes to client                                                                |
| III. Atomic Commit Discipline       | ✅ PASS | Will use atomic commits: (1) extract redaction module, (2) decompose \_request()                                               |
| IV. Licensing & Attribution         | ✅ PASS | New `redaction.py` will include SPDX headers per constitution                                                                  |
| V. Pre-Commit Integrity             | ✅ PASS | All hooks will pass; ruff, mypy, interrogate 100% enforced                                                                     |
| VI. Agent Co-Authorship & DCO       | ✅ PASS | Commits will include Co-authored-by and Signed-off-by                                                                          |
| VII. User Experience                | ✅ N/A  | No user-facing changes                                                                                                         |
| VIII. Performance                   | ✅ PASS | No behavioral changes; async patterns preserved                                                                                |
| IX. Phased Development              | ✅ PASS | Single-phase refactoring with clear atomic steps                                                                               |
| X. Security & Credential Management | ✅ PASS | Redaction logic preserved identically; no credential changes                                                                   |

**Gate Result**: ✅ ALL GATES PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/004-api-client-refactor/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
custom_components/hostaway/api/
├── __init__.py          # Public API surface (unchanged)
├── auth.py              # Token management (unchanged)
├── client.py            # API client class + retry utilities (refactored: <400 lines)
├── const.py             # API constants (unchanged)
├── exceptions.py        # Exception classes (unchanged)
├── models.py            # Data models (unchanged)
└── redaction.py         # NEW: redaction helpers, constants, regexes, 403-body classifier

tests/
├── api/
│   ├── test_client.py   # Existing client tests (unmodified)
│   └── ...
└── ...                  # All 317 tests remain green
```

**Structure Decision**: Single project layout. The new `redaction.py` is added
to the existing `custom_components/hostaway/api/` package alongside `client.py`.
No new directories are needed — this is an internal module extraction within an
existing package.

## Complexity Tracking

> No violations to justify. This refactoring _reduces_ complexity.

## Constitution Re-Check (Post-Phase 1 Design)

| Principle                           | Status  | Notes                                                                                                         |
| ----------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------- |
| I. Code Quality & Testing           | ✅ PASS | No new behavior = no new tests needed; interrogate 100% enforced on new `redaction.py` and refactored methods |
| II. API Client Design               | ✅ PASS | Design maintains clean abstraction; handler decomposition improves readability                                |
| III. Atomic Commit Discipline       | ✅ PASS | 3 atomic commits planned: (1) extract module, (2) decompose `_request()`, (3) consolidate domain methods      |
| IV. Licensing & Attribution         | ✅ PASS | `redaction.py` includes SPDX header per constitution format                                                   |
| V. Pre-Commit Integrity             | ✅ PASS | All hooks enforced; no bypassing                                                                              |
| VI. Agent Co-Authorship & DCO       | ✅ PASS | All commits carry Co-authored-by + Signed-off-by                                                              |
| VII. User Experience                | ✅ N/A  | No user-facing changes                                                                                        |
| VIII. Performance                   | ✅ PASS | Zero behavioral changes; extra function call overhead negligible                                              |
| IX. Phased Development              | ✅ PASS | Single delivery phase; each commit independently green                                                        |
| X. Security & Credential Management | ✅ PASS | Redaction logic preserved identically in new location                                                         |

**Post-Design Gate Result**: ✅ ALL GATES PASS — design is
constitution-compliant.
