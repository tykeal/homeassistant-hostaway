<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Phase 1 Data Model: Proactive Hostaway API Rate Limiting

**Feature**: `008-proactive-rate-limiting` | **Date**: 2026-10-08 |
**Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

This feature introduces no persisted storage and no config entry migration
(FR-033). Every entity below is in-memory runtime state, except
`CONF_RATE_LIMIT_BUDGET`, which is an optional config entry **option** with a
default, so existing entries load unchanged.

All types live in `custom_components/hostaway/api/rate_limit.py` unless
otherwise noted. That module has **zero Home Assistant imports** (R-011).

---

## 1. `RequestPriority`

```python
class RequestPriority(IntEnum):
    INTERACTIVE = 0
    SCHEDULED = 1
```

| Property | Value |
|---|---|
| Purpose | Admission **ordering** only (FR-018, FR-019) |
| Ordering | Lower value wins. `IntEnum` so the heap key sorts naturally. |
| Not responsible for | Saturation behaviour — that is `WaitPolicy` (R-008) |

`INTERACTIVE` covers user-triggered service calls, config-flow validation, and
the setup-time connectivity probe. `SCHEDULED` covers coordinator refreshes
and every paginated continuation inside one.

**Validation rule**: the enum has exactly two members. FR-018 says "at least
two levels"; adding a third later is additive and requires no call-site change
because the context is carried by contextvar (R-004).

---

## 2. `WaitPolicy`

```python
@dataclass(frozen=True, slots=True)
class WaitPolicy:
    duration: float
    shed_on_timeout: bool
```

| Field | Type | Meaning |
|---|---|---|
| `duration` | `float` seconds | Duration used once to create an operation or cycle deadline |
| `shed_on_timeout` | `bool` | `True` → raise `HostawayRateLimitShedError`; `False` → raise `HostawayRateLimitWaitTimeout` |

`WaitPolicy` does **not** grant each acquisition its own timeout. It is an
input to `RequestContext.start(...)`, which stores a single absolute monotonic
deadline for the logical operation. `acquire()` and retry backoff helpers read
the remaining time from that deadline.

Exactly three instances exist, exposed as module constants:

| Constant | `duration` | `shed_on_timeout` | Used by | Requirement |
|---|---|---|---|---|
| `INTERACTIVE_POLICY` | `30.0` | `False` | services, config flow, setup probe | FR-020, SC-004 |
| `SCHEDULED_POLICY` | `2.0` | `True` | coordinator refresh, 2nd and later | FR-021 |
| `FIRST_REFRESH_POLICY` | `30.0` | `False` | listings/reservations first refresh; custom-fields first refresh retry | FR-037, SC-015 |

**Validation rules**:

- `duration > 0`. A zero duration would make FR-021's "grace period is
  non-zero" unenforceable.
- `INTERACTIVE_POLICY.duration == 30.0`, but tests assert one operation-wide
  deadline rather than one 30-second wait per acquisition.
- `FIRST_REFRESH_POLICY.shed_on_timeout` must be `False`. FR-037 is absolute;
  a test asserts it.

---

## 3. `RequestContext`

```python
@dataclass(frozen=True, slots=True)
class RequestContext:
    priority: RequestPriority
    policy: WaitPolicy
    deadline: float

    @classmethod
    def start(
        cls, priority: RequestPriority, policy: WaitPolicy, now: float
    ) -> RequestContext: ...

    def remaining(self, now: float) -> float: ...
```

Carried through an ambient `contextvars.ContextVar` (R-004):

```python
_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "hostaway_request_context",
    default=None,
)

@contextmanager
def request_context(ctx: RequestContext) -> Iterator[None]: ...

def current_request_context() -> RequestContext: ...
```

| Rule | Rationale |
|---|---|
| Missing context is converted at acquisition time to `SCHEDULED` + `SCHEDULED_POLICY` with a conservative 2-second deadline for that lone acquisition | A forgotten call site gets the conservative, sheddable treatment rather than jumping the interactive queue |
| Immutable (`frozen=True`) | A context leaking a mutation across tasks would be a cross-talk bug |
| Set once at the logical operation boundary | The deadline is operation-wide or cycle-wide, never per request, page, retry, or acquisition (FR-020, FR-021) |
| Set in exactly four structural places | See R-004 table; none of them is a per-API-method change (FR-002, SC-009) |

**State transition**: none. The context is set on entry to a logical operation
and restored on exit by the context manager, including on exception. Tasks
created inside inherit a copy via `asyncio.Task`'s context snapshot. Retry
sleeps MUST use `ctx.remaining(now)` before sleeping; if no time remains, they
raise the same timeout/shed exception the next acquisition would raise.

---

## 4. `BudgetGate` (Protocol)

```python
class BudgetGate(Protocol):
    name: str
    scope: GateScope

    def capacity_available(self, now: float) -> bool: ...
    def record(self, now: float) -> None: ...
    def next_available(self, now: float) -> float | None: ...
    def suppress_until(self, until: float) -> None: ...
```

The gate abstraction used after request classification (FR-007, SC-014).
Endpoint-specific counters are selected **instead of** general gates, not
appended to them.

| Method | Contract |
|---|---|
| `capacity_available(now)` | Logically read-only predicate. It MUST NOT block or change effective capacity, but MAY discard expired timestamps from its own deque before answering. |
| `record(now)` | Called exactly once per admission, on every selected gate, only after **all** selected gates returned `True`. |
| `next_available(now)` | Logically read-only monotonic instant at which this gate could next admit, or `None` if it can admit now. It MAY discard expired timestamps before answering. |
| `suppress_until(until)` | Extends this gate's suppression and notifies all limiters waiting on it. |

**Validation rules**:

- All three methods are **synchronous**. An async gate would allow interleaving
  inside the admission decision and reintroduce partial-admission races
  (R-012).
- A request is admitted only when *every selected* gate reports capacity.
  General requests select account-general plus IP-general. A documented
  endpoint-specific request selects its endpoint bucket instead (FR-007).
- For one blocked waiter, the gate-ready instant is the `max()` of the
  non-`None` `next_available()` values across that waiter's selected gates.
  The limiter's single shared timer is armed at the earliest relevant wake-up
  across queued work: the earliest gate-ready instant for any blocked waiter,
  provider or gate suppression expiry, or queued waiter deadline.

Today classification selects the per-account general gate and the
process-wide per-IP general gate for every implemented endpoint. A future
endpoint-specific counter adds a classifier entry for that method/path and
selects the endpoint bucket only — no public API, coordinator, or service
call-site change (SC-014).

---

## 5. `SlidingWindowGate`

The one concrete gate. Implements the spec's **Request budget** entity.

| Field | Type | Meaning |
|---|---|---|
| `_budget` | `int` | Admissions permitted per window. Default `180` for general gates; for the IP and account gates this is the minimum across active entries for the relevant scope. |
| `_max_budget` | `int` | Per-gate ceiling. General gates use `200`; endpoint gates use their documented ceiling, for example `400`. |
| `_window` | `float` | Window length for this gate. General gates use `10.0`; endpoint gates use their documented value, for example `60.0` for conversations. |
| `_admissions` | `deque[float]` | Monotonic timestamp per admission inside the current window |
| `_synthetic_admissions` | `deque[float]` | Synthetic monotonic timestamps added only to reconcile lower server-reported `X-RateLimit-Remaining` values |

**Invariants**:

- `1 <= _budget <= _max_budget`. General gates set `_max_budget` to
  `RATE_LIMIT_CEILING` (`200`); endpoint-specific gates set it to the
  documented endpoint ceiling, such as `30`, `400`, or `200`.
- `_window > 0`. It is configured when the gate is created. General gates
  always use `10.0`; the window length is still not operator-configurable
  (FR-034).
- `len(_admissions) + len(_synthetic_admissions)` may exceed `_budget` after
  a budget reduction or server reconciliation, but while it does the gate has
  no capacity and MUST NOT discard timestamps to fit the budget.
- Timestamps are `time.monotonic()` values, never wall clock (FR-011).
- Both `_admissions` and `_synthetic_admissions` are pruned (`popleft` while
  `now - deque[0] >= _window`) at the start of every `capacity_available`,
  `record`, and `next_available` call. Memory is bounded by `_max_budget`
  entries across the two deques, not by the current `_budget`, because a
  budget reduction can intentionally retain more in-window timestamps than
  the current budget permits.

**Operations**:

| Operation | Effect |
|---|---|
| `capacity_available(now)` | prune both deques, then test `len(_admissions) + len(_synthetic_admissions) < _budget`. This is logically read-only even though pruning may discard expired entries. |
| `record(now)` | prune both deques, then `append(now)` to `_admissions`. The append is permitted only after the limiter has confirmed capacity; implementations assert the combined deque length never exceeds `_max_budget`. |
| `next_available(now)` | prune both deques; `None` if capacity, else take the earliest non-empty head across `_admissions` and `_synthetic_admissions` and return `head + _window`. It MUST NOT index either deque unless it is non-empty, because server feedback can fill only `_synthetic_admissions`. This is logically read-only even though pruning may discard expired entries. |
| `reconfigure(budget)` | validates range against `_max_budget`, sets `_budget`. **Does not touch `_admissions` or `_synthetic_admissions`.** Re-pumps all queues waiting on the gate. |
| `suppress_until(until)` | extends gate-scoped suppression and re-pumps all waiting limiters when it changes or expires. |
| `reconcile(limit, remaining)` | applies Hostaway feedback for this gate only; see §8. |

**`reconfigure` is the FR-008 / SC-012 guarantee.** An options change reloads
the entry, and the reload must *not* clear the window. Lowering the budget
below the current in-window count is legal and simply means no admission
occurs until the window drains — the gate must not discard timestamps to make
the new budget fit.

---

## 6. `Waiter`

Internal to `AccountRateLimiter`. One per in-flight `acquire()` that could not
be admitted immediately.

| Field | Type | Meaning |
|---|---|---|
| `priority` | `RequestPriority` | normal heap key, primary |
| `sequence` | `int` | heap key, secondary — FIFO fairness within a class |
| `queued_at` | `float` | monotonic instant used for anti-starvation aging |
| `interactive_admissions_at_queue` | `int` | interactive admission count when this waiter queued |
| `future` | `asyncio.Future[None]` | resolved by `_pump()` on admission |

**Heap ordering**: normal selection is `(priority, sequence)`. Before admitting
an interactive waiter, `_pump()` checks the oldest scheduled waiter; if it has
waited at least 1.0 s or 20 interactive admissions have occurred while it was
queued, that scheduled waiter is selected next. `sequence` comes from a
monotonically increasing per-limiter counter, so it never ties and `heapq`
never has to compare `Future` objects.

**State transitions**:

```text
                  _pump() admits
   queued  ────────────────────────────▶  admitted  (future resolved, window recorded)
      │
      │ timeout elapsed
      ├────────────────────────────────▶  timed out (removed from heap, re-pump)
      │                                    raises shed OR wait-timeout per policy
      │ task cancelled
      └────────────────────────────────▶  cancelled (removed from heap, re-pump)
```

**Cancellation invariant (spec edge case "Cancellation")**: a waiter removed
from the heap before its future resolved has consumed **no** window capacity,
so nothing leaks. If the future was already resolved in the same loop
iteration the waiter was cancelled, the admission is already recorded and MUST
NOT be un-recorded — un-recording would over-admit against a server that has
no idea the caller gave up. At most one slot is conservatively wasted.

---

## 7. `AccountRateLimiter`

The per-account aggregate and request classifier. One instance per Hostaway
account id (FR-005), sharing the process-wide IP gate object and provider
suppression state with other accounts.

| Field | Type | Meaning |
|---|---|---|
| `account_key` | `str` | `CONF_CLIENT_ID`. **Never emitted raw** — see §11. |
| `_account_general_gate` | `SlidingWindowGate` | per-account general counter |
| `_ip_general_gate` | `SlidingWindowGate` | shared process-wide IP counter |
| `_endpoint_gates` | `dict[EndpointKey, SlidingWindowGate]` | future endpoint-specific buckets selected by classifier |
| `_waiters` | `list[Waiter]` (heap) | pending acquisitions |
| `_sequence` | `int` | monotonic waiter counter |
| `_provider_suppression` | `ProviderSuppression` | shared process-wide provider suppression state |
| `_timer` | timer handle `\| None` | single armed wake-up |
| `_clock` | `Callable[[], float]` | injected, defaults `time.monotonic` |
| `_schedule` | timer hook | injected, defaults to the running loop's `call_later` |
| `_stats` | `LimiterStats` | diagnostics counters |

**Public surface** (the full contract is in
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md)):

- `async acquire() -> None`
- `note_rate_limited(applied_counter: str | None, retry_at: float | None, method: str, path: str, limit: int | None = None, remaining: int | None = None) -> None`
- `configure(*, account_budget: int, effective_ip_budget: int) -> None`
- `snapshot() -> LimiterSnapshot`

**Invariants**:

- `acquire()` holds no lock across an `await`. The admission decision is the
  synchronous `_pump()`; the caller only ever awaits its own `Future`. This is
  what makes the nested token-request-inside-data-request path (R-002)
  deadlock-free.
- At most **one** timer is armed at a time. Arming replaces, never stacks.
- A timer is armed only when `_waiters` is non-empty. An idle limiter costs
  zero event-loop wakeups (SC-008).
- Admission is reservation-only: there is no `release()`. The budget is a
  **rate**, not a concurrency pool, so a completed request does not return a
  slot.

**`_pump()` — the single admission decision point**:

```text
while _waiters:
    now = _clock()
    wake_deadlines = collect_queued_waiter_deadlines(_waiters)
    if _provider_suppression.active(now):
        arm shared timer at earliest(_provider_suppression.until, wake_deadlines)
        return
    for candidate in ordered_waiters_with_aging(_waiters, now):
        if candidate.future.done():   # cancelled between arrival and pump
            remove candidate from heap
            continue
        gates = classify(candidate.method, candidate.path)
        suppressed_until = max(g.suppressed_until for g in gates)
        if suppressed_until is not None and now < suppressed_until:
            remember blocked wake-up at suppressed_until for this candidate
            continue
        if not all(g.capacity_available(now) for g in gates):
            remember blocked wake-up at max(g.next_available(now) for g in gates)
            continue
        remove candidate from heap
        for g in gates:
            g.record(now)             # conjunction: record into every gate
        candidate.future.set_result(None)
        continue                      # another waiter may now be admissible
    arm shared timer at earliest remembered blocked wake-up or waiter deadline
    return
```

Because suppression is tested inside `_pump()`, it applies to interactive
waiters identically — FR-016 falls out of the structure rather than from a
special case. Candidate ordering happens before classification, and each
candidate's own gates are classified and capacity-checked before that same
candidate can be admitted. Blocked candidates stay queued, but a blocked
candidate does **not** stop the scan if a lower-priority candidate uses an
unrelated gate set that is currently admissible. The candidate admitted is
therefore the highest-priority candidate, after FR-019 aging, whose complete
gate set can admit now. This preserves the round-1 guard against classifying
one waiter and admitting another while avoiding head-of-line blocking across
independent endpoint buckets.

---

## 8. Suppression state

The spec's **Suppression state** entity is gate-scoped, not one field on the
per-account limiter.

| Trigger | Duration |
|---|---|
| 429 with `X-RateLimit-Retry-After: T` | Convert Unix timestamp `T` to a delay, then `min(max(delay, 0.1), MAX_BACKOFF)` (FR-015) |
| 429 without `X-RateLimit-Retry-After` | `DEFAULT_SUPPRESSION_SECONDS` = **10.0** (one full documented general window) — *fixed by owner decision* |

**Rules**:

- `note_rate_limited()` **extends** the selected gate or provider-global
  suppression, never shortens it. Two 429s in flight must not let the second
  one shorten the first one's backoff. Provider suppression is stored in the
  shared `ProviderSuppression` object, so a provider 429 observed by one
  account immediately blocks all Hostaway limiters in the process.
- Suppression applies to all priority classes (FR-016).
- `account` suppresses that account's general gate; `ip` suppresses the shared
  process-wide IP gate; `endpoint` suppresses the classified endpoint bucket;
  `provider` sets process-wide global suppression across all Hostaway limiters.
- `X-RateLimit-Limit` updates only the affected gate's runtime ceiling. The
  update is one-way downward: `effective_ceiling = min(configured_budget,
  observed_limit)`. A server-reported limit MUST NOT raise capacity above an
  operator-configured budget, and higher later values are ignored until an
  explicit operator reconfiguration or restart resets the runtime ceiling.
- `X-RateLimit-Remaining` reconciles only downward. If the server reports
  fewer remaining requests than the gate currently believes are available,
  the gate adds synthetic in-window admission timestamps at `now` until local
  available capacity is no greater than the server value. It never removes
  local admissions in response to a larger server remaining value.
- Suppression clears implicitly by time comparison in `_pump()` — no reload,
  no restart (FR-017, SC-007).
- Changing suppression on a shared gate or the provider suppression object
  re-pumps every limiter waiting on that state, including expiry wakeups.

**Call sites** (both inside `api/`, neither visible to API-method authors):

1. `HostawayApiClient._request`, in the `status_code == 429` branch, before
   the retry/raise decision.
2. `HostawayTokenManager._request_token`, in its `status_code == 429` branch,
   before raising `HostawayRateLimitError`.

---

## 9. `LimiterStats` and `LimiterSnapshot`

Diagnostics-facing, FR-029 / SC-013.

```python
@dataclass(slots=True)
class LimiterStats:
    admitted_total: int
    shed_total: int                 # sheds reported back by coordinators
    rate_limited_total: int         # observed 429s
    rate_limited_by_counter: dict[str, int]  # observed 429s by applied counter
    interactive_wait_total: float   # cumulative seconds
    scheduled_wait_total: float
```

```python
@dataclass(frozen=True, slots=True)
class GateSnapshot:
    budget: int
    effective_budget: int
    window_seconds: float
    admitted_in_window: int
    waiting_interactive: int
    waiting_scheduled: int
    suppressed: bool
    suppressed_for_seconds: float | None


@dataclass(frozen=True, slots=True)
class LimiterSnapshot:
    gates: dict[str, GateSnapshot]
    admitted_total: int
    shed_total: int
    rate_limited_total: int
    rate_limited_by_counter: dict[str, int]
```

`snapshot()` is a pure read; it prunes each gate's window first so
per-gate `admitted_in_window` values are accurate at the instant of the call.

**Shed counting is per coordinator, not per limiter.** FR-025 and SC-006
require the *coordinator name*. The shed counter therefore lives on the
coordinator base (§10) and is aggregated into diagnostics alongside the
limiter snapshot; `LimiterStats.shed_total` is the integration-wide roll-up fed by
coordinators calling `limiter.note_shed()`. `rate_limited_by_counter` uses
Hostaway's applied-counter names (`account`, `ip`, `endpoint`, `provider`)
when known and an `unknown` bucket when the header is missing or unrecognized,
so FR-029 and SC-013 can produce both the total and the per-counter break
down.

---

## 10. Coordinator shed state

Lives on a new shared base class in
`custom_components/hostaway/coordinator.py`.

| Field | Type | Meaning |
|---|---|---|
| `_first_refresh_complete` | `bool` | `False` until `_async_fetch_data` first returns successfully |
| `_shed_count` | `int` | lifetime sheds for this coordinator instance |
| `_shed_since_summary` | `int` | sheds since the last WARNING |
| `_last_shed_log` | `float \| None` | monotonic instant of the last WARNING |

**`_first_refresh_complete` MUST NOT be replaced by a `self.data is None`
test.** `HostawayCustomFieldsCoordinator.__init__` sets `self.data = []`, so
its `data` is never `None`; a `data is None` test would classify its first
refresh as already initialized and a failure would publish `[]` as though no
definitions exist. FR-037 requires a distinct not-yet-loaded state for custom
fields. The listings coordinator has the same hazard with a legitimate empty
`{}`.

**State transitions**:

```text
                    _async_fetch_data succeeds
 first-refresh ───────────────────────────────────▶ steady
 (never shed,        ▲                               │
  30 s wait)         │                               │ shed error caught
                     │  (never returns here)         ▼
                     └────────────────────── return self.data, log, count
```

**Shed log cooldown (FR-026, SC-006)**, modelled on the proven
`services/helpers.py::_log_locked_reservation` pattern:

| Condition | Level | Content |
|---|---|---|
| First shed, or cooldown lapsed | `WARNING` | coordinator name + "rate limiting" + count since last summary |
| Within cooldown | `DEBUG` | same detail |

`_SHED_LOG_COOLDOWN_SECONDS = 300.0` *(fixed by owner decision)*. State is per
instance, so it is bounded by construction and needs no pruning.

---

## 11. Account key handle (credential hygiene)

The limiter's `account_key` is `CONF_CLIENT_ID` — half of the Hostaway
credential pair. FR-029 forbids credentials in diagnostics.

| Rule | Detail |
|---|---|
| Never emit `account_key` verbatim | Not in diagnostics, not in `__repr__`, not in any log record |
| Diagnostics identifier | `account_handle` = first 12 hex chars of `sha256(account_key)` |
| Correlation | Use `account_handle` only. The config entry `unique_id` MUST NOT be emitted: `config_flow.py:244` sets it from `CONF_CLIENT_ID`, so it is the credential itself |
| Coordinator keys | Fixed labels (`listings`, `reservations`, `custom_fields`); never the generated coordinator names, which embed the unique id |

The helper belongs in the existing `custom_components/hostaway/api/redaction.py`
alongside the other redaction utilities.

---

## 12. Configuration option

| Property | Value |
|---|---|
| Constant | `CONF_RATE_LIMIT_BUDGET` in `custom_components/hostaway/const.py` |
| Storage key | `"rate_limit_budget"` |
| Location | config entry **options** only (FR-031) — never a setup-time field |
| Default | `DEFAULT_RATE_LIMIT_BUDGET = 180` (FR-010, FR-032) |
| Minimum | `1` — strictly greater than zero (FR-032) |
| Maximum | `RATE_LIMIT_CEILING = 200` (FR-032) |
| Out-of-range | **Rejected** with a validation error. Never clamped. (FR-032, SC-017) |
| UI placement | Collapsed `section` in the existing options form (R-013) |
| Migration | **None.** Absent key reads as `180`. (FR-033) |

**New constants in `custom_components/hostaway/api/const.py`**:

| Constant | Value | Note |
|---|---|---|
| `RATE_LIMIT_WINDOW_SECONDS` | `10.0` | not operator-configurable (FR-034) |
| `RATE_LIMIT_CEILING` | `200` | Hostaway's documented ceiling |
| `DEFAULT_RATE_LIMIT_BUDGET` | `180` | safety margin, **not** a Hostaway value (FR-010) |
| `DEFAULT_SUPPRESSION_SECONDS` | `10.0` | fixed by owner decision, FR-015 |

**Removed from the same module**: `RATE_LIMIT_PER_IP = 15` and
`RATE_LIMIT_PER_ACCOUNT = 20`. Both are unused repo-wide and both contradict
the 200/10s figure (R-016).

---

## 13. Exceptions

Added to `custom_components/hostaway/api/exceptions.py`.

```python
class HostawayRateLimitShedError(Exception):
    """A scheduled refresh cycle could not obtain budget and must be shed."""
    def __init__(self, message: str, *, waited: float) -> None: ...
```

```python
class HostawayRateLimitWaitTimeout(HostawayRateLimitError):
    """A bounded wait for rate-limit budget expired before admission."""
    def __init__(self, message: str, *, waited: float) -> None: ...
```

| Exception | Base | Why that base |
|---|---|---|
| `HostawayRateLimitShedError` | **`Exception`**, deliberately *not* `HostawayApiError` | All three coordinators contain `except HostawayApiError: raise UpdateFailed(...)`. Inheriting from `HostawayApiError` would let a shed be converted into a **failure** before the shed handler saw it, producing the "failed to update" SC-005 forbids. Keeping it outside the hierarchy makes that mistake structurally impossible. (R-006) |
| `HostawayRateLimitWaitTimeout` | `HostawayRateLimitError` → `HostawayApiError` | It *is* a request failure. Inheriting preserves the listings/reservations first-refresh path: existing `except HostawayApiError` → `UpdateFailed` → `ConfigEntryNotReady`, exactly what FR-037 and SC-015 require. |

**Required companion changes**:
`HostawayCustomFieldsCoordinator._async_update_data` ends with a bare
`except Exception as exc: raise UpdateFailed(...)`. That tail would swallow
`HostawayRateLimitShedError` from inside the fetch body. An explicit
`except HostawayRateLimitShedError: raise` must precede it.

Every service or helper that catches `HostawayRateLimitError` must be audited.
The custom-field write helper currently retries that exception as if it were a
server 429. It needs an earlier `except HostawayRateLimitWaitTimeout: raise`
(or conversion directly to `ServiceValidationError`) before
`except HostawayRateLimitError`, so a local proactive queue timeout is not
slept on and the read/merge/write sequence is not re-run. A regression test
MUST prove no service-level sleep or retry happens for this exception.

---

## 14. Entity relationships

```text
hass.data["hostaway_rate_limiters"]        (separate top-level key — NOT
   │                                        hass.data[DOMAIN], see R-010)
   └── dict[account_key -> AccountRateLimiter]
                                │
                                ├── account_general_gate           (FR-005)
                                ├── shared ip_general_gate          (FR-006)
                                ├── endpoint classifier/gates       (FR-007 seam)
                                ├── _waiters: heap[Waiter]          (FR-019)
                                ├── gate/global suppression         (FR-014..017)
                                └── _stats: LimiterStats            (FR-029)

AccountRateLimiter is injected into, and shared by:
   HostawayTokenManager  ──▶ _request_token        (FR-001, R-002)
   HostawayApiClient     ──▶ _request              (FR-002, chokepoint)

RequestContext (ContextVar) is set by:
   services/__init__.py::_bind_handler             → INTERACTIVE
   config_flow.py validation helpers               → INTERACTIVE
   __init__.py::async_setup_entry test_connection  → INTERACTIVE
   coordinator base::_async_update_data            → SCHEDULED (+ first-refresh policy)
```

**Lifetime (FR-008, SC-012)**:

| Event | Limiter |
|---|---|
| First entry for an account set up | created, registered |
| Second entry, same `CONF_CLIENT_ID` | **shared**, not duplicated (FR-005) |
| Options changed → entry reload | **survives**; account gate reconfigured in place from the minimum across active same-account entries, and the shared IP gate recomputes the minimum across active entries while preserving the reloading entry's previous contribution until setup replaces it. Windows are **not** reset. |
| Entry unloaded for reload | **survives**; budget contributions remain active until replacement setup completes, so the unload half cannot raise account or IP capacity |
| Entry truly unloaded or removed | contribution removed; account and IP minima recompute in place, and waiters are re-pumped |
| Entry removed (`async_remove_entry`) | account limiter dropped iff no other active entry shares the account key; shared IP gate recomputes its minimum |
| Home Assistant restart | gone; starts empty (explicitly out of scope — "Persisting limiter state across restarts") |
