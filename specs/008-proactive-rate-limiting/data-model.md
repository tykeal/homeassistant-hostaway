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
| `capacity_available(now)` | Pure predicate. MUST NOT mutate. MUST NOT block. |
| `record(now)` | Called exactly once per admission, on every selected gate, only after **all** selected gates returned `True`. |
| `next_available(now)` | Monotonic instant at which this gate could next admit, or `None` if it can admit now. |
| `suppress_until(until)` | Extends this gate's suppression and notifies all limiters waiting on it. |

**Validation rules**:

- All three methods are **synchronous**. An async gate would allow interleaving
  inside the admission decision and reintroduce partial-admission races
  (R-012).
- A request is admitted only when *every selected* gate reports capacity.
  General requests select account-general plus IP-general. A documented
  endpoint-specific request selects its endpoint bucket instead (FR-007).
- The limiter arms its timer at `max()` of the non-`None` `next_available()`
  values across gates.

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
| `_budget` | `int` | Admissions permitted per window. Default `180`; for the IP gate this is the minimum across loaded entries. |
| `_window` | `float` | Window length, `10.0` seconds. **Not** operator-configurable (FR-034). |
| `_admissions` | `deque[float]` | Monotonic timestamp per admission inside the current window |

**Invariants**:

- `1 <= _budget <= 200` (FR-032). `200` is `RATE_LIMIT_CEILING`.
- `_window == 10.0` always. There is no setter.
- `len(_admissions) <= _budget` at all times, after pruning.
- Timestamps are `time.monotonic()` values, never wall clock (FR-011).
- The deque is pruned (`popleft` while `now - _admissions[0] >= _window`) at
  the start of every `capacity_available`, `record`, and `next_available`
  call, so memory is bounded by `_budget` entries (max 200 floats).

**Operations**:

| Operation | Effect |
|---|---|
| `capacity_available(now)` | prune, then `len(_admissions) < _budget` |
| `record(now)` | prune, then `append(now)` |
| `next_available(now)` | prune; `None` if capacity, else `_admissions[0] + _window` |
| `reconfigure(budget)` | validates range, sets `_budget`. **Does not touch `_admissions`.** Re-pumps all queues waiting on the gate. |
| `suppress_until(until)` | extends gate-scoped suppression and re-pumps all waiting limiters when it changes or expires. |

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
account id (FR-005), sharing the process-wide IP gate object with other
accounts.

| Field | Type | Meaning |
|---|---|---|
| `account_key` | `str` | `CONF_CLIENT_ID`. **Never emitted raw** — see §11. |
| `_account_general_gate` | `SlidingWindowGate` | per-account general counter |
| `_ip_general_gate` | `SlidingWindowGate` | shared process-wide IP counter |
| `_endpoint_gates` | `dict[EndpointKey, SlidingWindowGate]` | future endpoint-specific buckets selected by classifier |
| `_waiters` | `list[Waiter]` (heap) | pending acquisitions |
| `_sequence` | `int` | monotonic waiter counter |
| `_global_suppressed_until` | `float \| None` | provider-wide suppression ends |
| `_timer` | timer handle `\| None` | single armed wake-up |
| `_clock` | `Callable[[], float]` | injected, defaults `time.monotonic` |
| `_schedule` | timer hook | injected, defaults to the running loop's `call_later` |
| `_stats` | `LimiterStats` | diagnostics counters |

**Public surface** (the full contract is in
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md)):

- `async acquire() -> None`
- `note_rate_limited(applied_counter: str | None, retry_at: float | None, method: str, path: str) -> None`
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
    if _global_suppressed_until is not None and now < _global_suppressed_until:
        arm timer at _global_suppressed_until ; return   # provider suppression
    gates = classify(waiter.method, waiter.path)
    suppressed_until = max(g.suppressed_until for g in gates)
    if suppressed_until is not None and now < suppressed_until:
        arm timer at suppressed_until ; return           # FR-014, FR-016
    if not all(g.capacity_available(now) for g in gates):
        arm timer at max(g.next_available(now)) ; return # FR-009
    waiter = select_next_waiter_with_aging(_waiters, now)
    if waiter.future.done():      # cancelled between arrival and pump
        continue
    for g in gates:
        g.record(now)             # conjunction: record into every gate
    waiter.future.set_result(None)
```

Because suppression is tested inside `_pump()`, it applies to interactive
waiters identically — FR-016 falls out of the structure rather than from a
special case.

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
  one shorten the first one's backoff.
- Suppression applies to all priority classes (FR-016).
- `account` suppresses that account's general gate; `ip` suppresses the shared
  process-wide IP gate; `endpoint` suppresses the classified endpoint bucket;
  `provider` sets global suppression across all Hostaway limiters.
- Suppression clears implicitly by time comparison in `_pump()` — no reload,
  no restart (FR-017, SC-007).
- Changing suppression on a shared gate re-pumps every limiter waiting on that
  gate, including expiry wakeups.

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
    rate_limited_total: int
```

`snapshot()` is a pure read; it prunes each gate's window first so
per-gate `admitted_in_window` values are accurate at the instant of the call.

**Shed counting is per coordinator, not per limiter.** FR-025 and SC-006
require the *coordinator name*. The shed counter therefore lives on the
coordinator base (§10) and is aggregated into diagnostics alongside the
limiter snapshot; `LimiterStats.shed_total` is the integration-wide roll-up fed by
coordinators calling `limiter.note_shed()`.

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
| Correlation | Diagnostics also carries the config entry's `unique_id`, which is already non-secret |

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
| Options changed → entry reload | **survives**; account gate reconfigured in place and the shared IP gate recomputes the minimum across loaded entries. Windows are **not** reset. |
| Entry unloaded | **survives** for account windows needed by reload; the shared IP gate recomputes its minimum from remaining loaded entries and re-pumps waiters |
| Entry removed (`async_remove_entry`) | account limiter dropped iff no other loaded entry shares the account key; shared IP gate recomputes its minimum |
| Home Assistant restart | gone; starts empty (explicitly out of scope — "Persisting limiter state across restarts") |
