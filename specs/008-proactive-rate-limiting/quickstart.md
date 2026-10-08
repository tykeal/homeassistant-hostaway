<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD024 MD040 MD060 -->

# Quickstart: Proactive Hostaway API Rate Limiting

**Feature**: `008-proactive-rate-limiting` | **Date**: 2026-10-08

Orientation for whoever implements this. Read
[plan.md](plan.md) for the shape, [research.md](research.md) for why each
decision was taken, [data-model.md](data-model.md) for the types and their
invariants, and
[contracts/rate-limiter-interface.md](contracts/rate-limiter-interface.md) for
the behaviour tests must assert.

---

## Environment

```bash
cd /path/to/worktree           # branch 008-proactive-rate-limiting
uv sync                        # if .venv is not yet populated

uv run pytest tests/           # baseline: 493 passing
uv run ruff check custom_components/ tests/
uv run mypy custom_components/
```

All three must be clean before you start and after every phase. The test count
may only grow (SC-011).

---

## The ten-second mental model

```text
                        contextvars.ContextVar
  service call  ────────── INTERACTIVE ──────────┐
  config flow   ────────── INTERACTIVE ──────────┤
  setup probe   ────────── INTERACTIVE ──────────┤
  coordinator   ────────── SCHEDULED  ───────────┤
                                                 ▼
                                      AccountRateLimiter.acquire()
                                                 │
                        ┌────────────────────────┴───────────────────┐
                        │  suppressed?  → wait                       │
                        │  all gates have capacity? → admit + record │
                        │  else → queue on (priority, sequence) heap │
                        └────────────────────────┬───────────────────┘
                                                 ▼
   HostawayApiClient._request ─┐      ┌─ HostawayTokenManager._request_token
     (inside the retry loop,   │      │    (bypasses _request entirely —
      so a retry re-acquires)  │      │     that is why it needs its own)
                               ▼      ▼
                          httpx shared client  ──▶  Hostaway
                               │
                      429 ─────┴──▶ limiter.note_rate_limited(applied_counter, retry_at, limit, remaining)
```

Saturation splits by caller:

| Caller | Waits | Then |
|---|---|---|
| interactive operation | one 30 s deadline | `HostawayRateLimitWaitTimeout` → actionable service error |
| scheduled, 2nd+ refresh cycle | one 2 s deadline | `HostawayRateLimitShedError` → `return self.data` |
| listings/reservations first refresh | one 30 s deadline | `HostawayRateLimitWaitTimeout` → `UpdateFailed` → `ConfigEntryNotReady` |
| custom-fields first refresh | one 30 s deadline | not-yet-loaded state + bounded retry |

---

## Five traps that will bite you

Each of these is a silent-correctness failure, not a crash. They are in the
plan and the data model too, repeated here because they are the ones easiest
to get wrong.

### 1. `HostawayRateLimitShedError` must NOT subclass `HostawayApiError`

All three coordinators contain:

```python
except HostawayApiError as exc:
    raise UpdateFailed(f"Failed to fetch ...: {exc}") from exc
```

If the shed exception were in that hierarchy, the coordinator's own handler
would convert a shed into a **failure** before your shed handler ever saw it —
`last_update_success` goes `False`, entities go unavailable, and SC-005 fails.
Subclass plain `Exception`.

### 2. `HostawayCustomFieldsCoordinator` has a bare `except Exception` tail

```python
except Exception as exc:
    raise UpdateFailed(f"Unexpected error fetching custom field definitions: {exc}")
```

That swallows the shed exception from inside the fetch body. Add an explicit
`except HostawayRateLimitShedError: raise` **ahead** of it.

### 3. `self.data is None` is NOT a first-refresh test here

`HostawayCustomFieldsCoordinator.__init__` ends with `self.data = []`. Its
`data` is never `None`. A `data is None` check would classify its first
refresh as sheddable, and a shed would publish `[]` — an empty definition set,
which FR-037 explicitly forbids. The listings coordinator has the same hazard
with a legitimately empty `{}`.

Use an explicit `_first_refresh_complete: bool`, set only after a successful
fetch. For custom fields, also track a not-yet-loaded state distinct from a
successfully loaded empty definitions list; setup must stay non-blocking.

### 4. The limiter registry must not live in `hass.data[DOMAIN]`

`async_unload_entry` ends with:

```python
if not hass.data.get(DOMAIN):
    async_unregister_services(hass)
```

Today that dict holds only entry-id keys, so the test means "no entries left".
Add a non-entry key and it becomes permanently truthy — services would never
be unregistered. Use a separate top-level key (`DATA_RATE_LIMITERS`).

### 5. The limiter must survive unload, and only die on removal

A config entry reload is unload-then-setup. Tearing the limiter down in
`async_unload_entry` would reset the window and let a reload admit a second
full budget inside the same 10 seconds — exactly what FR-008 and SC-012
forbid. Tear down in a new `async_remove_entry` hook instead, and only when no
other loaded entry shares the account key.

Equally: on an options change, call `limiter.configure(...)` on the
**existing** instance. Never construct a new one. The shared IP gate uses the
minimum configured budget across active entries, and same-account entries use
the minimum account budget across that account key. During reload, keep the
unloading entry's previous contributions active until setup replaces them, so
the unload half cannot raise capacity. Config-flow validation limiters do not
participate. Recompute in place and re-pump waiters without clearing
admissions.

---

### 6. A local wait timeout is not a server 429

`HostawayRateLimitWaitTimeout` inherits from `HostawayRateLimitError`, but the
custom-field write service catches `HostawayRateLimitError` to sleep and retry
server 429s. Add an earlier `except HostawayRateLimitWaitTimeout: raise` (or
convert directly to `ServiceValidationError`) before that branch, and audit any
other specific `HostawayRateLimitError` handlers. A proactive queue timeout
must not re-run the read → write → read-back sequence.

### 7. Endpoint buckets are selected, not appended

Hostaway documents that an endpoint with its own limit does not use the
general limit. Classify by method/path inside the enforcement point: general
requests consume account-general plus IP-general; future documented special
endpoints consume their endpoint account bucket only.

## Where each requirement lands

| Requirement | Lands in |
|---|---|
| FR-001 coverage incl. token | `client.py::_request` + `auth.py::_request_token` |
| FR-002 chokepoint, FR-004 never send | `client.py::_request` |
| FR-003 don't wrap the shared httpx client | by omission — verify nothing touches `self._http` config |
| FR-005 account keying, FR-008 lifetime | `__init__.py` registry + `async_remove_entry` |
| FR-006 per-IP general gate | shared IP gate with minimum active-entry budget, reload contribution preservation, and README (Phase G) |
| FR-007 endpoint-specific seam, SC-014 | method/path classifier + selected `BudgetGate` objects |
| FR-009 sliding window, FR-011 monotonic | `rate_limit.py::SlidingWindowGate` |
| FR-010 default 180 | `api/const.py::DEFAULT_RATE_LIMIT_BUDGET` |
| FR-012 retries re-acquire, SC-010 | acquisition **inside** the `for attempt` loop |
| FR-013 retry preserved | `retry.py` header parsing corrected; assert constants, jitter, and backoff curve untouched |
| FR-014..017 suppression | gate-scoped `note_rate_limited` + `_pump()` |
| FR-018..020 priority & interactive deadline | contextvar + priority heap with aging + `INTERACTIVE_POLICY` |
| FR-021..024 shedding | coordinator base class with cycle-wide deadline |
| FR-025..028 observability | coordinator base (shed log) + `client.py` (DEBUG/WARNING) |
| FR-029 diagnostics | new `diagnostics.py` + `redaction.py` digest |
| FR-030..034 configuration | `config_flow.py` + `const.py` + `strings.json` |
| FR-035..036 documentation | README |
| FR-037 first refresh never shed | coordinator base `_first_refresh_complete`; custom-fields not-yet-loaded state |

---

## Testing without waiting

The limiter takes an injected `clock` and an injected `schedule` (a
`call_later`-style hook). That is deliberate: it is what lets the User Story 1
acceptance scenario — "a refresh cycle that would issue 350 requests back to
back" — run in milliseconds rather than 20 real seconds, and it makes SC-007's
"no request admitted for at least 5 seconds" an exact assertion instead of a
flaky sleep.

Sketch:

```python
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.timers: list[tuple[float, Callable[[], None]]] = []

    def __call__(self) -> float:
        return self.now

    def schedule(self, delay: float, cb: Callable[[], None]) -> Handle: ...

    def advance(self, seconds: float) -> None:
        """Move time forward, firing due timers in order."""
```

Rules for limiter tests:

- **No `asyncio.sleep`.** If a test sleeps, the clock is not injected properly.
- Drive admission by `clock.advance(...)` and assert on which futures resolved.
- Assert ordering by resolving order, not by timing.
- For SC-018, queue sustained interactive work plus one scheduled waiter, then
  prove the scheduled waiter wins after 1.0 s or 20 interactive admissions.
- For SC-008, assert that a below-budget `acquire()` arms **no** timer.

For the Home Assistant layer, use `pytest-homeassistant-custom-component` as
the existing `tests/test_coordinator.py` and `tests/test_config_flow.py` do.

---

## Useful assertions for the tricky success criteria

**SC-016** — proving `async_set_updated_data` was not used:

```python
interval_before = coordinator.update_interval
debouncer_before = coordinator._debounced_refresh
# ... force a shed ...
assert coordinator.update_interval == interval_before
assert coordinator._debounced_refresh is debouncer_before
assert coordinator.last_update_success is True
```

**SC-015** — first refresh asymmetry:

```python
# saturate the limiter, then:
with pytest.raises(ConfigEntryNotReady):
    await coordinator.async_config_entry_first_refresh()
# OR it succeeded with real data — never a successful setup with None/empty
# Custom fields: setup succeeds, but state is explicitly not-yet-loaded until
# a later bounded retry fetches real definitions.
```

**SC-010** — a retry consumes separate budget:

```python
# mock transport: 429, 429, 429, 200
await client.get_listings_page()
assert limiter.snapshot().admitted_total == 4   # not 1
# Also assert retry sleeps do not exceed RequestContext.remaining(now).
```

**SC-013** — no credentials leak:

```python
payload = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
assert entry.data[CONF_CLIENT_ID] not in payload
assert entry.data[CONF_CLIENT_SECRET] not in payload
```

**SC-017** — reject, do not clamp:

```python
result = await flow.async_configure(flow_id, {"advanced": {"rate_limit_budget": 201}})
assert result["type"] is FlowResultType.FORM
assert result["errors"]["base"] == "invalid_rate_limit_budget"
# and critically: nothing was stored as 200
```

---

## Manual verification once implemented

CI must be green first (Constitution I: manual testing without green CI is
prohibited).

1. **Below budget — no behaviour change.** A small portfolio with default
   intervals. Entities update exactly as before; no new log lines at default
   verbosity; diagnostics shows `gates.account.admitted_in_window` and
   `gates.ip.admitted_in_window` well under their effective budgets.
2. **Forced shedding.** Set many listings and the 1-minute minimum reservation
   interval, or temporarily lower `rate_limit_budget` to a small value.
   Expect: a WARNING naming the reservations coordinator, entities still
   showing their previous values and **available**, and `shed_total` climbing
   in diagnostics.
3. **Interactive priority.** While shedding is happening, call
   `hostaway.set_custom_field`. It must complete its full read → write →
   read-back.
4. **Options round trip.** Expand the advanced section; confirm 180 is the
   default, 200 is accepted, 201 is rejected with a message rather than
   silently clamped, and a reload does not produce a burst.
5. **Diagnostics download.** Confirm the `rate_limit` block is present and
   that the client id does not appear anywhere in the file.

---

## Commit discipline reminders

- One logical change per commit; each commit must be green (Constitution III).
- `git commit -s` plus the `Co-authored-by` trailer (Constitution VI).
- Conventional Commits with capitalised types: `Feat`, `Fix`, `Test`, `Docs`,
  `Refactor`, `Chore`.
- SPDX headers on every new file; `specs/**` is also covered by `REUSE.toml`.
- Never `--no-verify` (Constitution V). On a hook failure: fix, `git add`,
  retry the commit. Do not `git reset`.
- `tasks.md` updates go in their own commit, separate from the code they track.
- **Do not create a `CHANGELOG.md`.** The repository deliberately has none;
  release notes are generated.
- **Do not modify `custom_components/hostaway/sensor/listing.py`.**
