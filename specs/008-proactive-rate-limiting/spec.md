<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Proactive Hostaway API Rate Limiting

**Feature Branch**: `008-proactive-rate-limiting`
**Created**: 2026-10-08
**Status**: Ready for planning
**Input**: User description: "Add integration-wide proactive Hostaway API rate
limiting through a shared limiter honored by all coordinators and API paths
(issue #213)."

## Overview

Hostaway enforces a published rate limit of **200 requests per 10 seconds, per
account and per IP address**. The integration today has **no proactive
limiter**. It stays under budget by design — conservative default scan
intervals and a small number of coordinators — and relies entirely on
**reactive** handling after the fact: `custom_components/hostaway/api/retry.py`
backs off on HTTP 429 and 5xx responses, honoring `Retry-After`, with
`MAX_RETRIES = 3`, `MAX_BACKOFF = 30.0` seconds, and jittered delay.

"Under budget by design" is a weaker guarantee than it sounds. The reservations
coordinator iterates **per selected listing**, calling
`api_client.get_all_reservations(listing_id)`, which itself paginates at
`DEFAULT_PAGE_LIMIT = 100`. A single reservations refresh therefore costs
roughly `N selected listings x pages per listing` requests, not one. With
`MIN_SCAN_INTERVAL` at 1 minute, an operator with a large portfolio can
configure a burst that comfortably exceeds 200 requests inside a 10-second
window without doing anything the integration considers unusual.

Feature 007 (custom field support) raised the floor further: it added the
custom-field definitions coordinator, and every custom-field write is a
**read -> write -> read-back** sequence of three requests, because FR-056 of
that feature mandates post-write read-back verification. On top of that,
user-triggered services (`set_custom_field`, `get_custom_fields`,
`get_custom_field_values`, `set_door_code`, `get_reservations`) fire
concurrently with scheduled refreshes and have no coordination with them.

This feature introduces an integration-wide proactive request budget that
**all** Hostaway traffic from this integration passes through, so that
concurrent coordinators and user-triggered services share one accounting of
the same limit instead of each independently assuming it has headroom.

### Chokepoint

All Hostaway HTTP traffic already flows through a single method,
`HostawayApiClient._request` in `custom_components/hostaway/api/client.py`.
That is the natural and only enforcement point. The client uses Home
Assistant's shared httpx client via
`homeassistant.helpers.httpx_client.get_async_client(hass)` and never
constructs its own `AsyncClient`. Because that transport is **shared with
other integrations**, the limiter MUST be scoped to this integration's own
requests and MUST NOT be implemented as a transport-level or client-level
wrapper that could throttle unrelated integrations' traffic.

### Reactive and proactive must compose, not fight

The existing retry logic is not being replaced. A proactive limiter reduces the
frequency of 429s; it does not make them impossible, because the per-IP budget
is shared with anything else on the host talking to Hostaway, and because the
exact server-side window semantics are not fully documented. The two mechanisms
must be layered deliberately: the limiter gates admission, the retry layer
handles what still gets rejected, and a 429 is treated as **evidence** that
feeds back into the limiter rather than as an isolated event the limiter never
learns about.

### Deliberate scope limitation: per-account only, per-IP left open

Hostaway limits per account **and** per IP. This feature implements **per
account only**. This is an explicit owner decision, recorded here as a known
limitation rather than an oversight.

**The consequence, stated plainly:** if two config entries for *different*
Hostaway accounts are configured on the same Home Assistant host, each will
track its own 200/10s budget and each will believe it is compliant, while
jointly presenting up to 400 requests per 10 seconds from a single IP to
Hostaway — exceeding the per-IP budget. The owner states they will never run
two Hostaway accounts on one host, which is why this is acceptable today.

The design MUST NOT paint itself into a corner. A future per-IP (process-wide)
dimension must be addable without rewriting the limiter or re-plumbing every
call site. See FR-006 and FR-007.

## Clarifications

### Session 2026-10-08

- Q: What is the right default budget value below the 200 ceiling (OQ-002)?
  → A: 180 requests per rolling 10-second window — roughly 10% headroom below
  the documented 200/10s ceiling, to absorb clock skew and in-flight requests
  the limiter cannot observe.
- Q: Should the budget be operator-configurable at all (OQ-005)? → A: Yes, via
  the options flow only, defaulting to 180 and hard-bounded to a maximum of
  200. It is the only lever available to an operator sharing an IP, which is
  why it is exposed despite the misconfiguration risk.
- Q: What is the right bounded maximum wait for interactive requests (OQ-003)?
  → A: 30 seconds. It comfortably covers a 3-request custom-field write
  (read → write → read-back, mandated by feature 007 FR-056) under saturation,
  while staying well below Home Assistant's 60-second service-call timeout so
  the call never appears hung.
- Q: What is the grace period before a scheduled cycle is shed (OQ-004)? → A:
  A short wait of approximately 2 seconds, not zero. A poll arriving moments
  before the sliding window frees capacity would otherwise shed needlessly.
- Q: Does Hostaway's authentication/token endpoint count against the same
  200/10s budget (OQ-001)? → A: Still unverified; remains a recorded
  assumption. The conservative assumption that it **does** count stands and
  must not be presented as documented Hostaway behaviour.
- Q: Which Home Assistant mechanism cleanly expresses "skip this refresh
  without signalling failure" (OQ-006)? → A: Return the coordinator's existing
  `self.data` from `_async_update_data`. Not `async_set_updated_data`, which
  resets the refresh interval and cancels the debounced refresh. Because
  `self.data` is initialised to `None`, the **first** refresh for a config
  entry must never be shed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Large portfolio stays under the Hostaway limit (Priority: P1)

An operator with many selected listings and short scan intervals configures the
integration. A reservations refresh fans out to many paginated requests. Today
that burst can exceed 200 requests in 10 seconds and Hostaway starts rejecting
traffic; the integration discovers this only after being told "no". With a
proactive limiter, the burst is paced so the account budget is never knowingly
exceeded in the first place.

**Why this priority**: This is the core problem in issue #213. Without it,
nothing else in this feature matters.

**Independent Test**: Configure a simulated account whose refresh cycle demands
more requests than the budget allows within one window; observe that outbound
request timestamps never exceed the configured budget inside any 10-second
window, with no 429 responses required to achieve that.

**Acceptance Scenarios**:

1. **Given** a refresh cycle that would issue 350 requests back to back,
   **When** the cycle runs, **Then** no 10-second window contains more
   admitted requests than the configured budget.
2. **Given** two coordinators refreshing simultaneously, **When** both request
   budget, **Then** their combined admitted request rate — not each one
   individually — is what is held under the budget.
3. **Given** the budget is fully consumed, **When** another scheduled request
   is attempted, **Then** it is not sent immediately and the caller is told
   the budget is unavailable rather than silently blocking forever.

---

### User Story 2 - Saturated budget sheds polls (Priority: P1)

The budget is exhausted. A scheduled coordinator refresh comes due. Rather than
queueing that cycle behind an unbounded backlog — which would make the
integration progressively later and later and eventually stampede — the cycle
is **skipped** and the skip is logged. The next scheduled cycle tries again
with a fresh budget.

**Why this priority**: Unbounded queueing is the failure mode that turns a
transient overload into a permanent one. Shedding scheduled load is what keeps
the integration recoverable, and it is the owner's explicit decision.

**Independent Test**: Saturate the budget, let a scheduled refresh come due,
and verify the cycle is skipped, a log record is emitted, previously-fetched
data remains available, and entities do **not** go unavailable.

**Acceptance Scenarios**:

1. **Given** an exhausted budget, **When** a scheduled coordinator refresh is
   due, **Then** the cycle is skipped after a short grace wait and a
   warning-level log record naming the coordinator is emitted.
2. **Given** a skipped cycle, **When** an operator inspects the entities fed by
   that coordinator, **Then** they still hold their last known values and are
   **not** marked unavailable, because the data is stale rather than wrong.
3. **Given** a skipped cycle, **When** the next scheduled interval arrives and
   budget is available, **Then** the refresh proceeds normally with no manual
   intervention and no accumulated backlog of skipped cycles.
4. **Given** repeated consecutive skips, **When** they continue, **Then** the
   log does not drown the operator in identical lines yet still makes the
   ongoing condition visible.
5. **Given** a saturated budget at config entry setup, **When** the
   coordinator's **first** refresh runs, **Then** it is **not** shed — it
   waits for budget, or fails in a way Home Assistant retries as a normal
   setup failure — because shedding it would publish `None` as coordinator
   data.
6. **Given** a scheduled cycle is shed, **When** the skip is performed,
   **Then** it is performed by returning the coordinator's existing data from
   its update path, and the coordinator's refresh interval and debounce timer
   are left untouched.

---

### User Story 3 - A user write is never starved by a poll (Priority: P1)

An automation calls `hostaway.set_custom_field` at the same moment a large
reservations poll is consuming the budget. The service call must complete. It
is a three-request read -> write -> read-back sequence, and an interrupted one
cannot satisfy the FR-056 read-back verification that is what makes the write
safe in the first place. The interactive call therefore takes priority over the
scheduled poll.

**Why this priority**: A write that reports success without its read-back
verification is a correctness failure, not a performance one.

**Independent Test**: Start a budget-consuming poll, issue a service call
mid-flight, and verify the service call's requests are admitted ahead of the
poll's remaining requests and the service completes successfully.

**Acceptance Scenarios**:

1. **Given** a scheduled poll and an interactive service call both waiting for
   budget, **When** budget becomes available, **Then** the interactive call is
   served first.
2. **Given** an exhausted budget, **When** an interactive service call needs
   budget, **Then** it **waits** for budget rather than being shed, up to a
   bounded timeout of 30 seconds.
3. **Given** an in-flight custom-field write, **When** the budget saturates
   between the write and its read-back, **Then** the read-back still completes
   within the interactive call's 30-second bounded wait, so the write's success
   report remains honest.
4. **Given** an interactive call that exceeds its 30-second bounded wait,
   **When** the timeout expires, **Then** the service raises a clear,
   actionable error naming rate limiting as the cause rather than proceeding
   anyway or reporting a false success.

---

### User Story 4 - An operator can see that the limiter is working (Priority: P2)

Entities look slightly stale. The operator needs to distinguish "Hostaway is
down", "my credentials broke", and "I configured intervals too aggressively for
my portfolio size and cycles are being shed". Each has a different fix.

**Why this priority**: Shed load that is invisible is indistinguishable from a
bug, and the owner requires skips to be observable, not silent.

**Independent Test**: Force skips and waits, then confirm the operator-facing
signals distinguish rate-limit-induced staleness from an outage or an auth
failure.

**Acceptance Scenarios**:

1. **Given** cycles have been skipped, **When** the operator reads the Home
   Assistant log at default verbosity, **Then** the skips are visible with the
   coordinator name and the reason.
2. **Given** debug logging is enabled, **When** the operator reproduces the
   condition, **Then** per-request admission detail including wait durations is
   available.
3. **Given** a 429 is received despite the limiter, **When** it is handled,
   **Then** it is logged distinctly from an ordinary skip so the operator can
   tell "we self-limited" from "Hostaway pushed back".

---

### User Story 5 - A 429 teaches the limiter something (Priority: P2)

Despite proactive limiting, Hostaway returns 429 — perhaps because another
process on the same IP consumed budget, or because the server's window
accounting does not match the client's. The limiter should respond by backing
off its own admission rate for a period, not by continuing to admit at full
rate while the retry layer absorbs the damage alone.

**Why this priority**: Without feedback, the limiter is an open-loop guess. With
it, the limiter degrades gracefully in exactly the situations its model is
wrong — including the per-IP case this feature does not otherwise cover.

**Independent Test**: Inject a 429 with and without `Retry-After`, and verify
that subsequent admissions are suppressed for the indicated or inferred period.

**Acceptance Scenarios**:

1. **Given** a 429 carrying `Retry-After`, **When** it is observed, **Then** no
   further requests are admitted until that interval has elapsed.
2. **Given** a 429 with no `Retry-After`, **When** it is observed, **Then**
   admission is suppressed for a bounded, conservative default interval.
3. **Given** a suppression period is active, **When** an interactive call
   arrives, **Then** it waits for the suppression to clear rather than bypassing
   it — the server's "no" outranks the interactive priority ordering.
4. **Given** suppression has elapsed with no further 429s, **When** normal
   operation resumes, **Then** the limiter returns to its configured budget
   without requiring a restart or reload.

---

### Edge Cases

- **A retry is a request.** When the retry layer re-issues a request after a
  429 or a 5xx, that re-issue consumes budget exactly as the original did.
  Retries must re-acquire budget, not reuse the original acquisition — see
  FR-012. If they did not, a 3-retry storm would triple actual traffic while
  the limiter reported compliance.
- **Multiple config entries, same account.** Two config entries using the same
  Hostaway credentials must share one budget, not get one each. See FR-005.
- **Multiple config entries, different accounts.** Each gets its own budget.
  This is the documented per-IP gap; see FR-006.
- **Config entry reload.** Reloading must not reset the budget to full and
  permit an immediate double-rate burst against a server that is still counting
  the prior window.
- **Home Assistant restart.** After a restart the limiter necessarily starts
  with no memory of pre-restart traffic. The startup burst must be bounded
  conservatively rather than assuming a clean slate.
- **Clock behavior.** The window must be measured on a monotonic basis so that
  a system clock adjustment cannot grant or withhold budget spuriously.
- **An interactive call that is itself enormous.** `get_reservations` invoked
  as a service can be as large as a poll. Interactive priority must not let a
  single huge interactive call monopolize the budget indefinitely at the cost
  of every poll being shed forever. See FR-020.
- **Cancellation.** A waiter cancelled while queued (service call cancelled,
  entry unloaded) must release its claim so the budget is not leaked.
- **Budget larger than demand.** In the common small-portfolio case the limiter
  must add no measurable latency; it must not become a throughput tax on
  installations that were never near the limit.
- **Startup.** The initial refresh of all coordinators plus listing discovery
  happens close together at setup. That burst passes through the same limiter.
- **The first refresh is not sheddable.** A `DataUpdateCoordinator` starts
  with `self.data` set to `None`, so a shed that returns existing data would
  publish `None` at setup and corrupt entity state. The first refresh for a
  config entry must wait for budget or fail outright so Home Assistant can
  retry setup. See FR-037.

## Requirements *(mandatory)*

### Functional Requirements

#### Coverage and enforcement point

- **FR-001**: Every HTTP request this integration issues to the Hostaway API
  MUST acquire rate-limit budget before being sent. This includes scheduled
  coordinator refreshes, paginated continuation requests, user-triggered
  service calls, authentication/token requests, connection tests performed
  during config flow, and retry re-issues.
- **FR-002**: Enforcement MUST occur at the integration's single HTTP
  chokepoint (`HostawayApiClient._request`) so that no present or future API
  method can bypass the limiter by construction. Adding a new API method MUST
  NOT require the author to remember to call the limiter.
- **FR-003**: The limiter MUST NOT be implemented by wrapping, replacing, or
  configuring the Home Assistant shared httpx client returned by
  `get_async_client(hass)`, because that client is shared with other
  integrations. Only this integration's own requests may be throttled.
- **FR-004**: A request that cannot be admitted MUST NOT be sent. The limiter
  MUST NOT rely on the server rejecting over-budget traffic.

#### Scope and keying

- **FR-005**: The limiter MUST be keyed by Hostaway **account identity**, so
  that multiple config entries representing the same account share exactly one
  budget. The account identity MUST be derived from the configured Hostaway
  account credential (`CONF_CLIENT_ID`, which is the Hostaway account id), not
  from the config entry id, title, or object identity.
- **FR-006**: Per-IP limiting is explicitly **out of scope** for this feature.
  The specification MUST document that two config entries for different
  accounts on the same host can jointly exceed Hostaway's per-IP budget while
  each believes itself compliant, and that this is an accepted limitation
  because the owner will not run multiple accounts on one host.
- **FR-007**: The limiter MUST be structured so that an additional
  process-wide (per-IP) budget dimension can be introduced later by composing
  an extra gate at the same enforcement point, **without** changing API client
  call sites, coordinator code, or service code. A request admission MUST be
  expressible as the conjunction of one or more independent budget gates.
- **FR-008**: Limiter state MUST be held for the lifetime of the account's
  presence in the running Home Assistant instance and MUST survive a config
  entry reload, so that a reload cannot reset the window and permit a burst.
  State MUST be released when the last config entry for that account is
  removed.

#### Window semantics

- **FR-009**: The limiter MUST treat Hostaway's limit as **200 requests per
  rolling 10-second window** and MUST enforce it as a **sliding** window. A
  fixed/tumbling window interpretation is forbidden, because it permits up to
  2x the limit across a window boundary and Hostaway's actual accounting is not
  documented in enough detail to rule that out.
- **FR-010**: The default configured budget MUST be **180 requests per rolling
  10-second window**, deliberately below the documented 200/10s ceiling. The
  ~10% headroom exists to absorb clock skew between the integration's window
  and Hostaway's server-side accounting, requests already in flight that the
  limiter can no longer withhold, and traffic the integration cannot see
  (other processes on the same IP, and the Hostaway dashboard or other clients
  using the same account). This default MUST be documented as a conservative
  safety-margin choice, not as a technical maximum or as a Hostaway-documented
  value.
- **FR-011**: The window MUST be measured using a monotonic time source so that
  system clock adjustments cannot grant or withhold budget.

#### Composition with reactive retry

- **FR-012**: Each retry attempt MUST independently acquire budget before being
  sent. A retry MUST NOT reuse the budget acquired by the attempt it is
  retrying.
- **FR-013**: The existing reactive retry behaviour (429 and 5xx backoff,
  `Retry-After` honoring, `MAX_RETRIES`, `MAX_BACKOFF`, jitter) MUST be
  preserved. This feature adds a layer; it does not remove one.
- **FR-014**: When a 429 is received, the limiter MUST enter a suppression
  state during which no further requests for that account are admitted.
- **FR-015**: If the 429 carries a `Retry-After` header, suppression MUST last
  at least that long, bounded by the same `MAX_BACKOFF` ceiling the retry layer
  applies. If it does not, a bounded conservative default suppression period
  MUST be used.
- **FR-016**: Suppression MUST apply to **all** callers including interactive
  ones. A server-issued `Retry-After` outranks the integration's internal
  priority ordering.
- **FR-017**: Suppression MUST clear automatically once elapsed, returning the
  limiter to its configured budget with no reload or restart required.

#### Priority, shedding, and queueing

- **FR-018**: Requests MUST carry a priority classification with at least two
  levels: **interactive** (user-triggered service calls and config-flow
  validation) and **scheduled** (coordinator refreshes and their paginated
  continuations).
- **FR-019**: When budget becomes available and both classes are waiting,
  **interactive** requests MUST be admitted before **scheduled** requests.
- **FR-020**: Interactive requests MUST wait for budget rather than being shed,
  subject to a bounded maximum wait of **30 seconds**. That bound is chosen to
  comfortably cover a three-request custom-field write (read → write →
  read-back, mandated by feature 007 FR-056) under a saturated budget, while
  staying well below Home Assistant's 60-second service-call timeout so the
  call never appears hung to the caller. Exceeding that wait MUST surface as an
  explicit, actionable error identifying rate limiting as the cause; the call
  MUST NOT proceed anyway and MUST NOT report success. The bounded wait also
  prevents a single very large interactive call from starving scheduled work
  indefinitely.
- **FR-021**: Scheduled requests MUST be **shed**, not queued indefinitely.
  When a scheduled coordinator refresh cannot obtain budget within a grace
  period of approximately **2 seconds**, the entire refresh cycle MUST be
  abandoned for that interval. The grace period is non-zero so that a poll
  arriving moments before the sliding window frees capacity is not shed
  needlessly. This shedding rule MUST NOT be applied to a coordinator's first
  refresh for a config entry, which is governed exclusively by FR-037.
- **FR-022**: Within a single refresh cycle, if budget is exhausted partway
  through a multi-request fan-out (for example, after fetching reservations for
  some but not all selected listings), the cycle MUST NOT publish a partial
  dataset that would make entities appear to have lost data. It MUST either
  abandon the cycle leaving prior data intact, or publish a result that
  preserves the last known values for the listings it could not reach. On a
  coordinator's **first** refresh there are no prior data to preserve, so
  FR-037 governs instead.
- **FR-023**: A shed cycle MUST NOT be reported to Home Assistant as a failed
  update, MUST NOT mark entities unavailable, and MUST NOT trigger the
  coordinator's failure/backoff path. The previously fetched data MUST remain
  the coordinator's current data, because it is stale rather than wrong. A shed
  MUST be expressed by returning the coordinator's existing `self.data` from
  `_async_update_data`, which leaves `last_update_success` true and republishes
  unchanged data. A shed MUST NOT be expressed via
  `DataUpdateCoordinator.async_set_updated_data`, because that method resets the
  refresh interval and cancels the debounced refresh, changing the
  coordinator's scheduling as a side effect of shedding.
- **FR-024**: A shed cycle MUST NOT be retried immediately or accumulate a
  backlog. The next attempt is the next normally scheduled interval.
- **FR-037**: A coordinator's **first** refresh for a config entry MUST NEVER
  be shed. Because `DataUpdateCoordinator.data` is `None` until the first
  successful update, shedding the first refresh would publish `None` as
  coordinator data and corrupt entity state at setup. The first refresh MUST
  instead either wait for budget to become available, or fail in a way that
  Home Assistant treats as a normal, retryable setup failure (the
  `ConfigEntryNotReady` path that `async_config_entry_first_refresh` already
  raises on failure). A first refresh MUST NOT return `None`, MUST NOT return
  an empty or partial dataset in place of real data, and MUST NOT be subject
  to the FR-021 grace-period shed.

#### Observability

- **FR-025**: Every shed scheduled cycle MUST emit a log record at
  warning level that names the coordinator and states rate limiting as the
  cause.
- **FR-026**: Repeated consecutive skips MUST be rate-limited in the log
  (for example, by logging the first occurrence and then periodic summaries)
  so the log remains usable, while the ongoing condition stays visible.
- **FR-027**: A 429 received despite proactive limiting MUST be logged
  distinguishably from a proactive skip, so an operator can tell self-limiting
  from server pushback.
- **FR-028**: At debug level, the limiter MUST log per-request admission
  decisions including the priority class, the wait duration incurred, and the
  remaining budget.
- **FR-029**: The limiter MUST expose, for diagnostics, the current account
  budget configuration, the number of requests admitted in the current window,
  the count of shed cycles, and whether suppression is active. This information
  MUST appear in the integration's diagnostics output and MUST NOT include
  credentials.

#### Configuration

- **FR-030**: The limiter MUST be enabled by default with no operator action
  required. Correct behaviour for a typical installation MUST NOT depend on
  configuration.
- **FR-031**: The requests-per-window budget MUST be operator-configurable, and
  MUST be exposed **only** through the config entry **options flow**
  (reconfigurable without removing the integration), never as a setup-time-only
  field. It is exposed because it is the only lever available to an operator
  whose IP is shared with other Hostaway traffic; the misconfiguration risk is
  accepted in exchange for that lever.
- **FR-032**: The requests-per-window budget option MUST default to **180** and
  MUST be hard-bounded to a maximum of **200**, Hostaway's documented ceiling.
  A configured value above 200 MUST be rejected by the options flow with a
  validation error; it MUST NOT be silently clamped or accepted. The option
  MUST also be bounded below by a value greater than zero so the integration
  cannot be configured into never issuing requests.
- **FR-033**: Existing config entries MUST continue to load without
  modification; this feature MUST NOT require a config entry migration or any
  operator reconfiguration.
- **FR-034**: The 10-second window length MUST NOT be operator-configurable, as
  it is a property of the Hostaway service rather than a tuning knob.

#### Documentation

- **FR-035**: User-facing documentation MUST state the per-account-only scope
  and its per-IP consequence in plain language, so an operator considering two
  accounts on one host is warned before they hit it.
- **FR-036**: Documentation MUST explain that stale-but-available entities can
  indicate shed cycles, and that the remedy is lengthening scan intervals or
  reducing selected listings rather than filing a bug.

### Key Entities

- **Request budget**: The admission allowance for one account — a maximum
  number of admitted requests within a rolling 10-second window (default 180,
  operator-configurable up to 200), plus the record of admissions within the
  current window.
- **Budget gate**: One independent admission condition. Today exactly one
  exists (per-account). The per-IP dimension of FR-007 would be a second. A
  request is admitted only when every gate admits it.
- **Account key**: The identity under which budget is shared, derived from the
  Hostaway account credential, so that multiple config entries for one account
  share one budget.
- **Priority class**: `interactive` or `scheduled`; determines both admission
  ordering (FR-019) and saturation behaviour — queue versus shed (FR-020,
  FR-021). A coordinator's first refresh is `scheduled` but is exempt from
  shedding (FR-037).
- **Suppression state**: A time-bounded period following an observed 429 during
  which no request for that account is admitted regardless of priority.
- **Shed record**: The observable fact that a named coordinator abandoned a
  scheduled cycle due to rate limiting, surfaced in logs and diagnostics.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Under a simulated workload demanding at least 2x the configured
  budget, **zero** 10-second sliding windows contain more admitted requests
  than the configured budget, measured across the entire run.
- **SC-002**: Under that same workload, the integration produces **zero** 429
  responses attributable to its own traffic, versus a measurable number of 429s
  for the same workload before this feature.
- **SC-003**: With two coordinators refreshing concurrently plus a concurrent
  service call, the **combined** admitted rate stays within a single shared
  budget; no two components are observed to each consume a full budget.
- **SC-004**: When the budget is saturated, an interactive
  `hostaway.set_custom_field` call completes its full read -> write -> read-back
  sequence, with the read-back verification required by feature 007 FR-056
  performed, in 100% of trials within the 30-second bounded interactive wait.
  When that 30-second bound is exceeded, 100% of trials raise an error naming
  rate limiting and zero trials report success.
- **SC-005**: When a scheduled cycle is shed, entities fed by that coordinator
  retain their prior values and report as available in 100% of trials; no
  "failed to update" is recorded.
- **SC-006**: Every shed cycle is discoverable from the Home Assistant log at
  default verbosity, naming the coordinator and the cause, within one log line
  per occurrence or one periodic summary during sustained shedding.
- **SC-007**: After an injected 429 with `Retry-After: 5`, no request is
  admitted for at least 5 seconds, and normal admission resumes automatically
  thereafter without a restart or reload.
- **SC-008**: For an installation whose demand is below budget — the typical
  small-portfolio case — the limiter adds no more than a negligible, bounded
  per-request overhead and introduces no deliberate delay to any request.
- **SC-009**: Adding a new method to `HostawayApiClient` without any
  limiter-specific code results in that method's traffic being rate limited,
  demonstrating FR-002 structurally rather than by convention.
- **SC-010**: A retry triggered by the existing retry layer is observed to
  consume budget separately from its original attempt, so a 3-retry sequence
  accounts for 4 requests in the budget, not 1.
- **SC-011**: The existing integration regression test suite passes unchanged
  apart from additions, and no existing coordinator or service behaviour
  changes for installations operating below budget.
- **SC-012**: A config entry reload does not increase the number of requests
  admissible in the 10 seconds spanning the reload.
- **SC-013**: Integration diagnostics output includes the limiter's budget,
  current window utilization, shed-cycle count, and suppression state, and
  contains no credentials.
- **SC-014**: Introducing a hypothetical second (per-IP) budget gate in a
  prototype requires changes only within the limiter and its single enforcement
  point — zero changes to coordinator, service, or API method code —
  demonstrating FR-007.
- **SC-015**: With the budget saturated at config entry setup, a coordinator's
  first refresh is never shed: across 100% of trials the coordinator's data is
  never observed to be `None` after setup reports success, and every trial
  ends either with real fetched data or with a `ConfigEntryNotReady`-style
  retryable setup failure — never with a successful setup publishing empty or
  `None` data.
- **SC-016**: A shed scheduled cycle leaves the coordinator's configured
  update interval and debounce timer unchanged, demonstrating that the shed
  was performed by returning existing data from `_async_update_data` rather
  than via `async_set_updated_data`.
- **SC-017**: The options flow defaults the requests-per-window budget to 180,
  accepts 200, and rejects 201 and any larger value with a validation error
  rather than clamping or accepting it, in 100% of trials.

## Assumptions

- **Documented**: Hostaway publishes a limit of 200 requests per 10 seconds,
  per account and per IP. This is treated as fact.
- **Assumption, not verified**: The precise server-side window algorithm
  (sliding versus fixed, and the exact accounting boundary) is **not**
  documented at a level that lets the integration match it. The spec therefore
  mandates the conservative interpretation (FR-009) and sub-ceiling default
  budget (FR-010). This is an assumption about our own safety margin, not a
  claim about Hostaway's implementation.
- **Assumption, not verified**: Hostaway returns HTTP 429 on limit violation
  and *may* include `Retry-After`. The existing retry layer already handles
  both the present and absent cases, so no new Hostaway behaviour is being
  assumed beyond what the code already tolerates.
- **Assumption**: `CONF_CLIENT_ID` uniquely identifies a Hostaway account and
  is therefore a sound budget key. Two config entries with the same client id
  address the same account and the same server-side budget.
- **Assumption, not verified**: Hostaway's token/authentication requests count
  against the same rate limit as data requests. This is **not** documented by
  Hostaway and has not been verified against the live service (OQ-001). It is
  recorded as an assumption, not as Hostaway behaviour. Counting them is the
  conservative choice; if they are in fact exempt, the only cost is slightly
  more headroom than necessary.
- **Verified against the installed Home Assistant source**: a refresh can
  decline to update without signalling failure by returning the coordinator's
  existing `self.data` from `_async_update_data`, which leaves
  `last_update_success` true and keeps entities available (FR-023). The same
  source shows `DataUpdateCoordinator.data` is initialised to `None` and that
  `async_config_entry_first_refresh` raises `ConfigEntryNotReady` when the
  first refresh fails — which is why the first refresh is never sheddable
  (FR-037). `async_set_updated_data` is not a valid shed mechanism because it
  resets the refresh interval and cancels the debounced refresh.
- The owner will not operate two Hostaway accounts on a single Home Assistant
  host, which is what makes the per-account-only scope acceptable (FR-006).
- Other processes on the same IP — the Hostaway web dashboard, other
  integrations, scripts — may consume part of the per-IP budget invisibly. The
  headroom in FR-010 and the 429 feedback loop in FR-014 through FR-017 are the
  mitigations for traffic the integration cannot see.
- Home Assistant coordinator semantics permit a refresh to decline to update
  without signalling failure, which is what FR-023 depends on. The exact
  mechanism has now been determined and is recorded above and in FR-023.
- No config entry migration is needed; all new settings are optional options
  with defaults (FR-033).

## Out of Scope

- Per-IP / process-wide rate limiting. Deliberately deferred; a clean seam is
  required (FR-007) but no implementation.
- Persisting limiter state across Home Assistant restarts.
- Replacing or redesigning the existing reactive retry layer.
- Request coalescing, response caching, or de-duplication of overlapping
  requests. These would reduce request volume but are a different feature.
- Changing default scan intervals, `MIN_SCAN_INTERVAL`, or
  `DEFAULT_PAGE_LIMIT`.
- Adaptive discovery of the server's real limit by probing it.
- Rate limiting any non-Hostaway traffic.

## Open Questions

One question remains genuinely unresolved and is flagged rather than guessed.
The other five were resolved in the 2026-10-08 clarification session and are
retained below as a decision record. Resolutions are recorded in
[Clarifications](#clarifications) and encoded in the requirements they bound.

### Outstanding

- **OQ-001** *(open)*: Does Hostaway's authentication/token endpoint count
  against the same 200/10s budget as data endpoints? Unverified by Hostaway.
  The spec assumes **yes** (conservative) and that assumption is recorded as
  an assumption, not as documented behaviour. If verification shows otherwise,
  FR-001's inclusion of token requests can be relaxed; the conservative
  default stands until then. This does not block planning or implementation —
  the conservative path is already correct under either answer.

### Resolved

- **OQ-002** *(resolved)*: Default budget — **180 requests per rolling
  10-second window**, ~10% headroom below the documented 200/10s ceiling for
  clock skew and in-flight requests the limiter cannot observe. Encoded in
  FR-010.
- **OQ-003** *(resolved)*: Bounded maximum interactive wait — **30 seconds**,
  long enough for a three-request custom-field write under saturation and well
  below Home Assistant's 60-second service-call timeout. Encoded in FR-020 and
  SC-004.
- **OQ-004** *(resolved)*: Scheduled-shed grace period — a short wait of
  approximately **2 seconds**, not zero, so a poll arriving moments before the
  window frees capacity is not shed needlessly. Encoded in FR-021.
- **OQ-005** *(resolved)*: The budget **is** operator-configurable, through the
  options flow only, defaulting to 180 and hard-bounded at 200. Encoded in
  FR-031, FR-032 and SC-017.
- **OQ-006** *(resolved)*: A shed is expressed by returning the coordinator's
  existing `self.data` from `_async_update_data`; `async_set_updated_data` is
  **not** suitable because it resets the refresh interval and cancels the
  debounced refresh. Because `data` starts as `None` and
  `async_config_entry_first_refresh` raises `ConfigEntryNotReady` on failure,
  the first refresh is never sheddable. Encoded in FR-023, FR-037, SC-015 and
  SC-016.

## References

- GitHub issue [#213](https://github.com/tykeal/homeassistant-hostaway/issues/213)
  — Add proactive Hostaway API rate limiting (the issue this feature
  implements).
- GitHub issue #198 / #195 — where the need was surfaced during feature 007
  review.
- `specs/007-custom-field-support/spec.md` — FR-056 post-write read-back
  verification, the requirement that makes interactive write completion a
  correctness concern (User Story 3, SC-004).
- `custom_components/hostaway/api/client.py` — `HostawayApiClient._request`,
  the enforcement chokepoint.
- `custom_components/hostaway/api/retry.py` — existing reactive 429/5xx
  handling that this feature composes with.
- `custom_components/hostaway/coordinator.py` — the three coordinators subject
  to shedding.
- `homeassistant/helpers/update_coordinator.py` (installed Home Assistant) —
  `DataUpdateCoordinator.data` initialised to `None`,
  `async_config_entry_first_refresh` raising `ConfigEntryNotReady` on failure,
  and `async_set_updated_data` resetting the refresh interval and cancelling
  the debounced refresh. The basis for FR-023 and FR-037.
