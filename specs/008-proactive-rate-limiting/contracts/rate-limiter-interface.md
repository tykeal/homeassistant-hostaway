<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Contract: Rate Limiter Interface

**Feature**: `008-proactive-rate-limiting` | **Date**: 2026-10-08

This integration exposes no new external HTTP API and no new Home Assistant
service. The contracts that matter for this feature are **internal
interfaces** that other modules depend on, plus two **operator-facing
contracts** (an options-flow field and a diagnostics payload) that are visible
outside the codebase.

Each section states the contract and the observable behaviour tests must
assert. Behaviour marked **MUST** is traceable to a requirement in
[spec.md](../spec.md).

---

## 1. `AccountRateLimiter` — the acquisition contract

Module: `custom_components/hostaway/api/rate_limit.py`

```python
class AccountRateLimiter:
    def __init__(
        self,
        account_key: str,
        *,
        account_budget: int = DEFAULT_RATE_LIMIT_BUDGET,
        ip_gate: SlidingWindowGate | None = None,
        clock: Callable[[], float] = time.monotonic,
        schedule: TimerScheduler | None = None,
    ) -> None: ...

    async def acquire(self, method: str, path: str) -> None: ...
    def note_rate_limited(
        self,
        applied_counter: str | None,
        retry_at: float | None,
        method: str,
        path: str,
        limit: int | None = None,
        remaining: int | None = None,
    ) -> None: ...
    def note_shed(self) -> None: ...
    def configure(self, *, account_budget: int, effective_ip_budget: int) -> None: ...
    def snapshot(self) -> LimiterSnapshot: ...
```

### `async acquire(method, path) -> None`

Reads the ambient `RequestContext` (priority class, wait policy, and absolute
monotonic deadline) from the contextvar, classifies the request by method and
path, and returns when the request may be sent. It raises when the operation's
remaining deadline expires. The method/path inputs are supplied only by the
existing enforcement chokepoints; public API method, coordinator, and service
call sites do not change.

| Behaviour | Requirement |
|---|---|
| MUST return without awaiting a timer when every selected gate has capacity and suppression is inactive | SC-008 |
| MUST record the admission into **every selected** gate before returning | FR-007 |
| MUST classify general requests to account-general + IP-general and endpoint-specific requests to their endpoint bucket instead | FR-007, SC-014 |
| MUST NOT record an admission for a caller that times out or is cancelled before admission | spec edge case "Cancellation" |
| MUST normally admit a waiting `INTERACTIVE` caller before a waiting `SCHEDULED` caller when capacity frees | FR-019, SC-003 |
| MUST admit the oldest scheduled waiter once FR-019 aging is met and that waiter's gates have capacity | FR-019, SC-018 |
| MUST retain blocked waiters in the queue while continuing to scan for the highest-priority waiter whose complete gate set is currently admissible, so unrelated endpoint buckets are not head-of-line blocked | FR-014 |
| MUST preserve FIFO order within one priority class | fairness; prevents starvation inside a class |
| MUST raise `HostawayRateLimitShedError` when the operation-wide `SCHEDULED_POLICY` deadline has no remaining time | FR-021 |
| MUST raise `HostawayRateLimitWaitTimeout` when the operation-wide `INTERACTIVE_POLICY` or `FIRST_REFRESH_POLICY` deadline has no remaining time | FR-020, FR-037, SC-004 |
| MUST NOT give each acquisition a fresh timeout; queued waits consume `RequestContext.remaining(now)` | FR-020, FR-021 |
| MUST use one shared timer per limiter, armed for the earliest gate availability, suppression expiry, or queued waiter deadline, so a waiter timeout fires even while capacity remains blocked | FR-020, FR-021 |
| MUST NOT admit while a selected gate or provider-global suppression is active, **regardless of priority class** | FR-014, FR-016 |
| MUST use the injected `clock` for every time decision, never wall clock | FR-011 |
| MUST hold no lock across an `await`, so a nested acquisition (token request inside a data request) cannot deadlock | R-002 |
| MUST NOT release or return capacity when the request completes — admission is a rate reservation, not a concurrency slot | FR-009 |

**Error payload**: both exceptions carry `waited: float`, the actual seconds
spent queued. `HostawayRateLimitWaitTimeout`'s message MUST name rate limiting
as the cause in operator-readable terms (FR-020: "explicit, actionable error
identifying rate limiting as the cause").

### `note_rate_limited(applied_counter, retry_at, ..., limit, remaining) -> None`

`applied_counter` is Hostaway's documented `X-RateLimit-Applied` value
(`endpoint`, `account`, `ip`, or `provider`) when present. `retry_at` is the
`X-RateLimit-Retry-After` Unix timestamp when present. `limit` and `remaining`
come from `X-RateLimit-Limit` and `X-RateLimit-Remaining`. `method` and
`path` are used to find the affected endpoint bucket when
`applied_counter == "endpoint"` and to find the conservative fallback gates
when the value is absent or unknown.

| Behaviour | Requirement |
|---|---|
| MUST convert `retry_at` from a Unix timestamp to a delay relative to current wall-clock time before applying it | FR-015 |
| MUST apply the **full** converted delay to the gate's suppression deadline, uncapped by `MAX_BACKOFF` | FR-015, SC-007 |
| MUST reject a header that is non-numeric, not in the future, or more than one hour ahead, falling back to `10.0` seconds | FR-015 |
| MUST allow the retry layer's own sleep to stay bounded by `MAX_BACKOFF`, re-acquiring and re-waiting if it wakes before the deadline | FR-013, FR-015 |
| With no usable timestamp, MUST suppress for `DEFAULT_SUPPRESSION_SECONDS` (`10.0`) | FR-015 |
| MUST use `applied_counter` to suppress the affected gate: account, shared IP, endpoint bucket, or provider-global | FR-014 |
| MUST hold provider-global suppression in process-wide shared state, not on one account limiter | FR-014 |
| MUST fall back conservatively to all gates selected for the request when the applied counter is missing or unknown | FR-014 |
| MUST lower only the affected gate's runtime ceiling when `limit` is usable, using `min(configured_budget, limit)` | FR-014 |
| MUST NOT let server-reported `limit` raise capacity above the operator-configured budget | FR-014, FR-032 |
| MUST reconcile `remaining` only downward by adding synthetic in-window admissions when local availability is higher than the server-reported value | FR-014 |
| MUST NOT remove local admissions or raise the runtime ceiling in response to larger `remaining` or `limit` values, preventing oscillation | FR-014 |
| MUST extend, never shorten, an active suppression | concurrency safety |
| MUST re-pump all limiters waiting on a shared gate or provider suppression whose state changes or expires | FR-014 |
| MUST clear automatically on expiry with no reload or restart | FR-017, SC-007 |
| MUST increment `rate_limited_total` and the applied-counter count when known | FR-029 |

### `note_shed() -> None`

Called by a coordinator that has decided to shed. Increments the integration-wide
`shed_total` for diagnostics (FR-029). Does not affect admission.

### `configure(*, account_budget: int, effective_ip_budget: int) -> None`

| Behaviour | Requirement |
|---|---|
| MUST raise `ValueError` for either budget `< 1` or `> 200` | FR-032 |
| MUST apply the effective account budget, computed as the minimum across active entries sharing the account key, to this account gate | FR-005 |
| MUST apply the process-wide minimum to the shared IP gate | FR-006 |
| MUST preserve a reloading entry's prior contribution during the unload half of reload until setup replaces it | FR-006, SC-012 |
| MUST NOT clear any in-window admission record | FR-008, SC-012 |
| MUST re-pump all queues waiting on changed gates | FR-006 |
| MUST be idempotent for unchanged values | reload safety |

### `snapshot() -> LimiterSnapshot`

Pure read. MUST prune each selected gate's window first so per-gate
`admitted_in_window` values are accurate. MUST NOT contain `account_key` or any
credential (FR-029).

---

## 2. Request classifier and `BudgetGate` contract (FR-007, SC-014)

```python
class BudgetGate(Protocol):
    name: str
    def capacity_available(self, now: float) -> bool: ...
    def record(self, now: float) -> None: ...
    def next_available(self, now: float) -> float | None: ...
    def suppress_until(self, until: float) -> None: ...

class BucketId(StrEnum):
    ACCOUNT_GENERAL = "account_general"
    IP_GENERAL = "ip_general"
    CONVERSATION_MESSAGES = "conversation_messages"
    PRICE_DETAILS = "price_details"
    CREATE_RESERVATION = "create_reservation"

# Pure, stateless, and testable on its own: maps a request to *symbolic*
# bucket identifiers. It deliberately does NOT return gate objects, because
# the account-general gate belongs to one AccountRateLimiter while the IP
# gate is process-wide; only the limiter can resolve a symbol to the right
# instance, and doing so here would require hidden global state.
def classify_request(method: str, path: str) -> tuple[BucketId, ...]: ...

class AccountRateLimiter:
    # Instance operation: resolves symbols against this limiter's own gates
    # plus the shared process-wide gates.
    def _gates_for(self, method: str, path: str) -> tuple[BudgetGate, ...]: ...
```

| Behaviour | Requirement |
|---|---|
| All methods MUST be synchronous and non-blocking | R-012 |
| `capacity_available` and `next_available` MUST be logically read-only; they may discard expired local timestamps but must not change effective capacity or block | correctness of the conjunction |
| `classify_request` MUST be pure and return symbolic bucket identifiers, never gate instances | correctness with multiple accounts |
| `_gates_for` MUST resolve symbols against the caller's own account gates and the shared process-wide gates | FR-005, FR-006 |
| General requests MUST select account-general and IP-general gates | FR-007 |
| Documented endpoint-specific requests MUST select their endpoint bucket instead of the general gates | FR-007 |
| Admission MUST be the conjunction of selected gates | FR-007 |
| `record` MUST be invoked on every selected gate, only after all selected gates assented | FR-007 |

**SC-014 acceptance shape** — the demonstration test:

```text
Given  a general GET /v1/listings request
When   it is admitted
Then   account-general and IP-general utilization both increment

Given  a hypothetical POST /v1/reservations request with its documented bucket
When   it is admitted
Then   only that endpoint-specific account bucket increments
And    account-general and IP-general utilization do not change
And    zero lines of coordinator.py, services/ or public API methods differ
```

---

## 3. `RequestContext` — the priority-propagation contract

```python
def request_context(ctx: RequestContext) -> AbstractContextManager[None]: ...
def current_request_context() -> RequestContext: ...

def start_interactive_context(now: float) -> RequestContext: ...
def start_scheduled_context(now: float) -> RequestContext: ...
def start_first_refresh_context(now: float) -> RequestContext: ...
```

| Behaviour | Requirement |
|---|---|
| The default, with no context manager active, MUST start a scheduled context at acquisition time | safe default (R-004) |
| The deadline MUST be created once at logical operation entry and reused by every acquisition and retry sleep | FR-020, FR-021 |
| The context MUST propagate into tasks created inside it | fan-out coverage |
| The context MUST be restored on exit, including on exception | no cross-talk |
| Adding a new `HostawayApiClient` method MUST require no context code and MUST still be limited | FR-002, SC-009 |
| Adding a new entry to `SERVICE_DEFINITIONS` MUST make it interactive with no per-service code | FR-018 |

**SC-009 acceptance shape**:

```text
Given  a method added to HostawayApiClient that calls self._request
And    no limiter-specific code anywhere in that method
When   the method is invoked against a saturated limiter
Then   its request is not sent until budget is available
```

---

## 4. `HostawayApiClient` — the chokepoint contract

| Behaviour | Requirement |
|---|---|
| MUST acquire budget inside the retry loop, immediately before `self._http.request(...)`, passing method and path for classification | FR-001, FR-002, FR-004 |
| Retry backoff sleeps MUST be capped by the ambient operation deadline's remaining time | FR-020, FR-021 |
| Each retry iteration MUST perform a fresh acquisition — a 3-retry sequence consumes **4** admissions | FR-012, SC-010 |
| The recursive re-entry from `_handle_forbidden_response` MUST re-acquire | FR-012 |
| MUST call `limiter.note_rate_limited(...)` in the `429` branch before deciding to retry or raise | FR-014 |
| MUST NOT wrap, subclass, replace, or reconfigure the injected `httpx.AsyncClient` | FR-003 |
| MUST preserve the existing retry constants, jitter, and backoff curve unchanged while correcting Hostaway header parsing | FR-013 |
| The limiter MUST be optional in the constructor (`limiter: AccountRateLimiter \| None = None`), defaulting to unlimited, so existing unit tests construct a client without one | SC-011 |

**Logging** (FR-027, FR-028):

| Event | Level | Must be distinguishable from |
|---|---|---|
| Admission with wait duration, priority class, remaining budget | `DEBUG` | — |
| 429 received despite proactive limiting | `WARNING` | a proactive shed — the message MUST make clear this was **server pushback**, not self-limiting |

---

## 5. `HostawayTokenManager` — the second enforcement point

| Behaviour | Requirement |
|---|---|
| `_request_token` MUST acquire budget before `self._http.post(...)` using the token endpoint classification | FR-001 |
| The token acquisition and its post-generation delay MUST consume the ambient operation deadline when one exists | FR-020 |
| MUST call `note_rate_limited(...)` in its own `429` branch | FR-014 |
| The limiter MUST be optional in the constructor, defaulting to unlimited | SC-011 |
| The acquisition MUST inherit the ambient priority — a token fetched for an interactive call is interactive | FR-018 |

**Assumption marker (OQ-001)**: the module docstring and the acquisition site
MUST carry a comment stating that counting token requests is a *conservative
assumption*, not documented Hostaway behaviour, and naming OQ-001. It MUST NOT
be written as fact.

---

## 6. Coordinator shed contract

| Behaviour | Requirement |
|---|---|
| A shed MUST be performed by `return self.data` from `_async_update_data` | FR-023, OQ-006 |
| A shed MUST NOT call `async_set_updated_data` | FR-023, SC-016 |
| A shed MUST leave `update_interval`, `_unsub_refresh`, and `_debounced_refresh` untouched | SC-016 |
| A shed MUST leave `last_update_success` `True` and entities available | FR-023, SC-005 |
| A shed MUST NOT publish a partial dataset | FR-022 |
| A shed MUST NOT be retried immediately or accumulate backlog — the next attempt is the next scheduled interval | FR-024 |
| Listings and reservations first refreshes MUST NEVER be shed; they use the first-refresh context and failure propagates through `ConfigEntryNotReady` | FR-037, SC-015 |
| Custom-fields first refresh MUST remain non-blocking during setup, use a first-refresh context, and leave a not-yet-loaded state on timeout/failure | FR-037, SC-015 |
| First-refresh detection MUST use an explicit flag, never `self.data is None` | FR-037, R-007 |
| `HostawayCustomFieldsCoordinator` MUST re-raise `HostawayRateLimitShedError` ahead of its bare `except Exception` | FR-023 |

**SC-005 acceptance shape**:

```text
Given  a coordinator with prior data D and a saturated limiter
When   a scheduled refresh comes due and is shed
Then   coordinator.data == D
And    coordinator.last_update_success is True
And    no entity reports unavailable
And    a WARNING naming the coordinator and rate limiting was logged
```

**SC-016 acceptance shape**:

```text
Given  interval I and debounce handle H captured before a shed
When   the shed completes
Then   coordinator.update_interval == I
And    coordinator._debounced_refresh is H
```

### Shed logging contract (FR-025, FR-026, SC-006)

| Condition | Level | Content |
|---|---|---|
| First shed, or `_SHED_LOG_COOLDOWN_SECONDS` elapsed since the last WARNING | `WARNING` | coordinator name, "rate limiting" as cause, count since the previous summary |
| Within the cooldown | `DEBUG` | same detail |

MUST remain visible under sustained shedding. Because summaries are emitted
opportunistically on shed attempts rather than by a dedicated timer, SC-006
allows the summary interval to be 300.0 seconds plus one coordinator interval.

---

## 7. Options-flow contract (operator-facing)

Step: `options.step.init` in `custom_components/hostaway/config_flow.py`.

| Field | Type | Default | Bounds | Placement |
|---|---|---|---|---|
| `rate_limit_budget` | `int` | `180` | `1 <= v <= 200` | collapsed `section` named `advanced` |

| Behaviour | Requirement |
|---|---|
| MUST default to `180` | FR-010, SC-017 |
| MUST accept `200` | SC-017 |
| MUST **reject** `201` and above with a validation error — never clamp, never accept | FR-032, SC-017 |
| MUST reject `0` and negatives | FR-032 |
| MUST appear only in the options flow, never in the setup flow | FR-031 |
| MUST NOT require a config entry migration; an absent key reads as `180` | FR-033 |
| MUST NOT expose the 10-second window length | FR-034 |
| MUST flatten the section value back into a flat `options` dict | R-013 |
| MUST NOT move any existing flat option into the section | stored-options compatibility |
| MUST preserve the stored value when the section is not submitted | R-013 |

**New translation keys** in `strings.json` and `translations/en.json`:

| Key | Purpose |
|---|---|
| `options.step.init.sections.advanced.name` | section label |
| `options.step.init.sections.advanced.data.rate_limit_budget` | field label |
| `options.step.init.sections.advanced.data_description.rate_limit_budget` | explains 180 is a deliberate safety margin below Hostaway's 200 ceiling, not a Hostaway value (FR-010) |
| `options.error.invalid_rate_limit_budget` | rejection message naming the 1–200 bound |

**`show_advanced_options` MUST NOT be used.** Verified against installed Home
Assistant 2026.9.4: it is deprecated with `breaks_in_ha_version="2027.6"` and
its body unconditionally returns `True`, so it hides nothing (R-013).

---

## 8. Diagnostics contract (operator-facing)

New module: `custom_components/hostaway/diagnostics.py`, exposing
`async_get_config_entry_diagnostics(hass, entry) -> dict[str, Any]`. No
`manifest.json` change is required; Home Assistant discovers the module.

```json
{
  "rate_limit": {
    "limiter_label": "3f9c2a7b1d04",
    "budget_source": "option",
    "gates": {
      "account": {
        "budget": 180,
        "effective_budget": 180,
        "window_seconds": 10.0,
        "admitted_in_window": 17,
        "waiting_interactive": 0,
        "waiting_scheduled": 1,
        "suppressed": false,
        "suppressed_for_seconds": null
      },
      "ip": {
        "budget": 50,
        "effective_budget": 50,
        "window_seconds": 10.0,
        "admitted_in_window": 42,
        "waiting_interactive": 0,
        "waiting_scheduled": 3,
        "suppressed": true,
        "suppressed_for_seconds": 4.2
      }
    },
    "admitted_total": 10431,
    "rate_limited_total": 2,
    "rate_limited_by_counter": {
      "account": 1,
      "ip": 0,
      "endpoint": 0,
      "provider": 1,
      "unknown": 0
    },
    "shed_total": 7,
    "shed_by_coordinator": {
      "listings": 0,
      "reservations": 7,
      "custom_fields": 0
    }
  }
}
```

| Behaviour | Requirement |
|---|---|
| MUST include per-gate budget, current window utilization, waiting counts, and suppression state | FR-029, SC-013 |
| MUST include `rate_limited_total` and `rate_limited_by_counter` broken down by applied counter (`account`, `ip`, `endpoint`, `provider`, and `unknown`) | FR-029, SC-013 |
| MUST NOT include `client_id`, `client_secret`, or any token | FR-029, SC-013, Constitution X |
| `limiter_label` MUST be an opaque random identifier allocated at limiter creation, **not derived from the account key** | FR-029 |
| `budget_source` MUST distinguish `"default"` from `"option"` so an operator can see whether they changed it | diagnostic usefulness |

**SC-013 acceptance shape**:

```text
Given  a configured entry
When   diagnostics are downloaded
Then   the payload contains rate_limit.gates.account and rate_limit.gates.ip
And    those gate objects expose budget, admitted_in_window, waiting counts,
       suppressed, and suppressed_for_seconds independently
And    tests can create diverged account/IP state visible in the payload
And    no substring of client_id or client_secret appears anywhere in the
       serialized payload
```

---

## 9. Backward-compatibility contract (SC-011)

| Guarantee |
|---|
| All 493 existing tests MUST continue to pass; the suite may only grow |
| `HostawayApiClient` and `HostawayTokenManager` MUST remain constructible without a limiter |
| No coordinator, service, or sensor behaviour changes for an installation operating below budget |
| `custom_components/hostaway/sensor/listing.py` MUST NOT be modified |
| No config entry migration; no `CHANGELOG.md` (the repository deliberately has none — release notes are generated) |
| `ruff check custom_components/ tests/` and `mypy custom_components/` MUST remain clean |
