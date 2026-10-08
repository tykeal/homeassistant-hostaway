<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Implementation Plan: Proactive Hostaway API Rate Limiting

**Branch**: `008-proactive-rate-limiting` | **Date**: 2026-10-08 |
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from
`specs/008-proactive-rate-limiting/spec.md`

## Summary

Add an integration-wide, proactive, account-plus-IP request budget that every
Hostaway HTTP request this integration issues must pass before it is sent,
layered beneath the existing reactive 429/5xx retry logic rather than
replacing it.

The limiter is a Home-Assistant-free module,
`custom_components/hostaway/api/rate_limit.py`, implementing a **sliding
10-second window** of monotonic admission timestamps (default budget 180,
ceiling 200), a **priority heap** that admits interactive callers before
scheduled ones, and a **suppression state** driven by observed 429s so a
server "no" outranks everything including interactive priority.

Enforcement sits at **two** points, both inside the `api/` package and neither
visible to an API-method author: `HostawayApiClient._request`, with the
acquisition *inside* the existing retry loop so each retry attempt
independently consumes budget, and `HostawayTokenManager._request_token`,
which bypasses `_request` entirely and must be covered because FR-001 names
token requests. Priority reaches both without threading a parameter through
every method by way of a `contextvars.ContextVar`, set in exactly four
structural places: the single service-handler binder, the two config-flow
validation helpers, the setup-time connectivity probe, and a new shared
coordinator base class.

Saturation behaviour differs by caller. An interactive logical operation gets
one 30-second monotonic deadline; every acquisition and retry backoff in that
operation consumes the remaining time, then raises an actionable rate-limit
error. A scheduled coordinator cycle gets one 2-second cycle-wide deadline,
then **sheds** — a dedicated exception propagates to a shared coordinator base
which `return self.data`, leaving `last_update_success` true, entities
available, and the refresh interval and debounce timer untouched. Listings and
reservations first refreshes are never shed and still fail through the
`UpdateFailed` → `ConfigEntryNotReady` path. The custom-fields first refresh
remains asynchronous and non-blocking; it records not-yet-loaded and retries
rather than treating the initial `[]` as loaded data.

The budget is operator-tunable through the options flow only, in a collapsed
"advanced" section, defaulting to 180 and **rejecting** — never clamping —
values above 200 per general counter. Limiter state includes both account-keyed
gates and a process-wide IP gate, survives a config entry reload, and is
exposed through a new diagnostics module with credential material digested
rather than emitted.

The general Hostaway model is represented as two active gates: one
per-account gate and one process-wide per-IP gate. The account gate's effective
budget is the minimum configured budget across active entries sharing the same
account key. The IP gate's effective budget is the minimum configured budget
across active entries, preserving a reloading entry's contribution until setup
replaces it, and is recomputed in place without clearing admissions. A request
classifier preserves
the seam for future endpoint-specific counters: general endpoints use the
general gates, while a documented endpoint-specific endpoint will use its own
account bucket instead of the general pool.

## Technical Context

**Documented API facts**: Hostaway documents the corrected multi-counter
model, sliding-window semantics, 429 body, and `X-RateLimit-*` headers at
<https://api.hostaway.com/documentation>, verified live on 2026-10-08.

**Language/Version**: Python 3.14 (`uv` manages the toolchain; the worktree
resolves CPython 3.14.7)

**Primary Dependencies**: Home Assistant 2026.9.4 (installed and verified
against), httpx, voluptuous. **No new runtime dependency** — the limiter uses
only `asyncio`, `contextvars`, `collections.deque`, `heapq`, and `time` from
the standard library.

**Storage**: One new optional config entry **option**
(`rate_limit_budget`). No new persisted state; limiter state is in-memory and
deliberately does not survive a Home Assistant restart (spec, Out of Scope).

**Testing**: `uv run pytest tests/` with pytest-asyncio and
pytest-homeassistant-custom-component. Baseline to preserve: **493 tests
passing**, `uv run ruff check custom_components/ tests/` clean,
`uv run mypy custom_components/` clean, interrogate at 100%.

**Target Platform**: Home Assistant custom integration (HACS), Hostaway
Public API v1.

**Project Type**: Single Python package — a Home Assistant custom component.

**Performance Goals**: Never knowingly exceed 180 admitted requests in any
rolling 10-second window per account (SC-001). Below budget, add no deliberate
delay and no deliberate suspension while capacity exists — an idle limiter
arms no timers (SC-008). The gate timestamp deques are bounded by each documented gate ceiling (200 floats across real and synthetic admissions for general gates).

**Constraints**: All I/O async; no blocking of the HA event loop. The limiter
module must import nothing from `homeassistant` (Constitution II, the
convention `api/custom_fields.py` established). The shared
`get_async_client(hass)` httpx client must not be wrapped, subclassed, or
reconfigured (FR-003). `custom_components/hostaway/sensor/listing.py` must not
be touched. The repository has **no `CHANGELOG.md`** and must not gain one.
New files require SPDX headers; `specs/**` is additionally covered by
`REUSE.toml`.

**Scale/Scope**: Three coordinators (listings 5 min, reservations 2 min,
custom fields 15 min; minimum interval 1 min), one of which fans out per
selected listing and paginates at 100 records per page — the principal burst
source. Twelve registered services, of which the custom-field write path costs
three requests per write. Roughly 8 production files touched plus 2 new ones,
and 4–6 new or extended test modules.

**Unknowns**: None blocking. OQ-001 (does the Hostaway token endpoint count
against the budget?) remains open and is handled by the conservative
assumption that it does; see "Assumptions carried, not resolved" below.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Code Quality & Testing (NON-NEGOTIABLE) | PASS | Every unit is TDD-able with an injected clock and timer hook, so the limiter's window, priority, suppression, and cancellation behaviour get failing tests first with no `asyncio.sleep`. Docstrings on every new function and class for interrogate's 100% gate; full type annotations for mypy. |
| II. API Client Design | PASS | `api/rate_limit.py` imports nothing from `homeassistant`, matching the `api/custom_fields.py` precedent. Enforcement is in the client layer, and the HA-aware registry and lifecycle stay in `__init__.py`. Constitution II already requires "rate limiting awareness"; this feature is the proactive half of it. |
| III. Atomic Commit Discipline (NON-NEGOTIABLE) | PASS | Eight increments (a foundational setup phase plus phases A–G), each a self-contained, green increment. The removal of the stale `RATE_LIMIT_PER_IP` / `RATE_LIMIT_PER_ACCOUNT` constants is its own commit. `tasks.md` updates stay separate from code commits. |
| IV. Licensing & Attribution (NON-NEGOTIABLE) | PASS | Two new Python files (`api/rate_limit.py`, `diagnostics.py`) plus new test modules get the inline SPDX header; new markdown carries the block-comment form. |
| V. Pre-Commit Integrity (NON-NEGOTIABLE) | PASS | No `--no-verify`. ruff, ruff-format, mypy, interrogate, reuse-tool, markdownlint, codespell all run as normal. |
| VI. Agent Co-Authorship & DCO (NON-NEGOTIABLE) | PASS | `git commit -s` plus the `Co-authored-by` trailer on every commit. |
| VII. UX Consistency | PASS | The budget is a standard options-flow field with translated label, description, and error, following the existing `invalid_scan_interval` pattern. No entity naming or state-attribute change. Shed cycles keep entities available rather than flipping them unavailable — a deliberate UX choice (SC-005). |
| VIII. Performance Requirements | PASS | This principle literally requires "the client MUST NOT exceed Hostaway's published rate limits"; the feature is the mechanism. Admission is O(log n) on a heap bounded by in-flight waiters, memory is bounded by each documented gate ceiling, and an idle limiter costs zero wakeups (SC-008). No blocking call is introduced. |
| IX. Phased Development | PASS | Eight increments with explicit checkpoints, documented below and to be mirrored in `tasks.md`. The foundational constants/exceptions land first, then the pure limiter lands and is proven before any HA wiring depends on it. |
| X. Security & Credential Management (NON-NEGOTIABLE) | PASS | The limiter's account key is `CONF_CLIENT_ID` — half the credential pair. It is never logged, never in `__repr__`, and diagnostics emit only a truncated SHA-256 handle. No change to token handling. |

**Gate Result**: PASS. No constitution violations; the Complexity Tracking
table is therefore empty.

**Post-design re-check**: PASS. The design adds no new dependency, keeps the
Hostaway API logic library-extractable and HA-free, preserves every existing
coordinator, service, and sensor behaviour for installations below budget, and
preserves the reactive retry constants, jitter, and backoff curve while
correcting Hostaway rate-limit header parsing.

## Project Structure

### Documentation (this feature)

```text
specs/008-proactive-rate-limiting/
├── spec.md                              # input, unchanged
├── plan.md                              # this file
├── research.md                          # Phase 0 decisions (R-001..R-017)
├── data-model.md                        # Phase 1 entities and invariants
├── quickstart.md                        # Phase 1 implementer orientation
├── contracts/
│   └── rate-limiter-interface.md        # Phase 1 internal + operator contracts
├── checklists/
│   └── requirements.md                  # existing
└── tasks.md                             # Phase 2, NOT created by /speckit.plan
```

### Source Code (repository root)

```text
custom_components/hostaway/
├── api/
│   ├── rate_limit.py          # NEW  limiter, gates, contextvar, policies (HA-free)
│   ├── const.py               # EDIT +window/ceiling/default/suppression,
│   │                          #      -RATE_LIMIT_PER_IP, -RATE_LIMIT_PER_ACCOUNT
│   ├── exceptions.py          # EDIT +HostawayRateLimitShedError,
│   │                          #      +HostawayRateLimitWaitTimeout
│   ├── client.py              # EDIT acquire inside the retry loop; 429 feedback
│   ├── auth.py                # EDIT acquire in _request_token; 429 feedback
│   ├── redaction.py           # EDIT +account_handle digest helper
│   └── retry.py               # EDIT header parsing only (FR-013); the
│                              #      backoff curve, MAX_RETRIES, MAX_BACKOFF
│                              #      and jitter are preserved unchanged
├── diagnostics.py             # NEW  async_get_config_entry_diagnostics
├── const.py                   # EDIT +CONF_RATE_LIMIT_BUDGET, +DATA_RATE_LIMITERS
├── __init__.py                # EDIT limiter registry, injection, lifecycle,
│                              #      interactive context on test_connection,
│                              #      +async_remove_entry
├── coordinator.py             # EDIT shared shed base; three coordinators adapted
├── config_flow.py             # EDIT advanced section + budget validation;
│                              #      interactive context on validation helpers
├── services/__init__.py       # EDIT interactive context in _bind_handler
├── strings.json               # EDIT section + field + error strings
├── translations/en.json       # EDIT mirror of strings.json
└── sensor/listing.py          # MUST NOT BE TOUCHED

tests/
├── api/
│   ├── test_rate_limit.py     # NEW  pure limiter, fake clock, no sleeps
│   ├── test_client.py         # EDIT per-attempt acquisition, 429 feedback
│   └── test_auth.py           # NEW  token-endpoint acquisition
├── test_coordinator.py        # EDIT shed, first-refresh, interval preservation
├── test_config_flow.py        # EDIT budget default/accept-200/reject-201
├── test_diagnostics.py        # NEW  payload shape, credential absence
├── test_init.py               # EDIT registry sharing, reload survival, removal
└── test_services.py           # EDIT interactive priority and bounded wait
```

**Structure Decision**: The existing single-package HACS layout is retained
unchanged. The only new directories are none; the two new modules sit beside
their peers. The split between `api/rate_limit.py` (pure, HA-free, unit-tested
with a fake clock) and the HA wiring in `__init__.py` follows the convention
feature 007 established with `api/custom_fields.py` and is what makes SC-001,
SC-003, SC-007, SC-010, and SC-014 testable without a Home Assistant harness.

## Key Design Decisions

Full rationale and the source verification behind each is in
[research.md](research.md); the enforceable behaviour is in
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md).

1. **Two enforcement points, not one** (R-001, R-002). `_request` covers all
   data traffic, with acquisition inside the retry loop so a retry re-acquires
   by construction. `_request_token` is a second point because it calls
   `self._http.post` directly and never passes through `_request` — FR-001
   requires token requests to be budgeted, so one point is not enough.
2. **Sliding window as a monotonic timestamp deque** (R-003). Not a token
   bucket — a bucket would drip-feed the common below-budget case and violate
   SC-008's "no deliberate delay". Not a tumbling counter — forbidden by
   FR-009.
3. **Priority carried by `contextvars`, set in four structural places**
   (R-004). Threading a `priority=` parameter through every public method,
   `_request_results`, `_mutate`, `_paginate_offset`, and the `_request`
   callables that `custom_fields.py` accepts is exactly the per-call-site
   plumbing FR-002 forbids. Wrapping `services/__init__.py::_bind_handler`
   makes all twelve services — and every future one — interactive in one line.
4. **Priority class and wait policy are orthogonal** (R-008). The spec says
   priority determines saturation behaviour and then immediately records an
   exception: the first refresh is `scheduled` but not sheddable. Two fields
   in one immutable context models that declaratively instead of as a
   conditional threaded through the limiter.
5. **A shed is a dedicated exception caught by a shared coordinator base,
   which returns `self.data`** (R-006). The exception deliberately does **not**
   subclass `HostawayApiError`, because all three coordinators contain
   `except HostawayApiError: raise UpdateFailed(...)` — inheriting would turn
   a shed into the "failed to update" SC-005 forbids. Keeping it outside the
   hierarchy makes that mistake impossible rather than merely discouraged.
   `HostawayRateLimitWaitTimeout` still inherits from `HostawayRateLimitError`,
   so service-level 429 handlers must catch it first and not retry it.
6. **First-refresh detection uses an explicit flag, never `self.data is
   None`** (R-007). `HostawayCustomFieldsCoordinator.__init__` sets
   `self.data = []`, so its data is never `None`; a `data is None` test would
   present empty definitions as successfully loaded. Custom fields intentionally
   stay non-blocking during setup, so they require a not-yet-loaded state and
   bounded retry instead of `ConfigEntryNotReady` propagation.
7. **Request classification plus `BudgetGate` selection** (R-012). General
   requests select account-general plus IP-general gates. A future
   endpoint-specific request selects its endpoint bucket instead, matching
   Hostaway's documented rule that those endpoints do not use the general
   pool. SC-014 proves bucket selection, not additive throttling.
8. **Limiter registry under its own `hass.data` key, torn down only on entry
   removal** (R-010). It must not live under `hass.data[DOMAIN]`, because
   `async_unload_entry` tests `if not hass.data.get(DOMAIN)` to decide whether
   to unregister services — a non-entry key there would make that test
   permanently truthy. And it must survive unload, because a reload is
   unload-then-setup and destroying it would reset the window, which FR-008
   and SC-012 forbid.
9. **The advanced gate is a collapsed form `section`, not
   `show_advanced_options`** (R-013). Verified against installed HA 2026.9.4:
   `show_advanced_options` is deprecated, breaks in 2027.6, and its body is an
   unconditional `return True`, so it hides nothing. This is an owner decision encoded in the spec.
10. **Diagnostics digest the account key** (R-015, data-model §11). The
    account key *is* `CONF_CLIENT_ID`. FR-029 forbids credentials in
    diagnostics, so only a truncated SHA-256 handle is emitted. The config
    entry `unique_id` is **not** a safe correlation field:
    `config_flow.py:244` assigns it from `self._client_id`, so emitting it
    would disclose the credential verbatim. Coordinator keys use fixed
    labels instead of the generated names that embed it.

## Phased Implementation

Each phase ends green: `uv run pytest tests/`, `uv run ruff check
custom_components/ tests/`, and `uv run mypy custom_components/` all clean,
with the test count only ever increasing (SC-011). TDD within every phase is
non-negotiable (Constitution I).

### Foundational Phase 1 — Setup scaffolding

Capture the green baseline, add the corrected constants and dedicated
rate-limit exceptions, remove the stale 15/20 constants in their own commit,
and land the deterministic fake-clock harness. This phase changes no runtime
behaviour but gives phases A–G stable imports and test tooling.

*Checkpoint*: baseline recorded; constants, exceptions, and fake clock exist;
full suite, ruff, and mypy remain clean.

### Phase A — Limiter core (no integration)

New `api/rate_limit.py` using the foundational constants, exceptions, and
fake clock from Phase 1. Pure unit tests with an injected clock and timer
hook.

*Checkpoint*: SC-001, SC-003, SC-007, SC-010 (limiter half), SC-014, SC-018,
plus cancellation and monotonic-clock edge cases provable without Home
Assistant and without a single `asyncio.sleep`.

### Phase B — Chokepoint integration

Optional `limiter` parameter on `HostawayApiClient` and
`HostawayTokenManager`, defaulting to `None` (unlimited) so all 493 existing
tests keep constructing them unchanged. Acquisition inside the retry loop;
acquisition in `_request_token`; `note_rate_limited` on both 429 branches;
DEBUG admission logging and a distinguishable WARNING for server-pushback 429s.

*Checkpoint*: SC-002, SC-009, SC-010 end-to-end. FR-013 verified by asserting
the retry constants and backoff curve are untouched.

### Phase C — Home Assistant wiring and priority contexts

Registry under `DATA_RATE_LIMITERS`; creation, sharing by account key,
in-place `configure()` on reload, new `async_remove_entry` teardown. Interactive
context in `_bind_handler`, in the two config-flow validation helpers, and
around the setup-time `test_connection()`. The config-flow helpers also inject
a limiter into the token manager and API client they construct, reusing an
existing account limiter when one is loaded and otherwise using a transient
validation limiter excluded from shared budget minima. Retry sleeps are capped
by the ambient operation deadline rather than receiving independent waits.

*Checkpoint*: SC-012 (reload admits no extra budget), FR-005 (two entries,
one account, one budget), SC-004 priority ordering and operation-wide deadlines observable.

### Phase D — Coordinator shedding

Shared base class carrying `_first_refresh_complete`, shed counters, and the
cooldown log state. The listings and reservations coordinators' current
`_async_update_data` bodies become `_async_fetch_data`. The custom-fields
coordinator keeps setup non-blocking, adds not-yet-loaded state, and retries
with the first-refresh policy until real definitions load. The explicit
`except HostawayRateLimitShedError: raise` ahead of
`HostawayCustomFieldsCoordinator`'s bare `except Exception` lands here.

*Checkpoint*: SC-005, SC-006, SC-015, SC-016.

### Phase E — Options flow

`CONF_RATE_LIMIT_BUDGET`, the collapsed `advanced` section, flattening back to
flat options, reject-don't-clamp validation, and the four new translation keys
in both `strings.json` and `translations/en.json`.

*Checkpoint*: SC-017. Also assert an entry with no stored budget loads and
reads 180 (FR-033), and that submitting the form with the section collapsed
preserves a previously stored value.

### Phase F — Diagnostics

New `diagnostics.py` plus the `redaction.py` digest helper.

*Checkpoint*: SC-013, including per-gate account/IP state and the assertion that neither
`client_id` nor `client_secret` appears anywhere in the serialized payload.

### Phase G — Documentation and regression sweep

README sections for FR-035 (documented account and IP counters,
sharing consequences, and server-pushback limits, in plain language) and FR-036 (stale-but-available entities
indicate shed cycles; the remedy is longer intervals or fewer listings, not a
bug report). Note the one case an operator cannot tune away: a long
`X-RateLimit-Retry-After` suppression can exhaust an interactive call's 30-second bound.

*Checkpoint*: SC-008, SC-011. Full suite green, ruff clean, mypy clean,
interrogate 100%, `sensor/listing.py` untouched, no `CHANGELOG.md`.

## Assumptions carried, not resolved

These are recorded as assumptions in code comments and documentation. None may
be written as documented Hostaway behaviour.

| Assumption | Status | Consequence if wrong |
|---|---|---|
| The token endpoint counts against the general account and IP budgets (**OQ-001, open**) | Undocumented. The conservative assumption that it *does* stands. | Only cost is slightly more headroom than needed. Relaxing it is removing one acquisition — a one-line, test-covered change. Does not block implementation. |
| Hostaway uses a sliding window | Documented by Hostaway | Client uses the same documented model plus 10% headroom (FR-009, FR-010). |
| `CONF_CLIENT_ID` uniquely identifies a Hostaway account | From the spec | Two entries for one account would get two budgets |
| Per-IP limiting is in scope | Owner decision after corrected Hostaway documentation | The limiter tracks account and IP gates separately; multiple accounts share the IP gate. |

## Owner decisions now encoded

The earlier planning pass listed these as needing confirmation. The owner has
now decided them, so they are fixed inputs rather than open questions.

1. `DEFAULT_SUPPRESSION_SECONDS = 10.0`: a 429 without a usable
   `X-RateLimit-Retry-After` Unix timestamp suppresses for one full documented
   general window.
2. `_SHED_LOG_COOLDOWN_SECONDS = 300.0`: sustained shedding emits periodic
   warning summaries roughly every five minutes.
3. The budget option uses a collapsed `data_entry_flow.section`, not
   `FlowHandler.show_advanced_options`, because the latter is deprecated and
   unconditionally true in Home Assistant 2026.9.4.
4. `RATE_LIMIT_PER_IP = 15` and `RATE_LIMIT_PER_ACCOUNT = 20` are stale values
   from Hostaway's pre-2026-08-20 documentation and must be replaced by the
   corrected constants in the implementation phase.
5. `async_remove_entry` is added so limiter state survives reloads but can be
   torn down on true entry removal.
6. `diagnostics.py` is added because FR-029 requires diagnostics and the
   integration has no diagnostics surface today.
7. The process-wide IP budget is the minimum configured budget across active
   config entries. A reload preserves the reloading entry's contribution
   during unload until setup replaces it, so reload cannot raise capacity.
   Transient config-flow limiters do not participate.
8. The per-account budget is likewise the minimum configured budget across
   active entries sharing the account key, recomputed in place without
   clearing admissions; transient config-flow limiters do not participate.
9. Scheduled fairness uses aging: 1.0 second or 20 consecutive interactive
   admissions before the oldest scheduled waiter must be served.

## Spec ambiguities and contradictions found

Raised here rather than silently resolved.

1. **FR-002 now names two HTTP chokepoints.** The corrected spec aligns with
   the source finding that `HostawayTokenManager._request_token` bypasses
   `HostawayApiClient._request`, so both enforcement points are intentional.
2. **Advanced-mode-only was superseded by a collapsed section.** FR-031 now
   requires a collapsed options-flow section because Home Assistant's
   advanced-options gate is deprecated and unconditionally true.
3. **FR-029 implies a diagnostics surface that does not exist.** "MUST appear
   in the integration's diagnostics output" presupposes diagnostics. There are
   none. See item 6.
4. **FR-029's "MUST NOT include credentials" is in direct tension with FR-005's
   keying.** The budget key is `CONF_CLIENT_ID`, which is credential material.
   Resolved by emitting a truncated SHA-256 handle, but the spec does not
   anticipate the conflict.
5. **SC-002 before/after comparison is now simulated.** The spec no longer
   depends on a shared live Hostaway environment. Tests use a deterministic
   simulator containing only integration-generated traffic, first with limiter
   enforcement disabled and then enabled.
6. **SC-008 is now structural.** A wall-clock microbenchmark would be flaky in
   CI, so the spec requires that below-budget `acquire()` resolves without
   yielding to a timer and arms no `call_later`.
7. **FR-022's two permitted behaviours are not equivalent, and the spec does
   not choose.** "Abandon the cycle leaving prior data intact" *or* "publish a
   result preserving last known values for listings it could not reach". The
   plan takes the first — it is simpler, is what returning `self.data`
   naturally produces, and composes cleanly with the FR-023 shed mechanism.
   The second would additionally interact with feature 007's write-generation
   preservation logic in the reservations coordinator, which is non-trivial.
8. **The interaction between a long `X-RateLimit-Retry-After` and the 30-second
   operation deadline must be documented.** A `X-RateLimit-Retry-After`
   timestamp 30 seconds in the future produces a
   30-second suppression, which consumes the entire FR-020 budget, so an
   interactive call arriving at the start of it will time out. FR-016 makes
   this correct by construction, but it is the one failure an operator cannot
   fix by tuning intervals, and FR-036's documentation requirement does not
   mention it. The plan documents it in Phase G.

## Complexity Tracking

No constitution violations. Table intentionally empty.
