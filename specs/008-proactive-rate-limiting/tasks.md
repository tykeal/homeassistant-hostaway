<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Tasks: Proactive Hostaway API Rate Limiting

**Feature**: `008-proactive-rate-limiting` | **Branch**:
`008-proactive-rate-limiting` | **Issue**:
[#213](https://github.com/tykeal/homeassistant-hostaway/issues/213)
**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md),
[data-model.md](data-model.md),
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md),
[quickstart.md](quickstart.md)

## Format

`- [ ] TID [P?] [Story?] Description — files — FR/SC — Verify: ...`

- **[P]**: may run in parallel with other `[P]` tasks in the same phase
  (disjoint files, no ordering dependency).
- **[Story]**: `US1`–`US5` from [spec.md](spec.md). Setup, foundational,
  and polish tasks carry no story label.
- Every task names the files it touches, the requirements it satisfies, and
  an explicit verification step.

## Working agreements (apply to every task)

- **TDD is mandatory** (Constitution I): write the failing test first, then
  the production code. Limiter tests use the injected clock and timer hook —
  **no `asyncio.sleep`, no real waiting** (quickstart "Testing without
  waiting").
- Every task ends green: `uv run pytest tests/`,
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/`. Baseline **493 tests passing**; the count
  may only grow (SC-011). aislop CI score must stay **100**.
- Commits are atomic, signed (`git commit -s`), Capitalized Conventional
  Commits, subject ≤50 chars imperative with no trailing period, body wrapped
  at 72, with the `Co-authored-by` trailer (Constitution III, VI).
- **Updates to this file go in their own commit**, never mixed with the code
  they track.
- New files carry SPDX headers per `REUSE.toml`.
- `custom_components/hostaway/sensor/listing.py` **MUST NOT be touched**.
- **No `CHANGELOG.md`.** The repository deliberately has none.
- Never `--no-verify` (Constitution V).

## Phase map (each phase is one reviewable PR-sized unit)

| Phase | Theme | Tasks | Stories | Checkpoint |
|---|---|---|---|---|
| 1 | Setup & foundational scaffolding | T001–T005 | — | baseline captured, constants and exceptions landed |
| 2 (Plan A) | Limiter core, Home-Assistant-free | T006–T015 | US1, US3, US5 | SC-001, SC-002, SC-003, SC-007, SC-008, SC-014, SC-018 |
| 3 (Plan B) | Chokepoint integration | T016–T021 | US1, US4, US5 | SC-002, SC-009, SC-010 end-to-end |
| 4 (Plan C) | HA wiring, registry, priority contexts | T022–T026, T042 | US3 | SC-004, SC-012, FR-001, FR-005 |
| 5 (Plan D) | Coordinator shedding | T027–T030 | US2, US4 | SC-005, SC-006, SC-015, SC-016 |
| 6 (Plan E) | Options flow | T031–T033 | — | SC-017 |
| 7 (Plan F) | Diagnostics | T034–T036 | US4 | SC-013 |
| 8 (Plan G) | Documentation & regression sweep | T037–T041 | US4 | SC-008, SC-011 |

---

## Phase 1: Setup & Foundational Scaffolding

**Purpose**: Capture the baseline and land the zero-behaviour-change
primitives every later phase imports. Nothing here changes runtime behaviour.

**⚠️ Blocking**: no phase-2 work starts until T002 and T004 are green.

- [x] T001 Capture and record the green baseline: run `uv sync`,
  `uv run pytest tests/` (expect 493 passing),
  `uv run ruff check custom_components/ tests/`,
  `uv run mypy custom_components/` — no files changed — SC-011 —
  **Verify**: all three clean; note the exact test count in the PR
  description so later phases can prove the count only grew.

- [x] T002 [P] Add the documented rate-limit constants
  `RATE_LIMIT_WINDOW_SECONDS = 10.0`, `RATE_LIMIT_CEILING = 200`,
  `DEFAULT_RATE_LIMIT_BUDGET = 180`, `DEFAULT_SUPPRESSION_SECONDS = 10.0`,
  each with a docstring/comment stating that 180 is a deliberate safety
  margin and **not** a Hostaway-documented value —
  `custom_components/hostaway/api/const.py` — FR-009, FR-010, FR-015, FR-034
  — **Verify**: a unit test asserts each constant's value and that no
  window-length setter or option exists (FR-034).

- [x] T003 Remove the stale `RATE_LIMIT_PER_IP = 15` and
  `RATE_LIMIT_PER_ACCOUNT = 20` constants **in their own commit**, after
  confirming repo-wide they are unreferenced —
  `custom_components/hostaway/api/const.py` — R-016, spec "Provenance for
  corrected facts" — **Verify**:
  `rg 'RATE_LIMIT_PER_(IP|ACCOUNT)' custom_components/` returns nothing. The
  search is scoped to production code because the regression test that keeps
  the names gone necessarily mentions both of them; full suite still green. **Depends on T002.**

- [x] T004 [P] Add `HostawayRateLimitShedError(Exception)` — deliberately
  **not** a `HostawayApiError` subclass — and
  `HostawayRateLimitWaitTimeout(HostawayRateLimitError)`, both carrying
  `waited: float` — `custom_components/hostaway/api/exceptions.py`,
  `tests/api/test_exceptions.py` — FR-020, FR-023, data-model §13 —
  **Verify**: tests assert
  `not issubclass(HostawayRateLimitShedError, HostawayApiError)` and
  `issubclass(HostawayRateLimitWaitTimeout, HostawayRateLimitError)`. These
  two assertions are the structural guard for SC-005 and SC-015; they must
  never be relaxed.

- [x] T005 [P] Add the deterministic test harness: a `FakeClock` exposing
  `now`, a `call_later`-style `schedule` hook, and `advance(seconds)` that
  fires due timers in order — `tests/api/conftest.py` (or `tests/helpers.py`)
  — R-017, quickstart "Testing without waiting" — **Verify**: a self-test
  proves `advance()` fires timers in due order and that no limiter test in
  later phases contains `asyncio.sleep`
  (`rg 'asyncio.sleep' tests/api/test_rate_limit.py` returns nothing). The
  harness must also fail loudly rather than hang when a callback re-arms at
  zero delay, since a pump that wakes without making progress is exactly the
  bug it exists to expose.

**Checkpoint**: constants, exceptions, and the fake clock exist; behaviour
unchanged; suite green.

---

## Phase 2 (Plan A): Limiter Core — `api/rate_limit.py`

**Goal (US1, US3, US5)**: a pure, Home-Assistant-free limiter that paces
admissions against two sliding-window gates, orders waiters by priority with
anti-starvation aging, and honours server-issued suppression — provable
without a Home Assistant harness and without real time.

**Independent Test**: drive `acquire()` through a fake clock under a workload
demanding ≥2× the budget; assert no 10-second window ever contains more
admissions than the budget for either gate.

**Constraint**: this module MUST import nothing from `homeassistant`
(Constitution II, R-011). Add a test that asserts it.

- [x] T006 [P] [US3] Implement `RequestPriority`, `WaitPolicy`, the three
  policy constants (`INTERACTIVE_POLICY` 30.0/no-shed, `SCHEDULED_POLICY`
  2.0/shed, `FIRST_REFRESH_POLICY` 30.0/no-shed), `RequestContext` with
  `start()`/`remaining()`, the `_REQUEST_CONTEXT` contextvar, the
  `request_context()` context manager, `current_request_context()`, and the
  three `start_*_context()` helpers —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-018, FR-020, FR-021, FR-037,
  data-model §1–§3, contract §3 — **Verify**: tests assert the deadline is
  created **once** per operation and that `remaining()` shrinks across
  successive acquisitions; absent context resolves to
  `SCHEDULED` + `SCHEDULED_POLICY`; `FIRST_REFRESH_POLICY.shed_on_timeout is
  False`; the context is restored on exception and propagates into
  `asyncio.Task`s created inside it.

- [x] T007 [P] [US1] Implement `SlidingWindowGate` with a monotonic
  `deque[float]`, gate-specific `window_seconds` and `max_budget`, pruning at
  the head of `capacity_available`, `record`, and `next_available`, plus
  `reconfigure(budget)` that validates `1 <= budget <= max_budget` and
  **does not touch `_admissions`**. General gates use a 10-second window and a
  200 ceiling; endpoint gates can represent documented buckets such as
  30/minute and 400/10 seconds. `capacity_available()` and
  `next_available()` are logically read-only but may discard expired entries —
  A freshly **created** gate starts in a conservative startup hold, behaving
  as though half its effective budget were already consumed for its first
  full window, because limiter state is not persisted across a restart. The
  hold expires naturally and is **not** applied when an existing gate is
  merely reconfigured in place —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-009, FR-010, FR-011, FR-032, FR-034,
  FR-038, data-model §5 — **Verify**: a new gate admits only half its budget
  in its first window and the full budget in the next; `reconfigure()` on an
  existing gate re-arms **no** hold; tests prove sliding (not tumbling) semantics
  across a window boundary; the combined real and synthetic deques are bounded
  by `_max_budget`, not by the current budget; lowering the budget below the
  current in-window count discards **no** timestamps; server reconciliation
  can fill only `_synthetic_admissions` without making `next_available()`
  index an empty `_admissions` deque; a 30/60 gate and a 400/10 gate are
  representable; all time decisions use the injected clock (a wall-clock jump
  changes nothing).

- [x] T008 [P] [US1] Implement the `BudgetGate` protocol (all methods
  synchronous; `capacity_available` and `next_available` are logically
  read-only but may prune expired local timestamps; only `record` consumes new
  capacity) and
  `classify_request(method, path)` selecting account-general + IP-general for
  general endpoints, with a registry seam for the three documented
  endpoint-specific buckets (`POST /v1/conversations/{id}/messages` 30/min,
  `POST /v1/listings/{id}/calendar/priceDetails` 400/10s,
  `POST /v1/reservations` 200/10s) that are **not activated** —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-007, SC-014, data-model §4, contract §2
  — **Verify**: the SC-014 demonstration test registers a *prototype*
  endpoint bucket and shows a general request increments both general gates
  while the endpoint-specific request increments **only** its own bucket and
  leaves both general gates unchanged — with zero changes to `coordinator.py`,
  `services/`, or any public API method.

- [x] T009 [US1] [US3] Implement `AccountRateLimiter.__init__`,
  `async acquire(method, path)`, the `Waiter` record, the
  `(priority, sequence)` heap, and the synchronous `_pump()` admission
  decision. `_pump()` must order candidates first (honouring priority and
  FR-019 aging), classify and capacity-check each candidate's own gates, admit
  the highest-priority candidate whose complete gate set is currently
  admissible, and retain blocked candidates in the queue so an unrelated
  endpoint bucket is not head-of-line blocked. Include single-timer arming and
  conjunction `record()` across all selected gates —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-001 (admission half), FR-007, FR-019,
  SC-003, SC-008, SC-018, data-model §6–§7, contract §1 — **Verify**: tests
  assert an interactive waiter is served before a scheduled one; FIFO holds
  within a class; **SC-018** — sustained interactive load plus one scheduled
  waiter admits the scheduled waiter once aging is met; a test with an aged
  scheduled waiter whose gates differ from the next interactive waiter proves
  the pump does not classify one waiter and admit another; a regression test
  queues a blocked account-general waiter ahead of an endpoint-specific waiter
  with an independent bucket and proves the endpoint waiter is admitted while
  the blocked waiter stays queued; **SC-008** — a below-budget `acquire()`
  arms **no** timer and performs no deliberate suspension; at most one timer
  is armed at a time; no lock is held across an
  `await` (a nested token-acquire-inside-data-acquire test must not deadlock);
  there is no `release()` — admission is a rate reservation, not a pool.
  **Depends on T006, T007, T008.**

- [x] T010 [US2] [US3] Enforce the operation-wide deadline inside `acquire()`:
  queued waits consume `RequestContext.remaining(now)`, raising
  `HostawayRateLimitShedError` when a `shed_on_timeout` policy's deadline is
  exhausted and `HostawayRateLimitWaitTimeout` otherwise, both carrying the
  real `waited` seconds, with a message naming rate limiting in
  operator-readable terms —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-020, FR-021, SC-004, contract §1 —
  **Verify**: a test issues three acquisitions in one 30 s interactive context
  and proves the third sees a *reduced* remaining budget — **no acquisition
  receives a fresh 30 s or 2 s wait**; a scheduled waiter queued behind a
  10-second capacity block raises `HostawayRateLimitShedError` exactly at its
  2-second deadline, proving the shared timer wakes for waiter deadlines and
  not only for capacity or suppression expiry. `_pump()` MUST resolve expired
  waiters **before** the provider-suppression early return and before any
  capacity scan; a test asserts that a deadline elapsing while a gate is still
  blocked raises once and does **not** spin — count fake-clock timer arms and
  prove no zero-delay re-arm occurs. **Depends on T009.**

- [x] T011 [US1] Handle waiter cancellation and timeout removal: a waiter
  removed before its future resolved consumes **no** capacity; a waiter whose
  future already resolved in the same loop iteration keeps its recorded
  admission (never un-record); removal always re-pumps —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — spec edge case "Cancellation",
  data-model §6 — **Verify**: cancel a queued `acquire()` and assert
  `admitted_in_window` is unchanged and the next waiter is admitted;
  assert no `heapq` comparison ever reaches a `Future` object.
  **Depends on T009.**

- [x] T012 [US5] Implement `note_rate_limited(applied_counter, retry_at,
  method, path, limit=None, remaining=None)` with **gate-scoped** suppression:
  `account` → that account's general gate, `ip` → the shared process-wide IP
  gate, `endpoint` → the classified endpoint bucket, `provider` → shared
  process-wide provider suppression; missing/unknown values fall back to
  suppressing every gate selected for the request. Convert `retry_at` from a
  **Unix timestamp** against wall-clock time once, suppress for the **full**
  remaining delay (explicitly **not** capped by `MAX_BACKOFF`, so suppression
  never expires before the server's deadline) bounded only by
  `MAX_SUPPRESSION_SECONDS` (3600.0) as a corrupt-header safety net, and fall
  back to
  `DEFAULT_SUPPRESSION_SECONDS` (10.0). Suppression **extends, never
  shortens**, applies to interactive callers identically, clears by time
  comparison in `_pump()`, and re-pumps every limiter waiting on a changed
  shared gate or provider state. Consume `X-RateLimit-Limit` and
  `X-RateLimit-Remaining` only after validating each as a finite integer in
  range (`Limit >= 1`, `Remaining >= 0`), ignoring any malformed or
  out-of-range value entirely at debug level, then reconcile only the affected
  gate: lower the
  runtime ceiling to `min(configured_budget, limit)` without raising above the
  operator-configured budget, and add synthetic in-window admissions only when
  needed to make local available capacity no greater than the server-reported
  remaining count — `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-014, FR-015, FR-016, FR-017, SC-007,
  data-model §8, contract §1 — **Verify**: **SC-007** — inject a 429 with
  `X-RateLimit-Applied: account` and a timestamp 5 s in the future; assert no
  admission on that gate for ≥5 s, that the **IP** gate still admits, that an
  interactive waiter is *also* blocked, and that normal admission resumes with
  no reload. Assert malformed headers are ignored conservatively: `Limit` of
  `0`, negative, non-numeric, or non-finite leaves the runtime ceiling
  untouched and never violates the gate's `1 <= budget` invariant, and a
  negative `Remaining` adds **no** synthetic admissions and terminates. Add a multi-account regression test proving a `provider` 429
  observed through account A suppresses account B and that every affected
  queue is re-pumped when the provider suppression is set and when it expires.
  Also assert negative, zero, `NaN`, and `inf` timestamps are handled without
  raising and never produce a ~1.8-billion-second delay; server-reported
  larger limits or remaining counts do not raise capacity or delete local
  admissions, so feedback cannot oscillate. **Depends on T009.**

- [x] T013 [US1] Implement `configure(*, account_budget, effective_ip_budget)`
  raising `ValueError` outside `1..200` for general gates, applying the
  effective account budget (minimum across active same-account entries) to the
  account gate and the process-wide **minimum** to the shared IP gate,
  preserving all in-window admissions, re-pumping every queue on a changed
  gate, and being idempotent for unchanged values —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-006, FR-008, FR-032, SC-012, contract §1
  — **Verify**: tests assert `configure()` after recorded admissions leaves
  `admitted_in_window` unchanged, that waiters blocked under the old budget are
  re-pumped when it rises, that two limiters sharing one IP gate observe each
  other's admissions, and that two entries sharing one account key use the
  lower configured account budget rather than last-writer-wins. **Depends on
  T009.**

- [x] T014 [P] [US4] Implement `LimiterStats`, `GateSnapshot`,
  `LimiterSnapshot`, `snapshot()` (pure, prunes first), `note_shed()`, and
  per-applied-counter 429 storage —
  `custom_components/hostaway/api/rate_limit.py`,
  `tests/api/test_rate_limit.py` — FR-028, FR-029, data-model §9 —
  **Verify**: `snapshot()` exposes `account` and `ip` gates independently,
  includes `shed_total`, `rate_limited_total`, and
  `rate_limited_by_counter` broken down by applied counter, and contains
  **no** `account_key` or credential substring; `note_shed()` does not affect
  admission. **Depends on T009.**

- [x] T015 [US1] Add the deterministic over-budget simulator test: a workload
  demanding ≥2× budget driven entirely by the fake clock, with a Hostaway
  sliding-window model that returns 429 when either documented counter is
  exceeded — `tests/api/test_rate_limit.py` — **SC-001**, **SC-002**
  (limiter half), SC-003 — **Verify**: with enforcement **disabled** the
  simulated server returns ≥1 429; with enforcement **enabled** it returns
  **zero** 429s and no 10-second window on either gate exceeds the configured
  budget, measured across the whole run. **Depends on T009–T013.**

**Checkpoint (Plan Phase A)**: SC-001, SC-002 (limiter half), SC-003, SC-007,
SC-008, SC-014, SC-018 all proven without Home Assistant and without a single
`asyncio.sleep`. A test asserts `api/rate_limit.py` imports nothing from
`homeassistant`.

---

## Phase 3 (Plan B): Chokepoint Integration

**Goal (US1, US4, US5)**: every HTTP request this integration issues passes
through the limiter, by construction rather than by convention, and a 429
feeds back into it.

**Independent Test**: add a throwaway method to `HostawayApiClient` with no
limiter-specific code and show its traffic is limited (SC-009).

- [x] T016 [US1] Add an optional `limiter: AccountRateLimiter | None = None`
  constructor parameter to `HostawayApiClient` (defaulting to unlimited) and
  `await self._limiter.acquire(method, path)` **inside** the existing retry
  loop, immediately before `self._http.request(...)`, so every retry attempt
  and the recursive re-entry from `_handle_forbidden_response` re-acquires —
  `custom_components/hostaway/api/client.py`, `tests/api/test_client.py` —
  FR-001, FR-002, FR-004, FR-012, SC-009, SC-010, SC-011, contract §4 —
  **Verify**: **SC-010** — a mocked `429, 429, 429, 200` transport yields
  `snapshot().admitted_total == 4`, not 1; **SC-009** — a new client method
  added in the test with zero limiter code is still limited; all existing
  tests construct the client without a limiter and keep passing.
  **Depends on T009.**

- [x] T017 [US3] Cap retry backoff sleeps by the ambient operation deadline's
  remaining time, raising the same shed/timeout exception the next acquisition
  would raise when no time remains — `custom_components/hostaway/api/client.py`
  — FR-020, FR-021, contract §4 — **Verify**: a test in a 2 s scheduled context
  asserts the retry sleep never exceeds `ctx.remaining(now)` and that the cycle
  sheds instead of sleeping past its deadline. **Depends on T016.**

- [x] T018 [US5] Correct the rate-limit header handling: read Hostaway's
  documented `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Retry-After` (**a Unix timestamp**), and `X-RateLimit-Applied`
  on 429 responses; call `limiter.note_rate_limited(...)` in the `429` branch
  **before** the retry-or-raise decision; replace the standard `Retry-After`
  read in `api/retry.py::_parse_retry_after` and `api/auth.py:164-169` with the
  timestamp conversion, guarding negative and non-finite values. Pass `Limit`
  and `Remaining` into the limiter feedback contract so the affected gate can
  converge downward to the server's view without ever exceeding the configured
  budget —
  `custom_components/hostaway/api/client.py`,
  `custom_components/hostaway/api/retry.py`,
  `custom_components/hostaway/api/auth.py`, `tests/api/test_client.py` —
  FR-013, FR-014, FR-015 — **Verify**: tests assert `MAX_RETRIES`,
  `MAX_BACKOFF`, and the jittered backoff **curve** are unchanged (FR-013);
  a timestamp 5 s out produces a ~5 s delay, never ~1.8e9 s; a missing header
  yields the 10.0 s default; observed `Limit` and `Remaining` adjust only the
  affected gate and never raise an operator-configured budget. **Depends on
  T012, T016.**

- [x] T019 [US1] Add the second enforcement point: optional `limiter`
  parameter on `HostawayTokenManager`, `await limiter.acquire(...)` before
  `self._http.post(...)` in `_request_token` (~line 143), `note_rate_limited`
  in its own 429 branch, the post-generation delay consuming the ambient
  deadline, and an **OQ-001 assumption comment** at the module docstring and
  the acquisition site stating this is conservative, **not** documented
  Hostaway behaviour — `custom_components/hostaway/api/auth.py`,
  `tests/api/test_auth.py` — FR-001, FR-018, SC-011, contract §5 —
  **Verify**: a test proves a token fetched inside an interactive operation
  acquires at interactive priority and does not deadlock when nested inside a
  data-request acquisition; a test asserts the OQ-001 comment text exists (or a
  reviewer checklist item covers it). **Depends on T009, T016.**

- [x] T020 [P] [US4] Add observability at the chokepoint: `DEBUG` per-request
  admission logging with priority class, wait duration, and remaining budget;
  a **distinguishable** `WARNING` for a 429 received despite proactive
  limiting whose wording makes clear this was **server pushback**, not
  self-limiting — `custom_components/hostaway/api/client.py`,
  `tests/api/test_client.py` — FR-027, FR-028, contract §4 — **Verify**: tests
  with `caplog` assert the pushback WARNING is distinguishable by message from
  a proactive shed WARNING, and that nothing new is logged at default verbosity
  for a below-budget request. **Depends on T016.**

- [x] T021 [P] [US1] Add the FR-003 guard: assert the limiter never wraps,
  subclasses, replaces, or reconfigures the shared httpx client from
  `get_async_client(hass)` — `tests/api/test_client.py` — FR-003 —
  **Verify**: a test asserts the injected `AsyncClient` object identity and
  configuration are unchanged after client construction and after a limited
  request; a grep-style assertion shows no transport/event-hook mutation.

**Checkpoint (Plan Phase B)**: SC-002, SC-009, SC-010 proven end-to-end;
FR-013 verified by asserting the retry constants and backoff curve are intact.

---

## Phase 4 (Plan C): Home Assistant Wiring & Priority Contexts

**Goal (US3)**: one limiter per Hostaway account, shared across config
entries, surviving reloads, with interactive priority reaching every service
call, config-flow validation, and the setup probe without per-call-site
plumbing.

**Independent Test**: set up two config entries with the same
`CONF_CLIENT_ID`; assert exactly one `AccountRateLimiter` exists and a reload
admits no extra budget in the 10 s spanning it (SC-012).

- [x] T022 [P] Add `DATA_RATE_LIMITERS` (a **separate top-level** `hass.data`
  key, deliberately **not** under `hass.data[DOMAIN]`) and
  `CONF_RATE_LIMIT_BUDGET = "rate_limit_budget"` —
  `custom_components/hostaway/const.py` — FR-031, R-010, data-model §12 —
  **Verify**: a test asserts `async_unload_entry`'s
  `if not hass.data.get(DOMAIN)` service-unregistration check still evaluates
  falsy after the last entry unloads — the trap in quickstart §4.

- [x] T023 Build the limiter registry in `async_setup_entry`: create or reuse
  an `AccountRateLimiter` keyed by `CONF_CLIENT_ID`, compute the effective
  account budget as the minimum across active entries sharing that key, share
  the process-wide IP gate, and inject it into both `HostawayApiClient` and
  `HostawayTokenManager` — `custom_components/hostaway/__init__.py`,
  Refuse an **additional config entry for a different account key** outright
  rather than reusing the existing limiter, which would alias the second
  account onto the first account's gate and let one account's 429 suppress
  the other. Abort it in the config flow with `single_instance_allowed`, and
  fail any entry that still reaches setup with a **non-retryable**
  config-entry error, never a retryable not-ready error. Exactly one entry is
  ever installed in practice —
  `custom_components/hostaway/__init__.py`,
  `tests/test_init.py` — FR-005, FR-008, FR-039 — **Verify**: two entries with
  the same client id share **one** limiter object and the lower account
  budget; a second entry with a *different* client id is aborted by the
  config flow and, if forced through, fails setup non-retryably with no
  second persistent limiter created and no gate aliasing; the transient
  config-flow limiter of T042 remains permitted and still shares the IP gate.
  **Depends on T016, T019, T022.**

- [x] T024 Implement lifecycle: on options change/reload call
  `configure()` on the **existing** limiter (never construct a new one),
  recompute the account budget as the **minimum across active entries sharing
  the account key** and the IP gate budget as the **minimum across active
  entries** on setup/reload/unload/removal **without clearing admissions**.
  Distinguish reload from true unload/removal by preserving the reloading
  entry's previous account and IP contributions during the unload half until
  setup replaces them. Keep the registry alive through `async_unload_entry`,
  and add `async_remove_entry` dropping the account limiter only when no other
  active entry shares the key — `custom_components/hostaway/__init__.py`,
  `tests/test_init.py` — FR-005, FR-006, FR-008, SC-012 — **Verify**:
  **SC-012** — record admissions, reload the entry, and assert the number
  admissible in the 10 s spanning the reload did **not** increase; add a
  regression test with two entries where the reloading entry has the lower
  budget and the unload half does not temporarily raise the shared IP or
  same-account budget; assert transient config-flow validation limiters do
  **not** participate in either minimum; assert waiters on changed gates are
  re-pumped after a minimum change. **Depends on T013, T023.**

- [x] T025 Set the interactive `RequestContext` in exactly four structural
  places: the single service-handler binder, the two config-flow validation
  helpers, and the setup-time `test_connection()` probe —
  `custom_components/hostaway/services/__init__.py` (`_bind_handler`),
  `custom_components/hostaway/config_flow.py`,
  `custom_components/hostaway/__init__.py`, `tests/test_services.py` — FR-018,
  FR-020, SC-004 (ordering half) — **Verify**: a test proves a **new** entry
  added to `SERVICE_DEFINITIONS` is interactive with zero per-service code;
  a test proves a service call issued while a poll saturates the budget is
  admitted ahead of the poll's remaining requests. **Depends on T006, T023.**

- [x] T042 [US1] Inject limiters into config-flow validation: in both
  `_validate_credentials` and `_fetch_listings`, look up the limiter registry
  by `CONF_CLIENT_ID`; if an active config entry for that account already
  exists, reuse its shared `AccountRateLimiter`, otherwise create a transient
  validation limiter that is excluded from the shared account and IP
  budget-minimum calculations. Pass the selected limiter into both the
  `HostawayTokenManager` and `HostawayApiClient` constructed by each helper —
  `custom_components/hostaway/config_flow.py`, `tests/test_config_flow.py` —
  FR-001, FR-005, FR-006, FR-018, FR-020 — **Verify**: one test covers
  `_validate_credentials` and one covers `_fetch_listings`; each proves token
  and data requests acquire budget during config flow. Additional tests cover
  the existing-entry path reusing the shared limiter and the new-account path
  using a transient limiter that does **not** change the effective account or
  IP minima. The transient limiter MUST still share the **process-wide IP
  gate**: it is excluded from the budget *minima* only, never given a private
  IP gate, or config-flow requests would bypass admissions already consumed by
  other accounts. **Verify** this by asserting the combined IP admission
  history, not merely that the minima are unchanged — a config-flow request
  made while another account has saturated the IP gate must wait.
  **Depends on T016, T019, T022, T023, T025.**

- [x] T026 [US3] Audit every handler that catches `HostawayRateLimitError`
  and insert an earlier `except HostawayRateLimitWaitTimeout:` that re-raises
  or converts to `ServiceValidationError` **without** a service-level sleep or
  retry — specifically `custom_components/hostaway/services/custom_fields.py`
  around line 324, where the read → merge → write sequence is currently
  retried — `custom_components/hostaway/services/custom_fields.py`,
  `tests/services/`, `tests/test_services.py` — FR-020, SC-004 — **Verify**:
  the **regression test** asserts that on `HostawayRateLimitWaitTimeout` the
  service performs **zero** sleeps, **zero** re-runs of the read/merge/write
  sequence, reports **no** success, and raises an error naming rate limiting;
  a second test shows a genuine server `HostawayRateLimitError` still retries
  as before. Record the full audit result (every catch site found) in the PR
  description. **Depends on T004, T025.**

**Checkpoint (Plan Phase C)**: SC-004 ordering and operation-wide deadlines
observable; SC-012 proven; FR-005 proven with two entries and one budget.

---

## Phase 5 (Plan D): Coordinator Shedding

**Goal (US2, US4)**: a saturated scheduled cycle is skipped — visibly, without
failing, without touching the coordinator's scheduling, and never on a first
refresh.

**Independent Test**: saturate the limiter, let a scheduled refresh come due,
and assert the cycle is skipped, logged, data preserved, entities available.

- [x] T027 [US2] Add the shared coordinator base class carrying
  `_first_refresh_complete: bool`, `_shed_count`, `_shed_since_summary`,
  `_last_shed_log`, and `_SHED_LOG_COOLDOWN_SECONDS = 300.0`. Its
  `_async_update_data` opens a scheduled (or first-refresh) context, calls the
  subclass `_async_fetch_data()`, and on `HostawayRateLimitShedError`
  **returns `self.data`**, calls `limiter.note_shed()`, increments the
  per-coordinator shed counter, and logs a named record per the cooldown rule
  (WARNING first/after cooldown, then DEBUG within cooldown). The record MUST
  identify the coordinator by the fixed `listings`/`reservations`/
  `custom_fields` label, **never** by `self.name`: the generated coordinator
  names embed `entry.unique_id`, which is `CONF_CLIENT_ID`, so logging the
  generated name would leak the credential —
  `custom_components/hostaway/coordinator.py`, `tests/test_coordinator.py` —
  Keeping `HostawayRateLimitShedError` outside the `HostawayApiError`
  hierarchy is necessary but **not sufficient**: each subclass fetch path
  also ends in a broad `except Exception` (for example `coordinator.py:102`)
  that would convert the shed signal into an `UpdateFailed` anyway. The shed
  signal MUST therefore be re-raised ahead of every such handler —
  FR-021, FR-022, FR-023, FR-024, FR-025, FR-026, SC-005, SC-006, SC-016,
  data-model §10, contract §6 — **Verify**: a shed raised from inside
  `_async_fetch_data` reaches the base class and is **not** swallowed by the
  broad handler; `async_set_updated_data` appears
  **nowhere** in the shed path (`rg async_set_updated_data
  custom_components/hostaway/coordinator.py` returns nothing); a shed does not
  publish a partial dataset and does not retry immediately or accumulate
  backlog; a caplog assertion proves the emitted record contains the fixed
  label and does **not** contain `entry.data[CONF_CLIENT_ID]`.
  **Depends on T004, T010.**

- [x] T028 [US2] Adapt the listings and reservations coordinators: rename each
  existing `_async_update_data` body to `_async_fetch_data`, inherit the base,
  and use `FIRST_REFRESH_POLICY` for the first refresh so it is **never shed**
  and failure propagates `UpdateFailed` → `ConfigEntryNotReady`. Preserve the
  reservations coordinator's existing **sequential** per-listing fan-out
  (`coordinator.py:286-311`) exactly — one `await` per selected listing,
  paginating 100/page, accumulating into a local dict returned only at the end
  — `custom_components/hostaway/coordinator.py`, `tests/test_coordinator.py` —
  FR-022, FR-037, SC-015 — **Verify**: no concurrency is introduced into the
  fan-out; a mid-fan-out shed abandons the cycle leaving prior data intact
  rather than publishing partial data; first-refresh detection uses the
  explicit flag, **never** `self.data is None`. **Depends on T027.**

- [x] T029 [US2] Adapt `HostawayCustomFieldsCoordinator` for the FR-037
  asymmetry: it keeps refreshing ~1 s after setup via
  `async_refresh_retaining_stale()` and must stay non-blocking; add an
  explicit **not-yet-loaded** state distinct from a successfully loaded empty
  list (the `self.data = []` initialiser must **not** count as initialized),
  use the first-refresh policy so its first refresh is never shed, log the
  condition, and schedule **bounded retries** until real definitions load.
  Insert `except HostawayRateLimitShedError: raise` **ahead** of the existing
  bare `except Exception` tail — `custom_components/hostaway/coordinator.py`,
  `tests/test_coordinator.py` — FR-037, SC-015, quickstart traps §2 and §3 —
  **Verify**: with a saturated limiter, setup **succeeds**, the coordinator
  reports not-yet-loaded (not "loaded, empty"), and a later bounded retry
  converges once budget frees; a test proves the bare `except Exception` no
  longer swallows a shed. **Depends on T027.**

- [x] T030 [US2] [US4] Add the shedding behaviour tests —
  `tests/test_coordinator.py` — **SC-005**, **SC-006**, **SC-015**,
  **SC-016** — **Verify**: SC-005 — after a shed `coordinator.data` is
  unchanged, `last_update_success is True`, no entity is unavailable, and a
  WARNING naming the coordinator and rate limiting was logged; SC-016 —
  `update_interval` and the `_debounced_refresh` object identity are unchanged
  across the shed; SC-006 — sustained shedding emits the first WARNING
  immediately and subsequent summaries no more than 300.0 s plus one
  coordinator interval apart, with repeats demoted to DEBUG; SC-015 — the
  listings/reservations vs custom-fields asymmetry holds in 100% of trials.
  **Depends on T027, T028, T029.**

**Checkpoint (Plan Phase D)**: SC-005, SC-006, SC-015, SC-016 green.

---

## Phase 6 (Plan E): Options Flow

**Goal**: the budget is the one operator lever, defaulting to 180, in a
collapsed section, rejecting out-of-range values rather than clamping.

**Independent Test**: submit 180, 200, and 201 through the options flow and
assert accept/accept/reject-with-error.

- [x] T031 Add `rate_limit_budget` to `options.step.init` inside a
  `data_entry_flow.section` named `advanced` with `collapsed=True` —
  **never** `FlowHandler.show_advanced_options`, which is deprecated and
  unconditionally returns `True` in HA 2026.9.4. Validate `1 <= v <= 200`,
  **rejecting** out-of-range with `invalid_rate_limit_budget`; flatten the
  section back into a flat options dict; move **no** existing flat option into
  the section — `custom_components/hostaway/config_flow.py` — FR-030, FR-031,
  FR-032, FR-033, FR-034, contract §7 — **Verify**: `rg
  show_advanced_options custom_components/` returns nothing; an entry with no
  stored budget loads and reads 180 with **no** migration (FR-033); the window
  length is not exposed (FR-034). **Depends on T022.**

- [x] T032 [P] Add the four translation keys —
  `options.step.init.sections.advanced.name`,
  `...sections.advanced.data.rate_limit_budget`,
  `...sections.advanced.data_description.rate_limit_budget` (stating 180 is a
  deliberate safety margin below Hostaway's 200 ceiling, **not** a Hostaway
  value), and `options.error.invalid_rate_limit_budget` naming the 1–200 bound
  — `custom_components/hostaway/strings.json`,
  `custom_components/hostaway/translations/en.json` — FR-010, FR-031,
  Constitution VII, contract §7 — **Verify**: a test asserts `strings.json`
  and `translations/en.json` have identical key sets.

- [x] T033 Add the options-flow tests — `tests/test_config_flow.py` —
  **SC-017** — **Verify**: default is 180; 200 is accepted; **201 and above
  are rejected** with `errors["base"] == "invalid_rate_limit_budget"` and
  **nothing is stored as 200**; 0 and negatives are rejected; submitting with
  the section collapsed/unsubmitted preserves a previously stored value; the
  field appears only in the options flow, never the setup flow.
  **Depends on T031, T032.**

**Checkpoint (Plan Phase E)**: SC-017 green; existing entries load unchanged.

---

## Phase 7 (Plan F): Diagnostics

**Goal (US4)**: per-gate limiter state is downloadable, and no credential
appears in it.

**Independent Test**: download diagnostics for a configured entry and assert
the `rate_limit.gates.account` / `rate_limit.gates.ip` shape and the absence
of any credential substring.

- [x] T034 [P] [US4] Add an opaque `limiter_label` allocated when an
  `AccountRateLimiter` is created: a `secrets.token_hex(6)` value stored on the
  limiter, **not derived from the account key in any way** —
  `custom_components/hostaway/api/rate_limit.py`, `tests/api/` — FR-029,
  Constitution X, data-model §11 — **Verify**: the label is stable for the
  lifetime of the limiter, distinct per limiter, and **independent of the
  account key** — the same account key in two processes yields different
  labels, and no function maps the account key to the label. A derived digest
  is explicitly rejected: Hostaway client IDs are low-entropy numeric account
  identifiers, so a truncated hash would be an offline-testable verifier for
  the credential.

- [x] T035 [US4] Create `async_get_config_entry_diagnostics(hass, entry)`
  emitting the contract §8 payload: `limiter_label`, `budget_source`
  (`"default"` vs `"option"`), per-gate `account` and `ip` objects (budget,
  effective budget, window seconds, admitted in window, waiting interactive,
  waiting scheduled, suppressed, seconds until clear), `admitted_total`,
  `rate_limited_total`, `rate_limited_by_counter` with per-applied-counter
  counts (`account`, `ip`, `endpoint`, `provider`, `unknown`), `shed_total`,
  and `shed_by_coordinator` —
  `custom_components/hostaway/diagnostics.py` (**new file, SPDX header**) —
  FR-029 — **Verify**: no `manifest.json` change is needed; the account key is
  **never** emitted raw. `shed_by_coordinator` uses the fixed labels
  `listings`, `reservations`, `custom_fields` — **not** the generated
  coordinator names, which embed the entry `unique_id`. The entry `unique_id`
  MUST NOT appear anywhere in the payload: `config_flow.py:244` sets it from
  `CONF_CLIENT_ID`, so emitting it would leak the credential.
  **Depends on T014, T024, T027, T034.**

- [x] T036 [US4] Add the diagnostics tests —
  `tests/test_diagnostics.py` (**new file, SPDX header**) — **SC-013** —
  **Verify**: the payload contains both gate objects; a test creates
  **diverged** state (account utilization differs from IP utilization, or only
  one gate suppressed) and sees it in the payload; `json.dumps(payload)`
  contains `rate_limited_by_counter` with separate keys for applied counters
  and contains neither `entry.data[CONF_CLIENT_ID]` nor
  `entry.data[CONF_CLIENT_SECRET]` nor `entry.unique_id` nor any token.
  **Depends on T035.**

**Checkpoint (Plan Phase F)**: SC-013 green.

---

## Phase 8 (Plan G): Documentation & Regression Sweep

**Goal (US4)**: an operator can tell shed-induced staleness from an outage,
and the whole feature is proven not to have regressed anything.

- [x] T037 [P] [US4] Document the rate-limit model in plain language:
  Hostaway applies **both** an account counter and an IP counter (200/10 s
  each) to ordinary endpoint calls, this integration tracks both, the default
  180 is a deliberate ~10% safety margin and not a Hostaway value, and sharing
  an account or IP with other Hostaway clients can still cause server pushback
  the integration cannot fully predict — `README.md` — FR-035, FR-010 —
  **Verify**: markdownlint and codespell clean; no stale 15/20 figures remain
  anywhere in the repository.

- [x] T038 [P] [US4] Document the operator-facing symptom and remedy:
  stale-but-available entities can indicate shed cycles; the remedy is
  lengthening scan intervals or reducing selected listings, **not** filing a
  bug. Include the one case an operator cannot tune away — a long
  `X-RateLimit-Retry-After` suppression can consume an interactive call's
  entire 30-second deadline (plan "Spec ambiguities", item 8) — `README.md` —
  FR-036 — **Verify**: the suppression-vs-deadline interaction is stated
  explicitly; markdownlint clean.

- [x] T039 [P] Record the **OQ-001 follow-up** as a non-blocking action: ask
  <support@hostaway.com> whether `POST /v1/accessTokens` counts against the
  general account and IP buckets, **or** empirically observe live
  `X-RateLimit-*` headers on a token response. **Do not guess an answer and do
  not change the conservative assume-it-counts behaviour.** —
  `specs/008-proactive-rate-limiting/research.md` (follow-up note) and/or a
  tracking issue — spec OQ-001 — **Verify**: OQ-001 remains listed as
  **open**; no code comment or document states token counting as documented
  Hostaway behaviour.

- [x] T040 Full regression sweep — no new files — **SC-008**, **SC-011** —
  **Verify**: `uv run pytest tests/` passes with a count **≥ 493** and no
  pre-existing test modified other than by addition;
  `uv run ruff check custom_components/ tests/` and
  `uv run mypy custom_components/` clean; interrogate at 100%;
  `git diff --name-only main -- custom_components/hostaway/sensor/listing.py`
  is **empty**; no `CHANGELOG.md` exists; no config entry migration was added;
  aislop CI score is **100**; an SC-008 structural assertion shows a
  below-budget acquire arms no timer in the assembled integration.
  **Depends on all prior tasks.**

- [ ] T041 Update the traceability record: tick the two deferred items in
  `specs/008-proactive-rate-limiting/checklists/requirements.md` if the human
  re-review passes, and confirm every FR-001–FR-039 and SC-001–SC-018 maps to
  a completed task using the coverage table below —
  `specs/008-proactive-rate-limiting/checklists/requirements.md`,
  `specs/008-proactive-rate-limiting/tasks.md` — **Verify**: this update lands
  in its **own commit**, separate from any code. **Depends on T040.**

**Checkpoint (Plan Phase G)**: SC-008 and SC-011 green; feature complete and
reviewable against issue #213.

---

## Dependencies

```text
Phase 1  T001 ──▶ T002 ──▶ T003
         T004 [P]   T005 [P]
            │
Phase 2  T006 [P] T007 [P] T008 [P] ──▶ T009 ──▶ T010, T011, T012, T013, T014
                                                   └──────────┬───────────┘
                                                              ▼
                                                            T015
            │
Phase 3  T016 (needs T009) ──▶ T017, T020, T021
         T018 (needs T012, T016)
         T019 (needs T009, T016)
            │
Phase 4  T022 [P] ──▶ T023 (needs T016, T019) ──▶ T024 (needs T013)
         T025 (needs T006, T023) ──▶ T026 (needs T004)
         T042 (needs T016, T019, T022, T023, T025)
            │
Phase 5  T027 (needs T004, T010) ──▶ T028, T029 ──▶ T030
            │
Phase 6  T031 (needs T022) + T032 [P] ──▶ T033
            │
Phase 7  T034 [P] ──▶ T035 (needs T014, T024, T027) ──▶ T036
            │
Phase 8  T037 [P] T038 [P] T039 [P] ──▶ T040 ──▶ T041
```

### Parallel opportunities

- Phase 1: T002, T004, T005 (T003 must follow T002).
- Phase 2: T006, T007, T008 are independent; after T009, the tasks T010–T014
  touch separate regions and can be split across reviewers, though they share
  one file so coordinate or sequence the commits.
- Phase 3: T017, T020, T021 after T016.
- Phase 4: T042 can be reviewed after T025 without waiting for T026.
- Phase 6: T032 runs alongside T031.
- Phase 7: T034 runs alongside Phase 6.
- Phase 8: T037, T038, T039 are fully independent.

**Phases 6 and 7 are independent of Phase 5** and could be reviewed
concurrently; Phase 7's T035 needs the coordinator shed counters from T027.

---

## Requirement coverage

### Functional requirements

| FR | Task(s) |
|---|---|
| FR-001 | T016, T019, T042 |
| FR-002 | T016 |
| FR-003 | T021 |
| FR-004 | T016 |
| FR-005 | T023, T042 |
| FR-006 | T013, T024, T042 |
| FR-007 | T008, T009 |
| FR-008 | T013, T023, T024 |
| FR-009 | T002, T007 |
| FR-010 | T002, T007, T037 |
| FR-011 | T007 |
| FR-012 | T016 |
| FR-013 | T018 |
| FR-014 | T012, T018 |
| FR-015 | T002, T012, T018 |
| FR-016 | T012 |
| FR-017 | T012 |
| FR-018 | T006, T019, T025, T042 |
| FR-019 | T009 |
| FR-020 | T010, T017, T026, T042 |
| FR-021 | T010, T017, T027 |
| FR-022 | T027, T028 |
| FR-023 | T027, T029 |
| FR-024 | T027 |
| FR-025 | T027 |
| FR-026 | T027 |
| FR-027 | T020 |
| FR-028 | T014, T020 |
| FR-029 | T014, T034, T035 |
| FR-030 | T031 |
| FR-031 | T022, T031 |
| FR-032 | T007, T013, T031 |
| FR-033 | T031 |
| FR-034 | T002, T031 |
| FR-035 | T037 |
| FR-036 | T038 |
| FR-037 | T006, T028, T029 |
| FR-038 | T007, T024 |
| FR-039 | T023, T042, T022 |

### Success criteria

| SC | Task(s) |
|---|---|
| SC-001 | T015 |
| SC-002 | T015, T016 |
| SC-003 | T009, T015 |
| SC-004 | T010, T025, T026 |
| SC-005 | T027, T030 |
| SC-006 | T027, T030 |
| SC-007 | T012 |
| SC-008 | T009, T040 |
| SC-009 | T016 |
| SC-010 | T016 |
| SC-011 | T016, T019, T040 |
| SC-012 | T024 |
| SC-013 | T036 |
| SC-014 | T008 |
| SC-015 | T028, T029, T030 |
| SC-016 | T027, T030 |
| SC-017 | T033 |
| SC-018 | T009 |

**Uncovered requirements: none.** Every FR-001–FR-039 and SC-001–SC-018 maps
to at least one task.

---

## Scope guards (assert, do not assume)

- `custom_components/hostaway/sensor/listing.py` unmodified (T040).
- No `CHANGELOG.md` created (T040).
- No config entry migration (T031, T040).
- The endpoint-specific buckets
  (`POST /v1/conversations/{id}/messages`,
  `POST /v1/listings/{id}/calendar/priceDetails`,
  `POST /v1/reservations`) remain a **seam only** — the integration calls none
  of them today; T008 proves the seam with a prototype bucket and does not
  activate them.
- Limiter state is **not** persisted across Home Assistant restarts.
- The reservations fan-out stays **sequential** (T028).

---

## Owner decisions still needed

These are the only items the artifacts leave genuinely open. None blocks
starting Phase 1.

1. **OQ-001 remains open** (T039): whether `POST /v1/accessTokens` counts
   against a bucket. The conservative assume-it-counts stance stands; someone
   must own asking Hostaway support or observing live headers.
2. **FR-022 offers two behaviours and the spec does not choose.** The plan
   takes "abandon the cycle leaving prior data intact"; T028 implements that.
   Confirm the alternative (publishing last-known values per unreached
   listing, which would interact with feature 007's write-generation
   preservation) is not wanted.
3. **`checklists/requirements.md` has two items deliberately unchecked**
   pending a human re-review of the amended design. T041 ticks them only if
   that review passes — it is a human gate, not an agent one.
