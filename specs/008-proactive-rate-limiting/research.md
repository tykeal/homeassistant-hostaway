<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Phase 0 Research: Proactive Hostaway API Rate Limiting

**Feature**: `008-proactive-rate-limiting` | **Date**: 2026-10-08 |
**Spec**: [spec.md](spec.md)

This document records the decisions taken to resolve every Technical Context
unknown in [plan.md](plan.md), plus the codebase and Home Assistant source
verification that each decision rests on. Nothing here re-opens a resolved
clarification from the spec; where an earlier planning decision went beyond the first draft, that is
stated explicitly and now tied back to the owner decisions encoded in
[plan.md](plan.md).

Verification environment: Home Assistant `2026.9.4`, Python 3.14.7, as
installed by `uv sync` in this worktree.

---

## R-001: Where the limiter is enforced

**Decision**: Acquire budget inside `HostawayApiClient._request`, immediately
before `await self._http.request(...)`, *inside* the existing
`for attempt in range(MAX_RETRIES + 1)` loop.

**Rationale**:

- Every data request already funnels through `_request`
  (`custom_components/hostaway/api/client.py`). Verified by reading the
  module: `_request_results`, `_mutate`, `_paginate_offset`,
  `read_listing_with_custom_fields`, and `read_reservation_with_custom_fields`
  all reach the network only through `_request`. No API method calls
  `self._http` directly. This satisfies FR-002 and SC-009 structurally: a new
  method added to `HostawayApiClient` is limited by construction.
- Placing acquisition *inside* the retry loop means each `continue` — the 429
  branch, the 5xx branch, and the `httpx.RequestError` branch — re-enters the
  acquisition on its next iteration. That satisfies FR-012 and SC-010 with no
  extra bookkeeping: a 3-retry sequence performs four acquisitions.
- `_handle_forbidden_response` re-enters by calling `self._request(...)`
  recursively, so the token-refresh retry also re-acquires. Verified by
  reading the method.

**Alternatives considered**:

- *Wrap `httpx.AsyncClient`* — rejected outright by FR-003. The client comes
  from `homeassistant.helpers.httpx_client.get_async_client(hass)` and is
  shared process-wide with every other integration. A custom transport or a
  subclass would throttle unrelated traffic.
- *A decorator on each public API method* — rejected. It is opt-in by
  convention, which FR-002 forbids, and it would acquire once for a call that
  internally paginates into many requests.
- *Acquire outside the retry loop (once per `_request` call)* — rejected. It
  directly violates FR-012 and would let a retry storm triple real traffic
  while the limiter reported compliance.

---

## R-002: The token endpoint is a second enforcement point

**Decision**: `HostawayTokenManager._request_token` must also acquire budget.
The token manager takes the same limiter object by constructor injection.

**Rationale**:

- Verified by reading `custom_components/hostaway/api/auth.py`:
  `_request_token` calls `self._http.post(self._token_url, ...)` directly. It
  does **not** pass through `HostawayApiClient._request`. FR-001 explicitly
  lists "authentication/token requests" as in scope, so `_request` alone is
  not sufficient coverage.
- `_request` calls `await self._token_manager.get_token()` *before* acquiring
  its own budget for the data request. Acquisition is therefore strictly
  sequential — a token acquisition completes and releases before the data
  acquisition starts. The limiter holds no lock across an `await` that could
  re-enter it, so there is no deadlock. This ordering is a design constraint
  and is recorded as such in [contracts](contracts/rate-limiter-interface.md).
- The token request inherits the ambient priority context (R-004), so a token
  fetched on behalf of an interactive service call is itself interactive.

**Spec correction applied**: the original draft called
`HostawayApiClient._request` the single chokepoint, but source verification
showed token requests bypass it. The corrected spec now names both
chokepoints explicitly: `_request` for data traffic and `_request_token` for
token traffic while OQ-001 remains open.

**Assumption carried forward, not verified (OQ-001)**: whether Hostaway counts
token requests against the general account and IP budgets is undocumented and unverified. The
conservative assumption that it does is retained. Code comments and
documentation must describe this as an assumption, never as Hostaway
behaviour. If it is ever disproved, the only change required is removing the
acquisition from `_request_token` — a one-line, test-covered change.

---

## R-003: Sliding window algorithm

**Decision**: Monotonic timestamp deques. `collections.deque[float]` holds one
`time.monotonic()` value per real admission, and a second deque holds synthetic
admissions created only from downward server feedback. Before each admission
decision, pop from the left of **both** deques while
`now - deque[0] >= WINDOW_SECONDS` (10.0). Capacity is
`budget - len(real) - len(synthetic)`. The earliest moment capacity frees is
the earliest non-empty head across both deques plus `WINDOW_SECONDS`.

**Rationale**:

- FR-009 mandates a true sliding window and forbids a fixed/tumbling one. A
  deque of admission timestamps is the exact sliding window; it cannot
  double-admit across a boundary the way a tumbling counter can.
- FR-011 mandates a monotonic time source. `time.monotonic()` is immune to
  system clock adjustment, unlike `time.time()` or `dt_util.utcnow()`.
- Bounded memory: the real and synthetic deques together never exceed the
  gate's documented ceiling (`_max_budget`, max 200 for general gates). The
  bound cannot be the current budget, because lowering the budget below the
  retained in-window count must not discard timestamps.
- No polling. Rather than a `while True: await asyncio.sleep(...)` loop, the limiter arms a
  single `loop.call_later(...)` timer when waiters are pending, using the
  earliest required wake-up across gate readiness, suppression expiry, and
  waiter deadlines. This is O(1) wakeups and adds no idle event-loop cost,
  which SC-008 requires.

**Alternatives considered**:

- *Token bucket with continuous refill* — rejected. It smooths bursts into a
  steady 18 req/s drip, which would make the common small-portfolio case
  slower than it is today and so violate SC-008's "introduces no deliberate
  delay". A deque window admits the full burst instantly when there is room.
- *Fixed/tumbling counter reset every 10s* — forbidden by FR-009.
- *`asyncio.Semaphore` with timed release tasks* — rejected. It needs one
  `asyncio.Task` or timer handle per admission (up to 200 live timers), and
  `Semaphore` is strictly FIFO so it cannot express the FR-019 priority
  ordering.

---

## R-004: How priority reaches `_request` without changing call sites

**Decision**: A `contextvars.ContextVar` holding a small immutable
`RequestContext` (priority class + shed-exempt flag), with a context-manager
helper. Default value is `scheduled`, sheddable.

**Rationale**:

- FR-002 and SC-009 require that adding a new API method needs no
  limiter-specific code. Threading a `priority=` keyword through every public
  method, `_request_results`, `_mutate`, `_paginate_offset`, and the two
  `custom_fields` read helpers would be exactly the per-call-site plumbing
  FR-002 forbids, and would also force a signature change on
  `fetch_custom_field_definitions(self.api_client._request)` and the other
  callables that take `_request` as a parameter.
- `asyncio.Task` copies the current `contextvars.Context` at creation, so a
  context established in a service handler propagates into any task it spawns,
  and a coordinator's context propagates through its sequential per-listing
  fan-out.
- Defaulting to `scheduled` is the safe default: a forgotten call site gets
  the more conservative treatment (budget-respecting, sheddable) rather than
  jumping the queue.

**Call sites that set the context** — four, all structural rather than
per-method:

| Location | Context set | Covers |
|---|---|---|
| `services/__init__.py::_bind_handler` | `interactive` | all 12 registered services, and every future one, in one place |
| `config_flow.py::_validate_credentials` and `_fetch_listings` | `interactive` | config-flow validation (FR-018) |
| `__init__.py::async_setup_entry` around `test_connection()` | `interactive` | setup-time connectivity probe |
| `coordinator.py` shared base `_async_update_data` | `scheduled`, shed-exempt on first refresh | all three coordinators and their paginated continuations |

`_bind_handler` is the single wrapper that Home Assistant invokes for every
service; verified by reading `services/__init__.py`. Wrapping there means a
new service added to `SERVICE_DEFINITIONS` is interactive automatically.

**Alternatives considered**:

- *Explicit `priority` parameter on every API method* — rejected, see above.
- *Separate `HostawayApiClient` instances per priority* — rejected. It would
  double the object graph, confuse the token manager's cache, and still
  require every call site to pick the right client.
- *Infer priority from the call stack* — rejected as fragile and untestable.

---

## R-005: Priority queue and the admission pump

**Decision**: A `heapq` of waiters normally ordered by
`(priority_rank, sequence)`, drained by a synchronous `_pump()` callback, with
a concrete aging override for scheduled traffic. `priority_rank` is `0` for
interactive and `1` for scheduled; `sequence` is a monotonically increasing
integer giving FIFO fairness within a class. Before admitting another
interactive waiter, `_pump()` checks the oldest scheduled waiter. If that
scheduled waiter is at least **1.0 second** old, or if **20** interactive
admissions have occurred while it was queued, the ordered scan boosts that
scheduled waiter ahead of newer interactive work when its gates have capacity.
Blocked waiters remain queued, but do not prevent `_pump()` from admitting the
highest-priority later waiter whose independent gate set is currently
admissible.

**Rationale**:

- `asyncio.Lock`, `asyncio.Semaphore`, and `asyncio.Queue` are all strictly
  FIFO and cannot satisfy FR-019. Strict priority alone is also insufficient:
  under a continuous stream of independent interactive calls, scheduled
  waiters would repeatedly hit the 2-second grace and shed forever.
- `asyncio.PriorityQueue` would work for ordering but needs a dedicated
  consumer task per limiter, which must be created, owned, and shut down. A
  synchronous `_pump()` driven by (a) new arrivals and (b) one shared
  `call_later` timer armed at the earliest pending wake-up — gate readiness,
  suppression expiry, or a queued waiter's deadline — needs no background task
  at all and is trivially testable with a fake clock.
- The 1.0-second aging threshold is half of the scheduled 2.0-second grace,
  leaving room to admit the aged scheduled request before it sheds. The
  20-admission cap is a backstop for below-budget bursts where time has not
  advanced much under a fake or fast clock. Both are tiny compared with the
  actual coordinator intervals: reservations 2 min, listings 5 min, custom
  fields 15 min, and the global minimum interval 1 min.
- Admission is *reservation-only*: `_pump()` appends `now` to the window deque
  and resolves the waiter's `Future`. There is no matching "release" — the
  budget is a rate, not a pool of concurrent slots, so a request does not give
  its slot back when it completes. This is what makes the limiter correct for
  long-running requests.

**Cancellation (spec edge case "Cancellation")**: a waiter that is cancelled
or times out *before* its future resolves has not consumed window capacity, so
nothing is leaked; the limiter only removes it from the heap and re-pumps. The
race where `_pump()` resolves a future in the same event-loop iteration that
the waiter is cancelled is handled by checking `future.done()` before
resolving, and by having the waiter, on `CancelledError`, check whether its
future was already resolved — if it was, the admission is already recorded and
the limiter must *not* un-record it (an un-record would over-admit). That
conservative choice can waste at most one slot per cancelled call.

---

## R-006: Expressing a shed

**Decision**: The limiter raises a dedicated `HostawayRateLimitShedError` when
a *scheduled, sheddable* acquisition exceeds its ~2 s grace. A shared
coordinator base class catches it in `_async_update_data` and `return
self.data`.

**Verified against installed Home Assistant 2026.9.4**
(`homeassistant/helpers/update_coordinator.py`):

- `DataUpdateCoordinator.__init__` contains
  `self.data: _DataT = None  # type: ignore[assignment]`. Confirms `data`
  starts as `None`, which is the basis of FR-037.
- `async_set_updated_data` begins with `self._async_unsub_refresh()` and
  `self._debounced_refresh.async_cancel()` before assigning `self.data`.
  Confirms FR-023's prohibition: using it to shed would cancel and reschedule
  the refresh timer, breaking SC-016.
- `async_config_entry_first_refresh` delegates to
  `_async_config_entry_first_refresh` under the debounce lock and is
  documented to "automatically raise ConfigEntryNotReady if the refresh
  fails". Confirms the FR-037 fallback path.

**Critical consequence — `HostawayRateLimitShedError` must NOT subclass
`HostawayApiError`.** All three existing `_async_update_data` bodies contain
`except HostawayApiError as exc: raise UpdateFailed(...)`. If the shed
exception were part of that hierarchy, the coordinator's own handler would
convert a shed into a *failure* before the shed wrapper ever saw it —
producing exactly the "failed to update" that SC-005 forbids. Making it a
plain `Exception` subclass makes that mistake impossible rather than merely
discouraged.

**Second critical consequence —
`HostawayCustomFieldsCoordinator._async_update_data` has a bare `except
Exception as exc: raise UpdateFailed(...)` tail.** That would swallow the shed
error from inside. The refactor must add an explicit
`except HostawayRateLimitShedError: raise` ahead of it. This is called out as
its own task because it is a silent-correctness trap.

**Alternatives considered**:

- *Return `self.data` by having the limiter return a sentinel instead of
  raising* — rejected. The acquisition happens many frames below
  `_async_update_data`, inside `_request`; a sentinel would have to be checked
  and propagated by every intermediate frame.
- *`UpdateFailed` with a special message sniffed by the caller* — rejected;
  `UpdateFailed` already sets `last_update_success = False`, which SC-005
  forbids.

---

## R-007: Detecting a coordinator's first refresh (FR-037)

**Decision**: An explicit `self._first_refresh_complete: bool` flag on the
shared coordinator base, set to `True` only after `_async_fetch_data` returns
successfully. Shed-exemption is `not self._first_refresh_complete`. Listings
and reservations keep the absolute setup guarantee because `async_setup_entry`
awaits their `async_config_entry_first_refresh()` calls. The custom-fields
coordinator intentionally remains asynchronous and non-blocking during setup;
it uses the same first-refresh wait policy, but timeout/failure leaves a
not-yet-loaded state and a bounded retry path instead of failing the whole
config entry.

**Rationale**: `self.data is None` is **not** a reliable first-refresh test in
this codebase. `HostawayCustomFieldsCoordinator.__init__` ends with
`self.data = []` (verified by reading `coordinator.py`), so its `data` is never
`None`. A `data is None` check would classify its first refresh as sheddable,
and shedding it would return `[]` — publishing an empty definition set, which
FR-037 explicitly forbids ("MUST NOT return an empty or partial dataset in
place of real data"). The listings coordinator has the same hazard in a
different shape: an empty-but-legitimate `{}` is indistinguishable from
"never fetched".

An explicit flag is also what SC-015 can assert against directly.

**First-refresh acquisition policy**: priority class stays `scheduled`
(FR-019 ordering is unchanged, per the spec's Key Entities note), but the wait
policy becomes the bounded 30 s wait instead of the 2 s grace, and the
on-timeout error is `HostawayRateLimitWaitTimeout` rather than the shed error.
Because that error *does* belong to the `HostawayApiError` hierarchy, the
listings and reservations coordinators' existing `except HostawayApiError`
converts it to `UpdateFailed`, which `async_config_entry_first_refresh`
converts to `ConfigEntryNotReady` — exactly the retryable setup failure FR-037
and SC-015 ask for on those two blocking setup coordinators. The custom-fields
coordinator cannot rely on that propagation because setup has already
succeeded before its delayed `async_refresh_retaining_stale()` runs; it must
track not-yet-loaded separately and retry without presenting `[]` as loaded
data.

---

## R-008: Separating priority class from wait policy

**Decision**: Two orthogonal concepts.

| Concept | Values | Governs |
|---|---|---|
| Priority class | `interactive`, `scheduled` | admission **ordering** (FR-019) |
| Wait policy | timeout + on-timeout error | **saturation behaviour** (FR-020, FR-021, FR-037) |

Derived combinations, the only three that exist:

| Caller | Class | Timeout | On timeout |
|---|---|---|---|
| service call, config flow, setup probe | interactive | 30 s | `HostawayRateLimitWaitTimeout` |
| coordinator refresh, 2nd and later | scheduled | 2 s | `HostawayRateLimitShedError` |
| listings/reservations first refresh | scheduled | 30 s | `HostawayRateLimitWaitTimeout` → `ConfigEntryNotReady` |
| custom-fields first refresh | scheduled | 30 s | not-yet-loaded state + bounded retry |

**Rationale**: the spec's Key Entities section says priority class "determines
both admission ordering and saturation behaviour", and then immediately
records an exception — the first refresh is `scheduled` but not sheddable.
That exception is precisely a case where the two concepts diverge, so modelling
them as one enum with a special case would encode the exception as a
conditional sprinkled through the limiter. Two fields in one immutable
`RequestContext` keeps it declarative.

---

## R-009: 429 feedback and suppression

**Decision**: `_request`'s existing 429 branch calls
`limiter.note_rate_limited(applied_counter, retry_at, method, path, limit,
remaining)` before
deciding whether to retry or raise. `retry_at` comes from Hostaway's
`X-RateLimit-Retry-After` Unix timestamp. The implementation converts it to a
delay relative to current wall-clock time, clamps that delay with
`MAX_BACKOFF`, and suppresses the gate scope identified by
`X-RateLimit-Applied`: account suppresses the account general gate, IP
suppresses the shared process-wide IP gate, endpoint suppresses the classified
endpoint bucket, and provider suppresses all Hostaway traffic from this
integration through shared process-wide provider state. Missing or unusable
header data falls back conservatively to all gates selected for that request
for 10.0 seconds. `X-RateLimit-Limit` and `X-RateLimit-Remaining` reconcile
only the affected gate: `Limit` can lower that gate's runtime ceiling to
`min(configured_budget, limit)` but can never raise it above the
operator-configured value, and `Remaining` can add synthetic in-window
admissions to reduce local availability but can never remove local admissions.
Those one-way adjustments converge downward and avoid oscillation. `_pump()`
admits nobody for a suppressed selected gate and arms a `call_later` at the
expiry instant.

**Rationale**:

- FR-014 through FR-017. Reusing `MAX_BACKOFF = 30.0` as the suppression
  ceiling (FR-015) keeps the proactive and reactive layers bounded by the same
  constant, so they cannot disagree about how long a server "no" lasts.
- Suppression is checked in `_pump()`, which is the single admission decision
  point, so it applies to interactive waiters too — FR-016 — without a special
  case. Gate-scoped suppression is necessary because the process-wide IP gate
  is shared across accounts, while account and endpoint suppression must not
  unnecessarily block unrelated accounts.
- `note_rate_limited` must also be called from
  `HostawayTokenManager._request_token`'s 429 branch, which raises
  `HostawayRateLimitError` directly. Verified by reading `auth.py`.
- Clearing is implicit: `_pump()` compares `monotonic()` against each selected
  gate's suppression deadline and the shared provider-global deadline, so
  suppression expires with no reload and no restart (FR-017, SC-007). A
  changed shared gate or provider suppression re-pumps all limiters waiting on
  that state.

**`DEFAULT_SUPPRESSION_SECONDS` is fixed at 10.0 seconds.** This owner
decision equals one full documented general window — the shortest interval
after which the integration's own in-window accounting is certainly empty. It
is below `MAX_BACKOFF` and below the 30 s interactive bound, so an interactive
call can survive exactly one no-header suppression.

**Known interaction, by design**: a `X-RateLimit-Retry-After` timestamp 30
seconds in the future produces a 30 s suppression, which consumes the entire
FR-020 interactive budget, so a service
call that arrives at the start of such a suppression will time out. FR-016 is
explicit that the server's "no" outranks interactive priority, so this is
correct behaviour, not a defect — but it should be in the user documentation
(FR-036) because it is the one case where a service call fails for a reason
the operator cannot fix by tuning intervals.

**Composition with the retry layer**: the retry layer sleeps its jittered or
converted `X-RateLimit-Retry-After` delay, then loops, then re-acquires — and the re-acquisition may
block again on suppression that is still running. The two waits overlap rather
than add, because suppression is measured from the moment the 429 was observed
and the retry sleep starts at the same moment. FR-013 is satisfied: no retry
constant or backoff curve changes; only Hostaway header parsing is corrected.

**Service retry hazard**: `HostawayRateLimitWaitTimeout` inherits
`HostawayRateLimitError`, while the custom-field write path has a specific
`except HostawayRateLimitError` branch that sleeps and retries the whole
read/merge/write sequence for server 429s. The implementation must add an
earlier `except HostawayRateLimitWaitTimeout: raise` (or direct conversion to
`ServiceValidationError`) before that branch, and audit any other specific
`HostawayRateLimitError` handlers, so a local queue timeout is not mistaken for
a server 429.

---

## R-010: Account keying, lifetime, and where the limiter lives

**Decision**: A registry `dict[str, AccountRateLimiter]` keyed by
`entry.data[CONF_CLIENT_ID]`, plus one shared process-wide IP gate, stored
under its **own** `hass.data` key (`DATA_RATE_LIMITERS =
f"{DOMAIN}_rate_limiters"`), created lazily, and torn down only in a new
`async_remove_entry` hook. The effective account budget is recomputed in
place as the minimum configured budget across active entries sharing the
account key. The effective IP budget is recomputed in place as the minimum
configured budget across active config entries. During a reload, the unloading
entry is marked reloading and its prior contribution remains active until
replacement setup publishes the new contribution, so the unload half cannot
transiently raise either minimum. Config-flow validation limiters do not
participate in either minimum.

**Rationale**:

- FR-005 requires keying on the Hostaway account credential, not the entry id.
- **The registry must not live under `hass.data[DOMAIN]`.** Verified by
  reading `__init__.py`: `async_unload_entry` ends with
  `if not hass.data.get(DOMAIN): async_unregister_services(hass)`. Today
  `hass.data[DOMAIN]` contains only entry-id keys, so that test is "no entries
  left". A non-entry key added to that dict would make it permanently truthy
  and services would never be unregistered. A separate top-level key avoids
  the bug entirely; it also keeps `coordinator.py::_write_generations`, which
  does `hass.data[DOMAIN].get(entry_id)`, unambiguous.
- **Teardown belongs in `async_remove_entry`, not `async_unload_entry`.** A
  config entry *reload* is unload-then-setup. Destroying the limiter on unload
  would reset the window and let a reload admit a second full budget inside
  the same 10 seconds — exactly what FR-008 and SC-012 forbid. The integration
  has no `async_remove_entry` today; one must be added. It should drop the
  limiter only when no other *active* entry shares the same account key.
- **Reload must be distinguished from true unload/removal.** During reload,
  the entry's existing account and IP budget contributions remain active until
  the setup half replaces them. A true unload or removal removes the
  contribution and recomputes minima. This prevents the unload half of reload
  from temporarily raising the shared IP budget or a shared account budget.
- **Option changes must reconfigure in place, never recreate.** The existing
  `_async_update_listener` reloads the entry on any option change. On setup,
  `async_setup_entry` must call `limiter.configure(...)` on the
  already-registered instance rather than constructing a new one, for the same
  FR-008/SC-012 reason. The account gate receives the minimum configured
  budget across active entries sharing that account key; the shared IP gate
  receives the recomputed process-wide minimum. Neither reconfiguration clears
  admissions, and both re-pump affected queues.

**Config-flow validation path**: validation chooses between two limiter
sources. If an active entry for the account exists, validation reuses that
shared limiter so it participates in the already-loaded account and IP gates.
If no entry exists, validation uses a transient limiter that is injected into
the helpers' token manager and API client but is excluded from the active
account and IP budget minimums. This avoids leaving registry entries behind
for aborted flows while still limiting the FR-001 config-flow requests.

---

## R-011: Module placement and HA-independence

**Decision**: `custom_components/hostaway/api/rate_limit.py` — zero Home
Assistant imports, pure `asyncio` + `contextvars` + `heapq` + `time`. The
Home-Assistant-aware registry and lifecycle live in
`custom_components/hostaway/__init__.py`.

**Rationale**: Constitution principle II requires the API client layer to be
independently testable without Home Assistant, and feature 007 established the
convention with `api/custom_fields.py` (no HA imports). Keeping the limiter
HA-free means its concurrency, window, priority, and suppression behaviour can
be unit-tested with a fake clock and no HA test harness, which is what SC-001,
SC-003, SC-007, and SC-010 need.

The limiter needs a clock and a timer-scheduler. Both are injected
(`clock: Callable[[], float] = time.monotonic`, and a `call_later`-style hook
defaulting to `asyncio.get_running_loop().call_later`) so tests can drive time
deterministically without `asyncio.sleep`. This is what makes a "350 requests
back to back" test (User Story 1 acceptance scenario 1) run in milliseconds
instead of 20 real seconds.

**File size**: `pyproject.toml` sets `line-length = 88` and the repo's aislop
configuration flags files over 400 lines (see the `complexity/file-too-large`
ignores in `client.py` and `config_flow.py`). `rate_limit.py` is expected to
land near 300 lines. If it exceeds 400, split the gate implementations into
`api/rate_limit_gates.py` rather than adding a suppression comment.

---

## R-012: The composable-gate seam (FR-007, SC-014)

**Decision**: A request classifier maps method/path to selected gates. General
endpoints select the per-account general gate and the process-wide per-IP
general gate. Documented endpoint-specific endpoints select their own
account-scoped endpoint bucket **instead of** the general gates, matching
Hostaway's statement that such endpoints do not use the general limit. The
selected gates use a `BudgetGate` `Protocol` with synchronous
`capacity_available(now)`, `record(now)`, and `next_available(now)` methods.
Admission requires every selected gate to report capacity, records into every
selected gate, and computes one blocked waiter's gate-ready instant as
`max(next_available(...))` across that waiter's selected gates. The limiter's
single timer then wakes at the earliest pending reason across queued work:
gate readiness, suppression expiry, or a queued waiter's operation deadline.

**Rationale**: FR-007 requires that a future endpoint-specific dimension be
addable at the same enforcement point, with zero changes to public call sites,
while respecting Hostaway's rule that endpoint-specific limits do not use the
general pool. With this shape, per-account and per-IP limiting are both active
for general requests today. Adding an endpoint-specific counter later adds a classifier
entry and a gate for requests to that endpoint. No change to public API
methods, `coordinator.py`, `services/`, or `config_flow.py` is required.
SC-014 is demonstrable by a test that compares a general request, which
increments account and IP gates, with a prototype endpoint-specific request,
which increments only its endpoint bucket.

The gate methods are deliberately **synchronous**. An async gate would allow a
gate to await inside the admission decision, which would re-introduce the
interleaving and partial-admission races the single synchronous `_pump()`
exists to eliminate.

---

## R-013: Operator configuration surface

**Decision**: One new option, `CONF_RATE_LIMIT_BUDGET`
(`"rate_limit_budget"`), default `180`, validated `1 <= value <= 200`, added
to the existing `HostawayOptionsFlow.async_step_init` form inside a
**collapsed `homeassistant.data_entry_flow.section`** named `advanced`.

**Rationale and a correction to the feature brief**:

The brief asked for the option to be "advanced-mode-only". The obvious
mechanism, `FlowHandler.show_advanced_options`, is **not usable**. Verified
against installed Home Assistant 2026.9.4: the property is decorated with
`@deprecated_function("a user friendly way to present additional options in
the UI, for example a section", breaks_in_ha_version="2027.6")` and its body
is `return True` unconditionally. Gating on it would (a) emit a deprecation
warning, (b) break in HA 2027.6, and (c) not actually hide anything, because
it always returns `True`.

The replacement Home Assistant itself names in that deprecation message is a
form **section**. `homeassistant.data_entry_flow.section` was verified present
and takes `SectionConfig` with a `collapsed` boolean. A `collapsed=True`
section achieves the intent — the knob is tucked away from a casual operator
and is visibly marked as advanced — without a deprecated API.

**Implementation hazard this introduces**: a `section` nests its fields in the
submitted `user_input` under the section key, so
`user_input["advanced"]["rate_limit_budget"]`. The existing
`async_step_init` builds a flat `data={...}` dict for
`async_create_entry`. The new value must be **flattened** back out so
`entry.options[CONF_RATE_LIMIT_BUDGET]` stays flat and
`async_setup_entry` can read it the same way it reads every other option. The
existing flat options must keep their current shape; moving any of them into
the section would be a silent breaking change to stored options.

**Validation**: FR-032 requires rejection, not clamping. `vol.Range(max=200)`
inside the schema raises `vol.Invalid`, which Home Assistant surfaces as a
field error — but to produce the project's existing error-message style the
check should also appear in the explicit `if ... errors["base"] = ...` ladder
that `async_step_init` already uses for `invalid_scan_interval`. A new
`invalid_rate_limit_budget` error key goes in `strings.json` and
`translations/en.json`.

**Alternatives considered**:

- *`show_advanced_options`* — rejected, deprecated and non-functional, above.
- *A separate options sub-step* — rejected as heavier UX than one collapsed
  section for a single integer.
- *Not exposing it at all* — contradicts the resolved OQ-005 / FR-031.

---

## R-014: Log noise control for repeated sheds (FR-026, SC-006)

**Decision**: Per-coordinator cooldown state on the shared coordinator base:
the first shed in a quiet period logs `WARNING` naming the coordinator and the
cause; subsequent sheds within a cooldown window log `DEBUG` and increment a
counter; when a later shed occurs after the cooldown has lapsed, a single
`WARNING` summary reports the count since the last summary. There is no
dedicated summary timer, so SC-006 allows the warning-summary gap to be
300.0 seconds plus one coordinator interval.

**Rationale**: this mirrors a pattern already proven in this codebase —
`services/helpers.py::_log_locked_reservation` uses
`_LOCKED_LOG_COOLDOWN_SECONDS` with first-WARNING-then-DEBUG demotion and an
opportunistic prune of its state dict. Reusing the shape keeps the integration
internally consistent and reuses a pattern that already passes review. The
cooldown constant for sheds should be shorter than the reservation one (3600 s
is far too long when the shortest coordinator interval is 1 minute);
**300 seconds** is fixed, so a sustained shedding condition produces a
summary roughly every five minutes and stays visible per SC-006.

State is held per coordinator instance, not in a module-level dict, so it is
bounded by construction and disposed with the coordinator — no pruning needed.

---

## R-015: Diagnostics (FR-029, SC-013)

**Decision**: Add `custom_components/hostaway/diagnostics.py` exposing
`async_get_config_entry_diagnostics`.

**Rationale**: the integration has **no diagnostics module today** — verified
by `grep -rn diagnostics custom_components/`, which returns nothing. FR-029
requires limiter state to appear "in the integration's diagnostics output", so
this feature creates that surface. Home Assistant discovers a `diagnostics`
module in the integration package automatically; `manifest.json` needs no
change.

**Credential hazard, and the mitigation**: FR-029 says the output "MUST NOT
include credentials". The limiter's account key *is* `CONF_CLIENT_ID` — the
Hostaway client id, half of the credential pair. It must **not** be emitted
verbatim. Diagnostics should report a stable but non-reversible handle (a
truncated SHA-256 of the account key) plus the entry's own `unique_id`, which
is already non-secret and is what operators correlate against. The existing
`api/redaction.py` module is the natural home for the helper.

Payload shape is specified in
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md). It
uses a `gates` object so diagnostics can show account and IP utilization or
suppression diverging, and a `rate_limited_by_counter` object so observed 429s
are visible by applied counter rather than only as a total.

---

## R-016: Stale constants in `api/const.py`

**Finding**: `custom_components/hostaway/api/const.py` already declares
`RATE_LIMIT_PER_IP: int = 15  # per 10 seconds` and
`RATE_LIMIT_PER_ACCOUNT: int = 20  # per 10 seconds`. A repository-wide grep
shows they are referenced **nowhere** except `specs/001-.../tasks.md`.

Both values contradict this feature's documented 200/10s ceiling and
180/10s default. Leaving them in place next to the new, correct constants is
an active trap for the next reader.

**Decision**: remove both constants as part of this feature, in their own
atomic commit (constitution principle III), and introduce the new values —
`RATE_LIMIT_WINDOW_SECONDS = 10.0`, `RATE_LIMIT_CEILING = 200`,
`DEFAULT_RATE_LIMIT_BUDGET = 180` — in their place. They are unused, so
removal is non-breaking. The owner has accepted this as an implementation
requirement because leaving stale constants beside corrected values is a trap.

---

## R-017: Testing approach for the success criteria

**Decision**: three tiers.

1. **Pure limiter unit tests** (`tests/api/test_rate_limit.py`) with an
   injected fake clock and an injected timer hook. Covers SC-001, SC-003,
   SC-007, SC-010, SC-014, and the cancellation and monotonic edge cases. No
   `asyncio.sleep`, so a 350-request saturation test is instant.
2. **Client/auth integration tests** (`tests/api/test_client.py`,
   `tests/api/test_auth.py`) with `respx`-style mocked transports, asserting
   that acquisition happens per attempt (SC-010), that a new method is limited
   with no new code (SC-009), and that a 429 feeds back (SC-002).
3. **Home Assistant coordinator/flow/service tests** with
   `pytest-homeassistant-custom-component`. Covers SC-004, SC-005, SC-011,
   SC-012, SC-013, SC-015, SC-016, SC-017.

SC-016 ("shed leaves update interval and debounce timer unchanged") is
assertable directly: capture `coordinator.update_interval` and the identity of
`coordinator._debounced_refresh` plus `coordinator._unsub_refresh` before and
after a shed and assert both unchanged. That is the behavioural proof that
`async_set_updated_data` was not used.

SC-018 is asserted with sustained interactive arrivals and one scheduled waiter
queued behind them. Advance the fake clock to the 1.0-second aging threshold,
or admit 20 interactive requests, then assert the next capacity slot goes to
the scheduled waiter before its 2.0-second cycle deadline.

SC-008 is asserted structurally rather than by wall-clock benchmark, which
would be flaky in CI: assert that when the window has capacity, `acquire()`
resolves without yielding to a timer and without arming a `call_later`. A
microbenchmark would not survive CI variance.

**Baseline to preserve**: 493 tests passing, `ruff check` clean, `mypy` clean.
SC-011 requires that number only ever grow.
