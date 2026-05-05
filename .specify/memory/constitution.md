<!--
  Sync Impact Report
  ==================================================
  Version change: 0.0.0 → 1.0.0
  Change type: MAJOR - Initial constitution ratification

  Modified principles: N/A (initial creation)

  Added sections:
    - I. Code Quality & Testing Standards (NON-NEGOTIABLE)
    - II. API Client Design
    - III. Atomic Commit Discipline (NON-NEGOTIABLE)
    - IV. Licensing & Attribution Standards (NON-NEGOTIABLE)
    - V. Pre-Commit Integrity (NON-NEGOTIABLE)
    - VI. Agent Co-Authorship & DCO Requirements (NON-NEGOTIABLE)
    - VII. User Experience Consistency
    - VIII. Performance Requirements
    - IX. Phased Development
    - X. Security & Credential Management
    - Additional Constraints
    - Development Workflow & Quality Gates
    - Governance

  Removed sections: None

  Templates requiring updates:
    - .specify/templates/plan-template.md ✅ no change needed
    - .specify/templates/spec-template.md ✅ no change needed
    - .specify/templates/tasks-template.md ✅ no change needed

  Follow-up TODOs: None
  ==================================================
-->

# Hostaway Integration Constitution

## Core Principles

### I. Code Quality & Testing Standards (NON-NEGOTIABLE)

- All source code MUST pass configured linting and static analysis
  checks (ruff, mypy, interrogate) with zero errors or warnings.
- Every function and class MUST include a docstring that describes
  its purpose, parameters, return values, and raised exceptions.
- Type annotations MUST be present on all public function signatures.
- Interrogate MUST enforce 100% docstring coverage; commits that
  reduce coverage are PROHIBITED.
- **Code-level TDD is mandatory.** Every unit of production code
  MUST be preceded by a failing test that defines the desired
  behavior. The Red-Green-Refactor cycle is strictly enforced:
  1. Write a failing test that defines the desired behavior.
  2. Implement the minimum code required to make the test pass.
  3. Refactor while keeping all tests green.
- CI tests MUST pass before any manual or exploratory testing is
  performed. Manual testing without green CI is PROHIBITED.
- Test coverage MUST be maintained or increased with every change;
  coverage regressions MUST be justified and approved.

**Rationale**: This integration interacts with the Hostaway property
management platform where data integrity directly affects property
listings, reservations, guest records, and financial transactions.
Defective code can corrupt live booking data or cause silent data
loss. Rigorous quality gates are essential for reliability.

### II. API Client Design

- The Hostaway API client MUST be implemented as a clean abstraction
  layer that isolates all HTTP communication from business logic.
- The client MUST handle OAuth 2.0 authentication including token
  acquisition, refresh, and expiry detection transparently.
- The client MUST implement rate limiting awareness: it MUST respect
  Hostaway API rate limits and implement exponential backoff with
  jitter on 429 responses.
- The client MUST handle pagination for all list endpoints, providing
  async iterators or equivalent patterns to callers.
- Error handling MUST translate Hostaway API errors into well-typed
  Python exceptions with actionable context (HTTP status, endpoint,
  and response body excerpt).
- The client MUST be independently testable without requiring a live
  Hostaway API connection. All external HTTP calls MUST be mockable
  via dependency injection or fixture-based patterns.
- Request and response data MUST be validated against expected
  schemas; malformed API responses MUST raise explicit errors rather
  than propagating silently.

**Rationale**: The Hostaway API surface covers listings, reservations,
guests, calendars, pricing, channels, financials, conversations, and
webhooks. A well-isolated client layer enables independent testing,
simplifies debugging, and protects against upstream API changes
breaking unrelated integration logic.

### III. Atomic Commit Discipline (NON-NEGOTIABLE)

- Every commit MUST represent exactly one logical change (one
  feature, one fix, or one refactor).
- Each commit MUST compile and run successfully; broken intermediate
  states are PROHIBITED.
- Commit messages MUST follow Conventional Commits with capitalized
  types as defined in `AGENTS.md` and `.gitlint` (types: Fix, Feat,
  Chore, Docs, Style, Refactor, Perf, Test, Revert, CI, Build).
- Large features MUST be broken into multiple atomic commits.
- Mixing unrelated changes in a single commit is PROHIBITED.
- Task tracking document updates (e.g., `tasks.md`) MUST be
  committed separately from the code they track.

**Rationale**: Atomic commits enable clean git history for debugging
and auditing, easy reversion of specific changes without affecting
unrelated code, clear code review, and bisect-friendly debugging.

### IV. Licensing & Attribution Standards (NON-NEGOTIABLE)

- Each new or modified file MUST be REUSE-compliant, either by
  including correct SPDX license and copyright headers in the file
  itself or by ensuring the file is covered by an appropriate entry
  in `REUSE.toml`.
- Python files that use inline SPDX headers MUST use:
  ```
  # SPDX-FileCopyrightText: YYYY Andrew Grimberg <tykeal@bardicgrove.org>
  # SPDX-License-Identifier: Apache-2.0
  ```
- Markup and XML files that use inline SPDX headers MUST use block
  comment equivalents.
- The project follows the REUSE specification. Every file MUST be
  covered by an SPDX header or an entry in `REUSE.toml`.
- The reuse-tool pre-commit hook enforces compliance; files that are
  not properly covered by an SPDX header or `REUSE.toml` MUST NOT
  be committed.

**Rationale**: Proper attribution protects contributors and users,
ensures clear licensing terms, and maintains compliance with open
source best practices and Apache-2.0 license requirements.

### V. Pre-Commit Integrity (NON-NEGOTIABLE)

- All pre-commit hooks MUST pass locally prior to any push.
  Bypassing hooks with `--no-verify` is **PROHIBITED** under all
  circumstances.
- Pre-commit requirements include (per `.pre-commit-config.yaml`):
  - File integrity checks (no large files, valid AST, proper line
    endings)
  - Conventional commit message format (gitlint)
  - Code formatting and linting (ruff, ruff-format)
  - YAML validation (yamllint)
  - Type checking (mypy)
  - Documentation coverage (interrogate at 100%)
  - License compliance (reuse-tool)
  - GitHub Actions validation (actionlint)
  - Markdown linting (markdownlint)
  - Shell script analysis (shellcheck, bashate)
  - Spell checking (codespell)
  - Python project validation (validate-pyproject)

**Failure Recovery Protocol**:
1. Fix the issues identified by the pre-commit hooks.
2. Stage the fixes with `git add`.
3. Attempt the commit again as if the prior attempt never happened.
4. Do NOT use `git reset` after a failed commit attempt.

**Rationale**: Pre-commit hooks are the first line of defense against
defects, security issues, licensing violations, and technical debt.
Bypassing them creates risk for the entire codebase.

### VI. Agent Co-Authorship & DCO Requirements (NON-NEGOTIABLE)

- All commits authored or co-authored by AI agents MUST include
  proper attribution and sign-off.
- Every agent-assisted commit MUST include a `Co-authored-by`
  trailer identifying the AI agent:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```
  (Use the appropriate agent name and email address.)
- Every commit MUST carry a DCO sign-off added via `git commit -s`:
  ```
  Signed-off-by: Andrew Grimberg <tykeal@bardicgrove.org>
  ```
- The `Co-authored-by` trailer goes in the commit message body;
  `git commit -s` appends the `Signed-off-by` line last.

**Rationale**: Transparency in authorship is critical for legal
compliance (Developer Certificate of Origin), audit trails for code
provenance, understanding AI contribution patterns, and maintaining
trust in the development process.

### VII. User Experience Consistency

- Configuration UI MUST follow Home Assistant config flow patterns
  (discovery, user input, reauth, options flow).
- The config flow MUST support Hostaway OAuth 2.0 credential entry
  and listing selection.
- Entity naming MUST follow established HA conventions
  (e.g., `sensor.hostaway_<listing>_<attribute>`).
- State attributes MUST maintain backward compatibility unless
  explicitly versioned with a migration path.
- Error messages MUST be clear, actionable, and user-friendly,
  describing what went wrong and suggesting corrective steps.
- Breaking changes to entity structure, service calls, or
  configuration options MUST be documented and versioned before
  release.

**Rationale**: Users depend on predictable behavior. This integration
bridges Home Assistant and the Hostaway property management platform;
inconsistent UX can lead to misconfigured automations affecting live
property operations.

### VIII. Performance Requirements

- All I/O operations MUST use Home Assistant async patterns;
  blocking the HA event loop is PROHIBITED.
- Blocking calls MUST be offloaded to executor threads via
  `hass.async_add_executor_job(...)`.
- API polling intervals MUST be configurable with sensible defaults
  and enforced minimums to avoid exceeding Hostaway rate limits.
- API responses MUST be cached where appropriate to reduce redundant
  network calls; cache invalidation strategy MUST be documented.
- The integration MUST NOT impose measurable overhead on the Home
  Assistant event loop during normal operation.
- Rate limit awareness MUST be built into the API client; the
  client MUST NOT exceed Hostaway's published rate limits.
- Resource consumption (memory, CPU, I/O) MUST be considered during
  design; memory leaks are PROHIBITED.

**Rationale**: Home Assistant runs on diverse hardware (Raspberry Pi
to full servers). Poor performance degrades the entire HA instance.
The Hostaway API has rate limits; exceeding them disrupts the
integration and potentially affects other API consumers on the same
Hostaway account.

### IX. Phased Development

- Development MUST proceed in defined phases; each phase delivers
  an independently testable increment of functionality.
- Unit-level TDD (Red-Green-Refactor) MUST NOT be deferred under
  any circumstance. Higher-level tests that span multiple stories
  or depend on infrastructure from later phases MAY be deferred to
  the phase where their prerequisites exist.
- Each phase MUST conclude with a checkpoint where all CI tests
  pass and the increment is validated before the next phase begins.
- Phase boundaries MUST be documented in the implementation plan
  (`plan.md`) and task list (`tasks.md`).

**Rationale**: The Hostaway API surface is extensive. Phased
delivery prevents scope creep, ensures each increment is stable
before building upon it, and allows early validation of the API
client layer before implementing higher-level features.

### X. Security & Credential Management

- OAuth tokens, API keys, and any credentials MUST NEVER be
  committed to source control under any circumstances.
- Credentials MUST be stored exclusively via Home Assistant's
  built-in config entry storage mechanism.
- OAuth token refresh MUST be handled transparently by the API
  client; callers MUST NOT manage token lifecycle directly.
- Data transmitted to the Hostaway API MUST be validated before
  sending; malformed payloads MUST be rejected with clear error
  messages rather than forwarded to the API.
- Webhook endpoints (if implemented) MUST validate incoming
  request authenticity before processing events.
- Sensitive data (tokens, guest PII) MUST NOT appear in debug
  logs; log sanitization MUST be enforced.

**Rationale**: The Hostaway API provides access to property listings,
guest personal information, financial transactions, and reservation
data. Credential exposure or data leakage represents a severe
security and privacy risk for property managers and their guests.

## Additional Constraints

- **Language & Runtime**: Python 3.x with full type annotation
  coverage enforced by mypy.
- **Dependency Management**: Dependencies MUST be managed via `uv`.
  Once the project has dependencies, a locked dependency file
  (`uv.lock`) MUST be committed to the repository.
- **Home Assistant Compatibility**: The integration MUST follow
  Home Assistant's custom component conventions and remain
  compatible with the targeted minimum HA version specified in
  `manifest.json` or `hacs.json`.
- **Hostaway API Versioning**: The integration MUST document which
  Hostaway API version it targets. Changes to the Hostaway API
  that break compatibility MUST be addressed in a dedicated
  migration phase.
- **Data Validation**: All data pushed to Hostaway MUST be validated
  against expected schemas before transmission. Silent data
  corruption is PROHIBITED.
- **License Compliance**: The project follows the REUSE
  specification. Every file MUST be covered by an SPDX header
  or an entry in `REUSE.toml`.

## Development Workflow & Quality Gates

1. **Write tests** for the current phase or story (TDD red phase).
2. **Implement** the minimum code to pass those tests (TDD green).
3. **Refactor** while keeping all tests green.
4. **Run linting & type checks** locally (`ruff`, `mypy`).
5. **Stage and commit** atomically with sign-off and SPDX headers.
6. **Pre-commit hooks** run automatically — fix any failures and
   re-commit (do NOT reset; do NOT bypass).
7. **CI pipeline** MUST pass. No manual or exploratory testing is
   permitted until CI is green.
8. **Pull request review** MUST verify constitutional compliance,
   atomic commit structure, proper licensing headers, and agent
   co-authorship (when applicable).
9. **Manual validation** may proceed only after CI confirms all
   automated checks pass.

## Governance

- This constitution supersedes all other development practices.
  In case of conflict, this document prevails.
- Amendments MUST be documented with a version bump, rationale,
  and migration plan if existing code is affected.
- Version increments follow semantic versioning:
  - **MAJOR**: Backward-incompatible principle removals or
    redefinitions.
  - **MINOR**: New principles or materially expanded guidance.
  - **PATCH**: Clarifications, wording, or non-semantic
    refinements.
- All pull requests and code reviews MUST verify compliance with
  these principles. Non-compliance MUST block merge.
- Amendment history MUST be preserved in the Sync Impact Report
  comment at the top of this file.
- All `.specify/templates/*.md` files MUST be reviewed for
  consistency when amendments are made.
- Use `AGENTS.md` for runtime development guidance that
  supplements this constitution.

**Version**: 1.0.0 | **Ratified**: 2026-05-05 | **Last Amended**: 2026-05-05
