<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 -->

# Feature Specification: Proactive Hostaway API Rate Limiting

**Feature Branch**: `008-proactive-rate-limiting`
**Created**: 2026-10-08
**Status**: Ready for planning
**Input**: User description: "Add integration-wide proactive Hostaway API rate
limiting through a shared limiter honored by all coordinators and API paths
(issue #213)."

## Overview

Hostaway documents a **multi-counter** Public API rate-limit model at
<https://api.hostaway.com/documentation>, verified against the live API
reference on 2026-10-08. The documentation says each limit has its own
counter, and that an endpoint with its own limit does not use the general
limit. General endpoint calls count against **both** the account counter and
the IP counter.

| Maximum requests | Time frame | Applies to | Key | Certainty |
| --- | --- | --- | --- | --- |
| 30 | 1 minute | `POST /v1/conversations/{id}/messages` | Per account ID | Documented by Hostaway |
| 400 | 10 seconds | `POST /v1/listings/{id}/calendar/priceDetails` | Per account ID | Documented by Hostaway |
| 200 | 10 seconds | `POST /v1/reservations` | Per account ID | Documented by Hostaway |
| 200 | 10 seconds | All other endpoints | Per account ID | Documented by Hostaway |
| 200 | 10 seconds | All other endpoints | Per IP address | Documented by Hostaway |

Hostaway also documents the window semantics: each limit is measured over a
**sliding window**, not a fixed clock boundary. Sliding-window enforcement is
therefore a documented fact, not a conservative assumption. The integration
today has **no proactive limiter**. It stays under budget by design —
conservative default scan intervals and a small number of coordinators — and
relies entirely on **reactive** handling after the fact:
`custom_components/hostaway/api/retry.py` backs off on HTTP 429 and 5xx
responses, currently honoring only the standard `Retry-After` header, with
`MAX_RETRIES = 3`, `MAX_BACKOFF = 30.0` seconds, and jittered delay. Hostaway
documents its rate-limit headers on 429 responses as `X-RateLimit-*`, not
standard `Retry-After`, so this feature must correct that mismatch.

"Under budget by design" is a weaker guarantee than it sounds. The
reservations coordinator iterates **per selected listing**, calling
`api_client.get_all_reservations(listing_id)`, which itself paginates at
`DEFAULT_PAGE_LIMIT = 100`. A single reservations refresh therefore costs
roughly `N selected listings x pages per listing` requests, not one. With
`MIN_SCAN_INTERVAL` at 1 minute, an operator with a large portfolio can
configure a burst that comfortably exceeds 200 requests inside a 10-second
sliding window without doing anything the integration considers unusual.

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
the same documented limits instead of each independently assuming it has
headroom.

### Chokepoints

All Hostaway data traffic already flows through `HostawayApiClient._request`
in `custom_components/hostaway/api/client.py`. That remains the primary
enforcement point for API methods, including pagination and retries.
Authentication is the exception: `HostawayTokenManager._request_token` in
`custom_components/hostaway/api/auth.py` calls `self._http.post` directly, so
token requests need a second enforcement point while OQ-001 remains open. The
client uses Home Assistant's shared httpx client via
`homeassistant.helpers.httpx_client.get_async_client(hass)` and never
constructs its own `AsyncClient`. Because that transport is **shared with
other integrations**, the limiter MUST be scoped to this integration's own
requests and MUST NOT be implemented as a transport-level or client-level
wrapper that could throttle unrelated integrations' traffic.

### Reactive and proactive must compose, not fight

The existing retry logic is not being replaced. A proactive limiter reduces
the frequency of 429s; it does not make them impossible, because the
per-account budget is shared with other clients using the same Hostaway
account and the per-IP budget is shared with anything else on the host or NAT
talking to Hostaway. The two mechanisms must be layered deliberately: the
limiter gates admission, the retry layer handles what still gets rejected,
and a 429 is treated as **evidence** that feeds back into the limiter rather
than as an isolated event the limiter never learns from.

Hostaway documents this 429 body:

```json
{"status":"fail","message":"This error occurs because a server detects that your application has exceeded the rate limits or has made too many requests in a given period of time."}
```

It also documents these headers as appearing on 429 responses only:

- `X-RateLimit-Limit`: the limit reached.
- `X-RateLimit-Remaining`: requests left in the current window.
- `X-RateLimit-Retry-After`: a Unix timestamp; send the next request after
  this time. It is **not** a delay in seconds.
- `X-RateLimit-Applied`: the counter that was hit: `endpoint`, `account`,
  `ip`, or `provider`.

The existing `api/retry.py::_parse_retry_after` and
`api/auth.py::_request_token` 429 path read the standard `Retry-After` header,
which Hostaway does not document for rate-limit responses. Today that usually
yields `None` and falls back to exponential backoff. This is an in-scope bug
to specify for later implementation. A naive switch to treating
`X-RateLimit-Retry-After` as raw seconds would be worse: because it is a Unix
timestamp, it would calculate a delay around 1.8 billion seconds in 2026,
currently masked only by `MAX_BACKOFF = 30.0`.

### Current endpoint scope

The source code currently calls general endpoints only: listings,
reservations via `GET /v1/reservations` fanned out per listing with
`limit=100`, account-level custom fields via `GET /v1/customFields`,
listing and reservation reads with included custom-field resources, task,
user, and group endpoints, plus Feature 007 custom-field writes through
`PUT /v1/listings/{id}` and `PUT /v1/reservations/{id}`. The integration does
**not** call any endpoint-specific limited endpoint:

- it does not call `POST /v1/conversations/{id}/messages`;
- it does not call `POST /v1/listings/{id}/calendar/priceDetails`;
- it does not call `POST /v1/reservations`.

Therefore this feature implements the documented general counters: 200
requests per 10-second sliding window per account and 200 requests per
10-second sliding window per IP, both checked on each general-endpoint call.
The design must still leave a seam for endpoint-specific counters so a future
feature that adds one of those endpoints can attach the documented separate
bucket without re-plumbing call sites.

### Budget derivation

The previous default of **180 requests per 10 seconds** with a hard ceiling of
**200** remains correct for the documented general bucket: it is exactly 90%
of the 200-request ceiling, leaving roughly 10% headroom for clock skew,
in-flight requests, and traffic this integration cannot observe. The
correction is that this budget applies **per general counter**. A general
endpoint request is admitted only when both the per-account gate and the
per-IP gate have headroom. In the common single-Home-Assistant-instance,
single-account deployment the two gates will track almost identically. They
can diverge if another client uses the same Hostaway account, or if the same
IP serves multiple Hostaway accounts or config entries, so they must be
modelled separately.

### Provenance for corrected facts

The earlier feature draft treated "200 requests per 10 seconds, per account"
as the complete published limit and treated sliding-window behaviour as an
assumption. That is now superseded by Hostaway's live documentation at
<https://api.hostaway.com/documentation>. Hostaway's own changelog entry dated
2026-08-20 says it corrected the published limits, added the per-endpoint
limits for Send conversation message, Calculate reservation price, and Create
a reservation, documented the `X-RateLimit-*` headers returned on 429, and
added the Enterprise limit-increase process. Wayback snapshots from
2025-01-29 and 2025-09-12 show the old table: 15 requests per 10 seconds per
IP and 20 requests per 10 seconds per account, with no per-endpoint tiers, no
window semantics, and no header documentation. That old table is the origin
of `RATE_LIMIT_PER_IP = 15` and `RATE_LIMIT_PER_ACCOUNT = 20` in
`custom_components/hostaway/api/const.py`; those constants were correct when
written, are now stale, and are unreferenced in the codebase. This feature
requires replacing them with the corrected documented values during
implementation, but this specification change does not edit implementation
code.

## Clarifications

### Session 2026-10-08

- Q: What is the right default budget value below the 200 ceiling (OQ-002)?
  → A: 180 requests per 10-second sliding window, applied independently to
  each general counter — account and IP. This preserves the already-correct
  90% ratio below Hostaway's documented 200/10s general ceiling while adding
  the now-in-scope IP dimension.
- Q: Should the budget be operator-configurable at all (OQ-005)? → A: Yes, via
  the options flow only, defaulting to 180 and hard-bounded to a maximum of
  200 per counter. It is the only lever available to an operator sharing an IP
  or account with other Hostaway traffic, which is why it is exposed despite
  the misconfiguration risk.
- Q: What is the right bounded maximum wait for interactive requests (OQ-003)?
  → A: 30 seconds as a single operation-wide monotonic deadline established
  once at the start of the logical service, config-flow, or setup operation.
  Every budget acquisition and every retry backoff sleep in that operation,
  including paginated continuations and token acquisition when needed, consumes
  the remaining time on that one deadline.
- Q: What is the grace period before a scheduled cycle is shed (OQ-004)? → A:
  A short wait of approximately 2 seconds for the entire refresh cycle, not
  per request or per page. A poll arriving moments before the sliding window
  frees capacity would otherwise shed needlessly, but a saturated multi-page
  cycle must not receive a fresh 2-second wait for each request.
- Q: What default suppression period is used when a 429 lacks a usable
  `X-RateLimit-Retry-After` timestamp? → A: **10.0 seconds**, one full
  documented general window. Encoded in FR-015.
- Q: What cooldown controls repeated shed log summaries? → A: **300.0
  seconds**, so sustained shedding remains visible without flooding logs.
  Encoded in FR-026.
- Q: Should budget tuning be gated by Home Assistant advanced mode? → A: No.
  Installed Home Assistant 2026.9.4 marks `FlowHandler.show_advanced_options`
  deprecated with `breaks_in_ha_version="2027.6"`, and its implementation
  unconditionally returns `True`. The options flow must instead place the
  budget field in a `data_entry_flow.section` with `collapsed=True`. This
  supersedes any earlier advanced-mode-only wording.
- Q: Does Hostaway's authentication/token endpoint count against the same
  general 200/10s account and IP counters (OQ-001)? → A: Still undocumented;
  remains a recorded assumption. The conservative assumption that it **does**
  count stands and must not be presented as documented Hostaway behaviour.
  Confirmation requires empirical observation of live response headers or an
  answer from <support@hostaway.com>.
- Q: Which Home Assistant mechanism cleanly expresses "skip this refresh
  without signalling failure" (OQ-006)? → A: Return the coordinator's existing
  `self.data` from `_async_update_data`. Not `async_set_updated_data`, which
  resets the refresh interval and cancels the debounced refresh. Because
  `self.data` is initialised to `None`, the **first** refresh for a config
  entry must never be shed.

### Superseded decision record

An earlier clarification chose per-account-only limiting based on stale
published figures and on the assumption that one Home Assistant host would
never run two Hostaway accounts. Hostaway's corrected documentation now shows
that every general-endpoint call is checked against **both** a per-account and
a per-IP counter, each at 200 requests per 10 seconds. That earlier
per-account-only decision is superseded: per-IP limiting is in scope for this
feature.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Large portfolio stays under the Hostaway limit (Priority: P1)

An operator with many selected listings and short scan intervals configures the
integration. A reservations refresh fans out to many paginated requests. Today
that burst can exceed 200 requests in 10 seconds and Hostaway starts rejecting
traffic; the integration discovers this only after being told "no". With a
proactive limiter, the burst is paced so neither documented general counter
(account nor IP) is knowingly exceeded in the first place.

**Why this priority**: This is the core problem in issue #213. Without it,
nothing else in this feature matters.

**Independent Test**: Configure a simulated account whose refresh cycle demands
more requests than the budget allows within one window; observe that outbound
request timestamps never exceed the configured budget for either general
counter inside any 10-second window, with no 429 responses required to
achieve that.

**Acceptance Scenarios**:

1. **Given** a refresh cycle that would issue 350 requests back to back,
   **When** the cycle runs, **Then** no 10-second window contains more
   admitted requests than the configured budget for either the account gate or
   the IP gate.
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
wrong — including other traffic sharing the same account or IP.

**Independent Test**: Inject a 429 with and without
`X-RateLimit-Retry-After`, and verify that subsequent admissions are
suppressed for the indicated or inferred period.

**Acceptance Scenarios**:

1. **Given** a 429 carrying `X-RateLimit-Retry-After`, **When** it is observed,
   **Then** no further requests that would hit the affected applied counter
   are admitted until that interval has elapsed, while unrelated gates remain
   usable according to `X-RateLimit-Applied`.
2. **Given** a 429 with no usable `X-RateLimit-Retry-After`, **When** it is
   observed, **Then** admission is suppressed for a bounded, conservative default interval.
3. **Given** a suppression period is active, **When** an interactive call
   arrives, **Then** it waits for the suppression to clear rather than bypassing
   it — the server's "no" outranks the interactive priority ordering.
4. **Given** suppression has elapsed with no further 429s, **When** normal
   operation resumes, **Then** the affected gate returns to its current
   effective runtime ceiling without requiring a restart or reload. A lower
   server-reported limit that already reduced the runtime ceiling remains in
   force until explicit reconfiguration or restart.

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
  They share the process-wide IP budget while retaining separate account
  budgets; see FR-006.
- **Config entry reload.** Reloading must not reset the budget to full and
  permit an immediate double-rate burst against a server that is still counting
  the prior window.
- **Home Assistant restart.** After a restart the limiter necessarily starts
  with no memory of pre-restart traffic, so an empty window could immediately
  admit another full local budget inside a server window that is still
  counting. The startup burst must be bounded conservatively rather than
  assuming a clean slate (FR-038).
- **Clock behavior.** The window must be measured on a monotonic basis so that
  a system clock adjustment cannot grant or withhold budget spuriously.
- **An interactive call that is itself enormous.** `get_reservations` invoked
  as a service can be as large as a poll. Interactive priority must not let a
  single huge interactive call, or a stream of separate interactive calls,
  monopolize the budget indefinitely at the cost of every poll being shed
  forever. See FR-019 and FR-020.
- **Cancellation.** A waiter cancelled while queued (service call cancelled,
  entry unloaded) must release its claim so the budget is not leaked.
- **Budget larger than demand.** In the common small-portfolio case the limiter
  must add no measurable latency; it must not become a throughput tax on
  installations that were never near the limit.
- **Startup.** The initial refresh of all coordinators plus listing discovery
  happens close together at setup. That burst passes through the same limiter.
- **First-refresh behaviour differs by coordinator.** Listings and
  reservations use `async_config_entry_first_refresh()`, so their first
  refresh can fail through Home Assistant's retryable setup path. The
  custom-fields coordinator is intentionally non-blocking during setup; its
  first refresh must still never be shed, but failure must leave an explicit
  not-yet-loaded state and retry later rather than publishing empty
  definitions as successful data. See FR-037.

## Requirements *(mandatory)*

### Functional Requirements

#### Coverage and enforcement point

- **FR-001**: Every HTTP request this integration issues to the Hostaway API
  MUST acquire rate-limit budget before being sent. This includes scheduled
  coordinator refreshes, paginated continuation requests, user-triggered
  service calls, authentication/token requests, connection tests performed
  during config flow, listing fetches performed during config flow, and retry
  re-issues. Config-flow helpers MUST inject a limiter into both the token
  manager and API client they construct. If an active config entry for the
  account already exists, validation MUST reuse that account's shared limiter;
  otherwise it MUST use a transient validation limiter that is excluded from
  the shared account and IP budget-minimum calculations.
- **FR-002**: Enforcement MUST occur at the integration's HTTP chokepoints:
  `HostawayApiClient._request` for data traffic and
  `HostawayTokenManager._request_token` for token traffic while OQ-001 remains
  unresolved. Adding a new API method MUST NOT require the author to remember
  to call the limiter.
- **FR-003**: The limiter MUST NOT be implemented by wrapping, replacing, or
  configuring the Home Assistant shared httpx client returned by
  `get_async_client(hass)`, because that client is shared with other
  integrations. Only this integration's own requests may be throttled.
- **FR-004**: A request that cannot be admitted MUST NOT be sent. The limiter
  MUST NOT rely on the server rejecting over-budget traffic.

#### Scope, keying, and documented counters

- **FR-005**: The limiter MUST include a per-account general budget keyed by
  Hostaway **account identity**, so that multiple config entries representing
  the same account share exactly one account budget. The account identity MUST
  be derived from the configured Hostaway account credential (`CONF_CLIENT_ID`,
  which is the Hostaway account id), not from the config entry id, title, or
  object identity. The effective account budget is the minimum configured
  budget across all active config entries sharing that account key, recomputed
  in place without clearing recorded admissions or resetting the sliding
  window. All queues waiting on that account gate MUST be re-pumped after an
  effective account-budget change. Transient config-flow validation limiters
  MUST NOT participate in the account-budget minimum.
- **FR-006**: The limiter MUST include a process-wide per-IP general budget
  shared by all Hostaway config entries in the running Home Assistant process.
  A general-endpoint request MUST be admitted only when **both** the
  per-account gate and the per-IP gate have headroom. The effective IP budget
  is the minimum configured budget across all active config entries, recomputed
  in place on setup, reload, unload, and entry removal. A reload MUST be
  distinguished from a true unload/removal: the reloading entry's existing
  budget contribution remains active during the unload half until setup
  replaces it, so reload cannot temporarily raise the shared IP budget.
  Recomputing MUST NOT clear recorded admissions or reset the sliding window,
  and all queues waiting on the shared IP gate MUST be re-pumped after a
  budget change. Transient config-flow validation limiters MUST NOT participate
  in this process-wide minimum. This supersedes the earlier per-account-only
  scope decision, which was made against stale published figures.
- **FR-007**: The limiter MUST classify each request at the enforcement point
  using its HTTP method and path. General endpoints use the per-account
  general gate plus the process-wide per-IP general gate. Hostaway endpoints
  with their own documented limits MUST use the relevant endpoint-specific
  account bucket **instead of** the general account and IP gates, because
  Hostaway documents that endpoint-specific limits do not draw from the
  general pool. The current integration does not call those endpoints, so this
  feature implements active general gates only while preserving a request
  classifier seam for future endpoint buckets without changing API client
  method, coordinator, or service call sites.
- **FR-008**: Limiter state MUST be held for the lifetime of the relevant
  account and IP gates in the running Home Assistant instance and MUST survive
  a config entry reload, so that a reload cannot reset the window and permit a
  burst. Account-gate state MUST be released when the last config entry for
  that account is removed. The process-wide IP gate remains while any Hostaway
  entry is loaded.

#### Window semantics

- **FR-009**: The limiter MUST treat Hostaway's general limit as **200
  requests per 10-second sliding window** for the account counter and **200
  requests per 10-second sliding window** for the IP counter. A fixed/tumbling
  window interpretation is forbidden because Hostaway documents sliding-window
  accounting and a fixed window can over-admit across a boundary.
- **FR-010**: The default configured budget MUST be **180 requests per
  10-second sliding window per general counter**, deliberately below
  Hostaway's documented 200/10s ceiling. The ~10% headroom exists to absorb
  clock skew between the integration's window and Hostaway's server-side
  accounting, requests already in flight that the limiter can no longer
  withhold, and traffic the integration cannot see (other processes on the
  same IP, and the Hostaway dashboard or other clients using the same
  account). This default MUST be documented as a conservative safety-margin
  choice, not as a technical maximum or as a Hostaway-documented value.
- **FR-011**: The window MUST be measured using a monotonic time source so that
  system clock adjustments cannot grant or withhold budget spuriously.

#### Composition with reactive retry

- **FR-012**: Each retry attempt MUST independently acquire budget before being
  sent. A retry MUST NOT reuse the budget acquired by the attempt it is
  retrying.
- **FR-013**: The existing reactive retry behaviour (429 and 5xx backoff,
  `MAX_RETRIES`, `MAX_BACKOFF`, and jitter) MUST be preserved. This feature
  adds a layer; it does not remove one. Header parsing MUST be corrected to
  use Hostaway's documented `X-RateLimit-*` headers on 429 responses without
  treating `X-RateLimit-Retry-After` as a raw seconds value.
- **FR-014**: When a 429 is received, the limiter MUST consume all four
  documented `X-RateLimit-*` headers when present and enter suppression on the
  affected gate scope: `account` suppresses that account's general gate, `ip`
  suppresses the shared process-wide IP gate, `endpoint` suppresses the
  relevant endpoint-specific account bucket, and `provider` is treated as
  process-wide global suppression for all Hostaway traffic from this
  integration. Provider suppression MUST be held in shared process-wide state,
  not on any one account limiter. Setting or expiring provider suppression
  MUST notify and re-pump every limiter queue. Suppression MUST prevent further
  requests that would hit the affected gate while leaving unrelated gates
  usable. Missing or unrecognized applied-counter values MUST fall back
  conservatively to suppressing all gates applicable to the request. Any
  limiter waiting on a shared gate whose suppression or configuration changes
  MUST be notified and re-pumped. `X-RateLimit-Limit` and
  `X-RateLimit-Remaining` MUST each be validated before use and are
  **usable** only when they parse as a finite integer within range:
  `Limit >= 1` and `Remaining >= 0`. A missing, non-numeric, non-finite, or
  out-of-range value MUST be ignored entirely, leaving the gate untouched and
  logging at debug level. This is required, not cosmetic: a `Limit` of `0` or
  a negative value would violate the gate's `1 <= budget` invariant, and a
  negative `Remaining` would demand more synthetic admissions than the gate
  can hold, which cannot be satisfied and risks a non-terminating
  reconciliation. Ignoring is the conservative choice because the local
  budget already bounds traffic on its own. When usable, they MUST reconcile
  only the affected gate: an observed
  limit lowers that gate's runtime ceiling to `min(configured_budget, limit)`
  and never raises it above the operator-configured value; an observed
  remaining count lower than the local view is represented by synthetic
  in-window admissions so local available capacity is no greater than the
  server reports. Higher observed limits or remaining counts do not delete
  local admissions. These one-way adjustments converge downward and cannot
  oscillate during a runtime; explicit operator reconfiguration or restart is
  required to raise a lowered runtime ceiling.
- **FR-015**: If a 429 carries `X-RateLimit-Retry-After`, the value MUST be
  interpreted as a Unix timestamp and converted to a delay relative to current
  wall-clock time before being applied to the limiter and retry layer, and
  MUST never be treated as a raw seconds value. The resulting delay MUST be
  applied to the two layers differently:
  - The **gate suppression deadline** MUST retain the server-derived delay up
    to a single safety cap of `MAX_SUPPRESSION_SECONDS` (**3600.0**). It MUST
    NOT be clamped to `MAX_BACKOFF`, because truncating it to 30 seconds would
    admit a request before the moment the server told us to resume, which is
    exactly the outcome this feature exists to prevent. This matters
    most for provider-level 429s and for endpoint buckets whose windows exceed
    `MAX_BACKOFF`, such as the documented 30-per-minute messages bucket.
  - The **retry layer's sleep** MAY remain bounded by the existing
    `MAX_BACKOFF` ceiling. A retry that wakes early simply re-acquires from
    the limiter and waits on the still-active suppression, so the server
    deadline is still honoured.

  A waiter's operation-wide deadline MAY therefore expire while suppression is
  still in force; that case MUST surface as the normal wait-timeout path and
  MUST NOT shorten or clear the gate's deadline for other callers.

  The `MAX_SUPPRESSION_SECONDS` cap exists solely to bound a corrupt or
  wrong-unit header — a timestamp sent in **milliseconds** reads, as seconds,
  as a moment tens of thousands of years ahead and would otherwise wedge the
  integration for the lifetime of the process. The opposite mistake, a raw
  seconds delay such as `3600`, reads as a moment in 1970 and is already
  rejected by the not-in-the-future rule below. Hostaway documents no bucket remotely close
  to an hour, so a legitimate deadline is never expected to reach the cap. A
  capped suppression is **not** treated as honouring the server deadline: when
  it expires, the single request admitted next simply earns a fresh 429 whose
  header re-suppresses the gate, so traffic stays suppressed at the cost of
  one probe per hour rather than resuming in full.

  Only a header that is absent, non-numeric, or not in the future is
  **unusable**; a distant-but-valid timestamp is used and capped, never
  discarded. For an unusable header suppression MUST last **10.0 seconds**.
- **FR-016**: Suppression MUST apply to **all** callers including interactive
  ones. A server-issued `X-RateLimit-Retry-After` outranks the integration's
  internal priority ordering.
- **FR-017**: Suppression MUST clear automatically once elapsed, returning the
  affected gate to its current effective runtime ceiling with no reload or
  restart required. Suppression expiry MUST NOT raise a gate whose runtime
  ceiling was lowered by `X-RateLimit-Limit`; explicit operator
  reconfiguration or restart is required to raise that lowered ceiling.

#### Priority, shedding, and queueing

- **FR-018**: Requests MUST carry a priority classification with at least two
  levels: **interactive** (user-triggered service calls and config-flow
  validation) and **scheduled** (coordinator refreshes and their paginated
  continuations).
- **FR-019**: When budget becomes available and both classes are waiting,
  **interactive** requests normally take precedence, but the queue MUST include
  anti-starvation aging for scheduled work. If the oldest scheduled waiter has
  waited at least **1.0 second**, or if **20** interactive admissions have been
  made while that scheduled waiter was queued, the next admission for which its
  gates have capacity MUST go to the oldest scheduled waiter. These defaults
  fit the 2.0-second scheduled grace while preserving interactive preference,
  and they are small relative to the real coordinator intervals (listings 5
  min, reservations 2 min, custom fields 15 min, minimum 1 min).
- **FR-020**: Interactive logical operations MUST wait for budget rather than
  being shed, subject to a single operation-wide monotonic deadline of
  **30 seconds** established once at the start of the service call, config-flow
  validation, or setup probe. Every subsequent acquisition in that operation
  and every retry backoff sleep MUST consume only the deadline's remaining
  time; paginated continuations, read → write → read-back custom-field writes,
  retries, and token acquisition do not receive fresh 30-second waits.
  Exceeding the deadline MUST surface as an explicit, actionable error
  identifying rate limiting as the cause; the call MUST NOT proceed anyway and
  MUST NOT report success. A local proactive wait timeout MUST be surfaced
  directly and MUST NOT be handled as a server 429 retry. Any handler that
  catches `HostawayRateLimitError` for reactive 429 retry logic MUST first
  catch `HostawayRateLimitWaitTimeout` and re-raise or convert it without a
  service-level sleep or retry; all such handlers MUST be audited and covered
  by a regression test.
- **FR-021**: Scheduled refresh cycles MUST be **shed**, not queued
  indefinitely. A scheduled coordinator cycle gets one cycle-wide monotonic
  deadline of **2.0 seconds** covering all limiter waits and retry backoff
  sleeps in that cycle; requests, pages, and retries do not get fresh
  2-second waits. If the cycle cannot proceed within the remaining deadline,
  the entire refresh cycle MUST be abandoned for that interval. The grace
  period is non-zero so that a poll arriving moments before the sliding window
  frees capacity is not shed needlessly. This shedding rule MUST NOT be applied
  to a coordinator's first refresh for a config entry, which is governed
  exclusively by FR-037.
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
  be shed, but first-refresh failure handling is intentionally asymmetric. For
  the listings and reservations coordinators, which setup awaits with
  `async_config_entry_first_refresh()`, the first refresh MUST either fetch
  real data or fail through Home Assistant's retryable `ConfigEntryNotReady`
  path; successful setup MUST NOT publish `None`, partial data, or an empty
  *placeholder* for those coordinators. An empty dataset legitimately returned
  by a **successful** fetch — for example when no listings are selected — is
  valid and MUST be published normally. The prohibition is on emptiness that
  stands in for an absent or failed fetch, not on emptiness as a real result. The custom-fields coordinator MUST remain asynchronous
  and non-blocking during setup so a custom-fields outage does not fail the
  whole integration. Its first refresh MUST still use the first-refresh wait
  policy and never shed, but a timeout or failure MUST leave an explicit
  **not yet loaded** state distinct from a successfully loaded empty list, log
  the condition, and schedule bounded retries on subsequent refresh intervals
  until real definitions are fetched. Successful setup MUST NOT treat the
  initial `[]` placeholder as initialized custom-field data.
- **FR-038**: Because limiter state is deliberately **not persisted** across a
  restart, a freshly created gate MUST begin in a conservative startup hold
  rather than at full capacity: for the first full window after creation it
  MUST behave as though half of its effective budget were already consumed.
  The hold MUST expire naturally once that first window has elapsed, MUST NOT
  apply to a gate merely reconfigured in place (a reload reuses the existing
  gate and its recorded admissions), and MUST NOT delay the first refresh
  beyond its wait policy.
- **FR-039**: At most **one** persistent `AccountRateLimiter` MUST exist per
  Home Assistant instance, so the shared process-wide IP gate has exactly one
  persistent waiter queue. This makes the priority and aging ordering of
  FR-018 and FR-019 a **total** order over all persistent IP-gated traffic.
  Were two persistent limiters to queue on that gate, a freed IP slot would be
  taken in timer-callback order rather than priority order — a scheduled
  waiter in one limiter could overtake an interactive waiter in the other, and
  each aging counter would be blind to the other's admissions. The
  implementation MUST therefore **refuse the additional config entry itself**
  rather than reusing the existing limiter for it. Reuse is not an option: it
  would alias a second account onto the first account's gate, contradicting
  FR-005, and an account-scoped 429 earned by one account would suppress the
  other even though Hostaway counts them separately.

  Enforcement MUST happen at both ends. The config flow MUST abort an attempt
  to add a second entry with the standard `single_instance_allowed` reason;
  today it only guards against a **duplicate** account, since its unique id is
  the client id, so a different client id is currently accepted. Setup of an
  additional entry that nonetheless reaches `async_setup_entry` MUST fail with
  a **non-retryable** config-entry error naming the single-instance
  constraint, never with a retryable not-ready error, because retrying cannot
  resolve it.

  The short-lived config-flow limiter of FR-001 is the single permitted
  exception. It shares the process-wide IP gate, so budget correctness still
  holds; only ordering fairness against the entry's queue is unspecified, and
  that exposure is bounded by the config flow's brief lifetime and by its
  requests being interactive priority, which is the highest band anyway.

#### Observability

- **FR-025**: Every shed scheduled cycle MUST emit a named log record that
  identifies the coordinator and states rate limiting as the cause, and MUST
  increment a shed counter for diagnostics. The coordinator MUST be identified
  by a fixed label (`listings`, `reservations`, `custom_fields`) and MUST NOT
  be identified by the generated coordinator name, which embeds the config
  entry `unique_id` and is therefore credential material. Warning-level
  frequency is governed entirely by FR-026.
- **FR-026**: Repeated consecutive skips MUST be rate-limited in the log
  using a **300.0-second** cooldown (log the first occurrence, demote repeats
  within the cooldown, then emit periodic summaries) so the log remains usable
  while the ongoing condition stays visible.
- **FR-027**: A 429 received despite proactive limiting MUST be logged
  distinguishably from a proactive skip, so an operator can tell self-limiting
  from server pushback.
- **FR-028**: At debug level, the limiter MUST log per-request admission
  decisions including the priority class, the wait duration incurred, and the
  remaining budget.
- **FR-029**: The limiter MUST expose, for diagnostics, per-gate state for the
  account and IP gates: configured budget, effective budget, current-window
  utilization, waiting counts, suppression state, and seconds until
  suppression clears. Diagnostics MUST also include shed-cycle counts and
  observed 429 counts by applied counter when known. This information MUST
  appear in the integration's diagnostics output and MUST NOT include
  credentials.

#### Configuration

- **FR-030**: The limiter MUST be enabled by default with no operator action
  required. Correct behaviour for a typical installation MUST NOT depend on
  configuration.
- **FR-031**: The requests-per-window budget MUST be operator-configurable, and
  MUST be exposed **only** through the config entry **options flow**
  (reconfigurable without removing the integration), never as a setup-time-only
  field. It MUST be placed in a `data_entry_flow.section` with
  `collapsed=True`, not gated by `FlowHandler.show_advanced_options`. It is
  exposed because it is the only lever available to an operator whose IP or
  account is shared with other Hostaway traffic; the misconfiguration risk is
  accepted in exchange for that lever.
- **FR-032**: The requests-per-window budget option MUST default to **180** and
  MUST be hard-bounded to a maximum of **200 per general counter**, Hostaway's documented ceiling.
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

- **FR-035**: User-facing documentation MUST state that Hostaway applies both
  account and IP general counters to ordinary endpoint calls, that this
  integration tracks both, and that sharing an account or IP with other
  Hostaway clients can still cause server pushback the integration cannot
  fully predict.
- **FR-036**: Documentation MUST explain that stale-but-available entities can
  indicate shed cycles, and that the remedy is lengthening scan intervals or
  reducing selected listings rather than filing a bug.

### Key Entities

- **Request budget**: The admission allowance for one documented counter — a
  maximum number of admitted requests within a 10-second sliding window
  (default 180, operator-configurable up to 200), plus the record of
  admissions within the current window.
- **Budget gate**: One independent admission condition selected by the request
  classifier. General endpoint calls use the per-account general gate and the
  process-wide per-IP general gate together. Endpoint-specific calls use their
  documented endpoint bucket instead of the general gates.
- **Account key**: The identity under which the per-account budget is shared,
  derived from the Hostaway account credential, so that multiple config
  entries for one account share one account budget.
- **IP gate**: The process-wide general budget representing this Home
  Assistant instance's outbound IP counter. It is shared across all Hostaway
  config entries in the process, uses the minimum configured budget across
  active entries, preserves a reloading entry's contribution until replacement
  setup completes, and re-pumps all affected queues when reconfigured or
  suppressed.
- **Endpoint classifier**: The extension point for Hostaway endpoints with
  separate documented counters. It maps method and path to either the general
  gate set or an endpoint-specific account bucket. The current integration does
  not call those endpoints, so no endpoint-specific gate is active yet.
- **Priority class**: `interactive` or `scheduled`; determines admission
  ordering with the aging rule in FR-019. Saturation behaviour comes from the
  operation's wait policy and shared deadline: interactive waits until its
  30-second deadline, scheduled steady-state cycles shed at their 2-second
  cycle deadline, and first refreshes use the first-refresh rule in FR-037.
- **Suppression state**: A time-bounded period following an observed 429 during
  which no request for the affected counter is admitted regardless of
  priority.
- **Shed record**: The observable fact that a named coordinator abandoned a
  scheduled cycle due to rate limiting, surfaced in logs and diagnostics.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Under a simulated workload demanding at least 2x the configured
  budget, **zero** 10-second sliding windows contain more admitted requests
  than the configured budget for either the account gate or the IP gate,
  measured across the entire run.
- **SC-002**: In a deterministic simulator containing only integration-generated
  traffic and Hostaway's documented sliding-window counters, the same
  over-budget workload produces 429 responses when limiter enforcement is
  disabled and **zero** 429 responses when limiter enforcement is enabled.
- **SC-003**: With two coordinators refreshing concurrently plus a concurrent
  service call, the **combined** admitted rate stays within the shared account
  and IP budgets; no two components are observed to each consume a full
  budget.
- **SC-004**: When budget becomes available before the single 30-second
  operation deadline expires, an interactive `hostaway.set_custom_field` call
  completes its full read -> write -> read-back sequence and performs the
  feature 007 FR-056 verification within that deadline in 100% of trials. When
  the operation deadline expires first, 100% of trials raise an error naming
  rate limiting and zero trials sleep again, retry the whole write sequence as
  a service-level 429 retry, or report success.
- **SC-005**: When a scheduled cycle is shed, entities fed by that coordinator
  retain their prior values and report as available in 100% of trials; no
  "failed to update" is recorded.
- **SC-006**: Every shed cycle is discoverable from the Home Assistant log at
  default verbosity, naming the coordinator and the cause. The first occurrence
  emits a warning immediately; sustained shedding emits warning summaries at
  most **300.0 seconds plus one coordinator interval** apart, because summaries
  are emitted opportunistically on shed attempts rather than by a dedicated
  timer.
- **SC-007**: After an injected 429 with `X-RateLimit-Applied: account` and an
  `X-RateLimit-Retry-After` Unix timestamp 5 seconds in the future, no request
  using that account gate is admitted for at least 5 seconds, and normal
  admission resumes automatically thereafter without a restart or reload.
- **SC-008**: For an installation whose demand is below budget — the typical
  small-portfolio case — an acquisition with available capacity resolves
  synchronously with respect to limiter timers: it arms no timer and performs
  no deliberate suspension while capacity exists.
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
- **SC-013**: Integration diagnostics output includes a per-gate `gates`
  structure for at least `account` and `ip`, and a test can show diverged gate
  state (for example account utilization differs from IP utilization or only
  one gate is suppressed). The payload also includes shed-cycle count and
  observed 429 counts by applied counter when known, and contains no
  credentials.
- **SC-014**: A classifier test demonstrates correct bucket selection: a
  general request consumes the account-general and IP-general gates, while a
  hypothetical documented endpoint-specific request consumes only its
  endpoint-specific account bucket and does **not** consume the general account
  or IP gates. Adding that prototype endpoint bucket requires zero changes to
  coordinator, service, or public API method code.
- **SC-015**: With the budget saturated at config entry setup, listings and
  reservations first refreshes are never shed: across 100% of trials setup
  either succeeds with a genuinely fetched dataset — which may legitimately be
  empty, such as when no listings are selected — or fails through a
  `ConfigEntryNotReady`-style retryable setup failure. No trial produces a
  successful setup whose data is an unfetched placeholder. The custom-fields
  coordinator remains non-blocking; across 100% of trials, setup success leaves
  it in an explicit not-yet-loaded state rather than treating `[]` as
  successfully initialized data, and a later bounded retry converges when
  budget becomes available.
- **SC-018**: Under sustained interactive load with scheduled waiters queued,
  scheduled traffic still progresses: once the oldest scheduled waiter reaches
  the FR-019 aging threshold and capacity exists before its 2-second cycle
  deadline, the next admission goes to that scheduled waiter in 100% of
  trials.
- **SC-016**: A shed scheduled cycle leaves the coordinator's configured
  update interval and debounce timer unchanged, demonstrating that the shed
  was performed by returning existing data from `_async_update_data` rather
  than via `async_set_updated_data`.
- **SC-017**: The options flow presents the requests-per-window budget inside
  a collapsed section, defaults it to 180, accepts 200, and rejects 201 and
  any larger value with a validation error rather than clamping or accepting
  it, in 100% of trials.

## Assumptions

- **Owner constraint — single installation**: by the nature of this
  integration, exactly **one** Hostaway config entry is ever installed on a
  Home Assistant instance. This is the deployment the design targets and the
  basis of FR-039. The multi-entry language retained in FR-005 and in the
  lifecycle tables is defensive bookkeeping so that duplicate or
  mid-reload entries cannot raise a budget; it is not a supported
  configuration and MUST NOT be read as a licence to run concurrent
  persistent limiters.
- **Documented by Hostaway**: Hostaway publishes separate counters at
  <https://api.hostaway.com/documentation>, including the general 200
  requests per 10 seconds per account counter and the general 200 requests per
  10 seconds per IP counter. Both apply to ordinary endpoint calls.
- **Documented by Hostaway**: Each limit has its own counter. The three
  endpoint-specific counters do not draw from the general pool. The current
  integration does not call those endpoint-specific limited endpoints.
- **Documented by Hostaway**: Hostaway measures each limit over a
  **sliding window**; the counter does not reset on a fixed clock boundary.
- **Documented by Hostaway**: HTTP 429 rate-limit responses carry
  `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Retry-After`, and `X-RateLimit-Applied` when Hostaway returns
  the documented headers. `X-RateLimit-Retry-After` is a Unix timestamp, not a
  delay in seconds.
- **Observed/inferred provenance**: The stale `RATE_LIMIT_PER_IP = 15` and
  `RATE_LIMIT_PER_ACCOUNT = 20` constants came from older Hostaway published
  limits visible in 2025 Wayback snapshots. They were correct when written,
  are unreferenced today, and are now stale after Hostaway's documented
  2026-08-20 correction.
- **Assumption**: `CONF_CLIENT_ID` uniquely identifies a Hostaway account and
  is therefore a sound budget key. Two config entries with the same client id
  address the same account and the same server-side account budget.
- **Undocumented assumption (OQ-001)**: Hostaway's token/authentication
  requests count against the same general account and IP limits as data
  requests. This is **not** documented by Hostaway and has not been verified
  against the live service. Counting them is the conservative choice; if they
  are in fact exempt, the only cost is slightly more headroom than necessary.
- **Observed against the installed Home Assistant source**: a refresh can
  decline to update without signalling failure by returning the coordinator's
  existing `self.data` from `_async_update_data`, which leaves
  `last_update_success` true and keeps entities available (FR-023). The same
  source shows `DataUpdateCoordinator.data` is initialised to `None` and that
  `async_config_entry_first_refresh` raises `ConfigEntryNotReady` when the
  first refresh fails — which is why the first refresh is never sheddable
  (FR-037). `async_set_updated_data` is not a valid shed mechanism because it
  resets the refresh interval and cancels the debounced refresh.
- Other processes on the same IP — the Hostaway web dashboard, other
  integrations, scripts — may consume part of the per-IP budget invisibly. The
  headroom in FR-010 and the 429 feedback loop in FR-014 through FR-017 are
  the mitigations for traffic the integration cannot see.
- No config entry migration is needed; all new settings are optional options
  with defaults (FR-033).

## Out of Scope

- Implementing endpoint-specific counters for endpoints the integration does
  not call today. A clean seam is required (FR-007), but active gates for
  `POST /v1/conversations/{id}/messages`,
  `POST /v1/listings/{id}/calendar/priceDetails`, and
  `POST /v1/reservations` are deferred until the integration calls those
  endpoints.
- Persisting limiter state across Home Assistant restarts.
- Replacing or redesigning the existing reactive retry layer beyond consuming
  Hostaway's documented `X-RateLimit-*` headers correctly on 429 responses.
- Request coalescing, response caching, or de-duplication of overlapping
  requests. These would reduce request volume but are a different feature.
- Changing default scan intervals, `MIN_SCAN_INTERVAL`, or
  `DEFAULT_PAGE_LIMIT`.
- Adaptive discovery of the server's real limit by probing it.
- Rate limiting any non-Hostaway traffic.

## Open Questions

One question remains genuinely unresolved and is flagged rather than guessed.
The other nine were resolved in the 2026-10-08 clarification session and are
retained below as a decision record. Resolutions are recorded in
[Clarifications](#clarifications) and encoded in the requirements they bound.

### Outstanding

- **OQ-001** *(open)*: Does Hostaway's authentication/token endpoint count
  against the same general account and IP 200/10s budgets as data endpoints?
  Undocumented by Hostaway. The spec assumes **yes** (conservative) and that assumption is recorded as
  an assumption, not as documented behaviour. Confirming this requires empirical observation of live response headers or
  asking <support@hostaway.com>. If verification shows otherwise, FR-001's inclusion of token requests can be relaxed; the conservative
  default stands until then. This does not block planning or implementation —
  the conservative path is already correct under either answer.

### Resolved

- **OQ-002** *(resolved)*: Default budget — **180 requests per 10-second sliding
  window per general counter**, ~10% headroom below the documented 200/10s
  ceiling for clock skew and in-flight requests the limiter cannot observe.
  Encoded in FR-010.
- **OQ-003** *(resolved)*: Bounded maximum interactive wait — **30 seconds**
  as one operation-wide monotonic deadline, long enough for a three-request
  custom-field write under ordinary saturation but not renewable per request,
  page, token acquisition, or retry. Encoded in FR-020 and SC-004.
- **OQ-004** *(resolved)*: Scheduled-shed grace period — **2.0 seconds** as
  one cycle-wide monotonic deadline, not zero and not renewable per request or
  page, so a poll arriving moments before the window frees capacity is not shed
  needlessly while a saturated multi-page cycle cannot wait repeatedly. Encoded
  in FR-021.
- **OQ-005** *(resolved)*: The budget **is** operator-configurable, through the
  options flow only, defaulting to 180 and hard-bounded at 200 per general
  counter. Encoded in FR-031, FR-032 and SC-017.
- **OQ-006** *(resolved)*: A shed is expressed by returning the coordinator's
  existing `self.data` from `_async_update_data`; `async_set_updated_data` is
  **not** suitable because it resets the refresh interval and cancels the
  debounced refresh. Listings and reservations use
  `async_config_entry_first_refresh`, so their first-refresh failures propagate
  to `ConfigEntryNotReady`. The custom-fields coordinator intentionally does
  not block setup; it needs an explicit not-yet-loaded state and bounded retry
  rather than pretending its initial `[]` is successfully loaded data. Encoded
  in FR-023, FR-037, SC-015 and SC-016.
- **OQ-007** *(resolved)*: Per-IP limiting is in scope. The earlier
  per-account-only decision is superseded by Hostaway's corrected
  documentation showing that ordinary endpoint calls count against both
  account and IP counters. Encoded in FR-006.
- **OQ-008** *(resolved)*: 429s without a usable
  `X-RateLimit-Retry-After` suppress for **10.0 seconds**. Encoded in FR-015.
- **OQ-009** *(resolved)*: Repeated shed log summaries use a **300.0-second**
  cooldown. Encoded in FR-026.
- **OQ-010** *(resolved)*: The budget option uses a collapsed options-flow
  section, not Home Assistant advanced-mode gating. Encoded in FR-031 and
  SC-017.

## References

- Hostaway Public API documentation, <https://api.hostaway.com/documentation>
  — documented multi-counter rate limits, sliding-window semantics, 429 body,
  `X-RateLimit-*` headers, and Enterprise increase path, verified live on
  2026-10-08.
- Hostaway documentation changelog entry dated 2026-08-20 — provenance for the
  corrected published limits and newly documented 429 headers.
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
  handling that this feature composes with, and the current standard
  `Retry-After` parser that must learn Hostaway's documented
  `X-RateLimit-Retry-After` Unix timestamp.
- `custom_components/hostaway/coordinator.py` — the three coordinators subject
  to shedding.
- `homeassistant/helpers/update_coordinator.py` (installed Home Assistant) —
  `DataUpdateCoordinator.data` initialised to `None`,
  `async_config_entry_first_refresh` raising `ConfigEntryNotReady` on failure,
  and `async_set_updated_data` resetting the refresh interval and cancelling
  the debounced refresh. The basis for FR-023 and FR-037.
