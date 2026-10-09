# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the proactive rate limiter core.

Every test drives time through :class:`FakeClock`, so none of them sleeps
and none of them depends on how fast the machine is. A test that needed a
real delay would be asserting something about the event loop rather than
about the limiter.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Generator
from pathlib import Path

import pytest

from custom_components.hostaway.api import rate_limit
from custom_components.hostaway.api.const import (
    DEFAULT_SUPPRESSION_SECONDS,
    MAX_SUPPRESSION_SECONDS,
    RATE_LIMIT_CEILING,
    RATE_LIMIT_WINDOW_SECONDS,
)
from custom_components.hostaway.api.exceptions import (
    HostawayRateLimitShedError,
    HostawayRateLimitWaitTimeout,
)
from custom_components.hostaway.api.rate_limit import (
    AGING_ADMISSIONS,
    AGING_SECONDS,
    FIRST_REFRESH_POLICY,
    INTERACTIVE_POLICY,
    SCHEDULED_POLICY,
    AccountRateLimiter,
    BucketId,
    EndpointBucketSpec,
    GateScope,
    ProviderSuppression,
    RequestContext,
    RequestPriority,
    SlidingWindowGate,
    Waiter,
    WaitPolicy,
    classify_request,
    current_request_context,
    register_endpoint_bucket,
    registered_endpoint_buckets,
    request_context,
    reset_shared_state,
    shared_ip_gate,
    shared_provider_suppression,
    start_first_refresh_context,
    start_interactive_context,
    start_scheduled_context,
    unregister_endpoint_bucket,
)
from tests.helpers import FakeClock


@pytest.fixture(autouse=True)
def _isolated_shared_state() -> None:
    """Keep process-wide limiter state from leaking between tests."""
    reset_shared_state()
    for bucket in list(registered_endpoint_buckets()):
        unregister_endpoint_bucket(bucket)


def make_limiter(
    clock: FakeClock,
    *,
    account_budget: int = 4,
    ip_budget: int = 4,
    account_key: str = "acct",
    startup_hold: bool = False,
) -> AccountRateLimiter:
    """Build a limiter wired to a fake clock with small, legible budgets.

    The startup hold is off by default: it is a correctness property of its
    own and halving every budget would obscure what the other tests assert.

    Args:
        clock: The fake clock driving both time and timers.
        account_budget: Budget for the account gate.
        ip_budget: Budget for the IP gate.
        account_key: Account identifier.
        startup_hold: Whether gates begin half-consumed.

    Returns:
        A limiter whose gates have no startup hold unless requested.
    """
    ip_gate = SlidingWindowGate(
        "ip",
        GateScope.IP,
        budget=ip_budget,
        created_at=clock.now(),
        startup_hold=startup_hold,
    )
    limiter = AccountRateLimiter(
        account_key,
        account_budget=account_budget,
        ip_gate=ip_gate,
        provider_suppression=ProviderSuppression(),
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    if not startup_hold:
        limiter._account_general_gate._synthetic_admissions.clear()
        limiter._account_general_gate._startup_hold_count = 0
    return limiter


class _Yield:
    """An awaitable that yields to the loop exactly once.

    A zero-delay sleep would do the same thing, but this suite bans the
    sleep API outright so that no test can ever come to depend on real
    elapsed time. Yielding is not timing: it only lets callbacks that are
    already due actually run.
    """

    def __await__(self) -> Generator[None]:
        """Yield control to the event loop once.

        Yields:
            None.
        """
        yield


async def settle() -> None:
    """Let the event loop run every callback that is already due."""
    for _ in range(5):
        await _Yield()


# ---------------------------------------------------------------------------
# T006 - priority, policy, and context primitives
# ---------------------------------------------------------------------------


def test_the_limiter_package_does_not_import_home_assistant() -> None:
    """The limiter core stays testable without Home Assistant.

    Every module in the package is checked, not just the entry point, so
    splitting the code further cannot quietly reintroduce the dependency.
    """
    package = Path(inspect.getfile(rate_limit)).parent
    modules = sorted(package.glob("*.py"))
    assert len(modules) >= 2
    for module in modules:
        assert "homeassistant" not in module.read_text(encoding="utf-8"), module.name


def test_interactive_sorts_ahead_of_scheduled() -> None:
    """Interactive work is ordered first."""
    assert RequestPriority.INTERACTIVE < RequestPriority.SCHEDULED


def test_policies_match_their_intent() -> None:
    """Only routine refreshes may be dropped when the budget runs out."""
    assert SCHEDULED_POLICY.shed_on_timeout is True
    assert INTERACTIVE_POLICY.shed_on_timeout is False
    assert FIRST_REFRESH_POLICY.shed_on_timeout is False
    assert SCHEDULED_POLICY.duration < INTERACTIVE_POLICY.duration


def test_wait_policy_rejects_non_positive_duration() -> None:
    """A policy that permits no waiting at all is a configuration error."""
    with pytest.raises(ValueError, match="must be positive"):
        WaitPolicy(duration=0.0, shed_on_timeout=False)


def test_context_deadline_is_fixed_once_for_the_operation() -> None:
    """A multi-call operation gets one deadline, not one per call."""
    ctx = RequestContext.start(RequestPriority.INTERACTIVE, INTERACTIVE_POLICY, 100.0)
    assert ctx.deadline == pytest.approx(130.0)
    assert ctx.remaining(120.0) == pytest.approx(10.0)
    assert ctx.remaining(200.0) == 0.0


def test_request_context_restores_the_previous_value() -> None:
    """Leaving a context block puts back whatever was ambient before."""
    outer = start_interactive_context(now=0.0)
    inner = start_scheduled_context(now=0.0)
    with request_context(outer):
        assert current_request_context(0.0) is outer
        with request_context(inner):
            assert current_request_context(0.0) is inner
        assert current_request_context(0.0) is outer


def test_request_context_restores_even_when_the_block_raises() -> None:
    """An exception inside the block must not leak the context outward."""
    ctx = start_interactive_context(now=0.0)
    with pytest.raises(RuntimeError), request_context(ctx):
        msg = "boom"
        raise RuntimeError(msg)
    assert current_request_context(0.0).priority is RequestPriority.SCHEDULED


def test_missing_context_degrades_to_cautious_defaults() -> None:
    """A forgotten context must not quietly gain interactive privileges."""
    ctx = current_request_context(50.0)
    assert ctx.priority is RequestPriority.SCHEDULED
    assert ctx.policy is SCHEDULED_POLICY
    assert ctx.deadline == pytest.approx(50.0 + SCHEDULED_POLICY.duration)


def test_context_helpers_carry_the_right_policies() -> None:
    """Each helper pairs the priority the caller means with its policy."""
    assert start_interactive_context(0.0).policy is INTERACTIVE_POLICY
    assert start_scheduled_context(0.0).policy is SCHEDULED_POLICY
    first = start_first_refresh_context(0.0)
    assert first.policy is FIRST_REFRESH_POLICY
    assert first.priority is RequestPriority.SCHEDULED


async def test_context_is_inherited_by_child_tasks() -> None:
    """A fan-out keeps the priority of the operation that started it."""
    ctx = start_interactive_context(0.0)
    observed: list[RequestPriority] = []

    async def child() -> None:
        """Record the priority visible inside a spawned task."""
        observed.append(current_request_context(0.0).priority)

    with request_context(ctx):
        await asyncio.gather(child(), child())

    assert observed == [RequestPriority.INTERACTIVE, RequestPriority.INTERACTIVE]


# ---------------------------------------------------------------------------
# T007 - the sliding window gate
# ---------------------------------------------------------------------------


def test_gate_admits_up_to_budget_then_blocks() -> None:
    """The window holds exactly as many admissions as the budget allows."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=3, startup_hold=False)
    for _ in range(3):
        assert gate.capacity_available(0.0)
        gate.record(0.0)
    assert not gate.capacity_available(0.0)


def test_gate_frees_capacity_as_the_window_slides() -> None:
    """Capacity returns exactly one window after the oldest admission."""
    gate = SlidingWindowGate(
        "a", GateScope.ACCOUNT, budget=2, window_seconds=10.0, startup_hold=False
    )
    gate.record(0.0)
    gate.record(4.0)
    assert not gate.capacity_available(5.0)
    assert gate.next_available(5.0) == pytest.approx(10.0)
    assert gate.capacity_available(10.1)
    assert gate.in_window(10.1) == 1


def test_gate_next_available_is_none_when_capacity_exists() -> None:
    """There is nothing to wait for when the gate can admit now."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=2, startup_hold=False)
    assert gate.next_available(0.0) is None


def test_gate_next_available_accounts_for_a_lowered_budget() -> None:
    """A budget cut below what is in flight needs more than one expiry."""
    gate = SlidingWindowGate(
        "a", GateScope.ACCOUNT, budget=4, window_seconds=10.0, startup_hold=False
    )
    for moment in (0.0, 1.0, 2.0, 3.0):
        gate.record(moment)
    gate.reconfigure(2)

    # Three of the four must age out before a fifth fits, so the wake must
    # be the third admission's expiry, not the first's.
    assert gate.next_available(4.0) == pytest.approx(12.0)
    assert not gate.capacity_available(11.5)
    assert gate.capacity_available(12.1)


def test_gate_rejects_an_out_of_range_budget() -> None:
    """A budget outside the documented range is refused up front."""
    with pytest.raises(ValueError, match="budget must be between"):
        SlidingWindowGate("a", GateScope.ACCOUNT, budget=0)
    with pytest.raises(ValueError, match="budget must be between"):
        SlidingWindowGate("a", GateScope.ACCOUNT, budget=RATE_LIMIT_CEILING + 1)


def test_gate_rejects_a_non_positive_window() -> None:
    """A window of zero length would make the counter meaningless."""
    with pytest.raises(ValueError, match="window_seconds must be positive"):
        SlidingWindowGate("a", GateScope.ACCOUNT, window_seconds=0.0)


def test_startup_hold_reserves_half_the_budget() -> None:
    """A fresh gate assumes it may have sent traffic before a restart."""
    gate = SlidingWindowGate(
        "a",
        GateScope.ACCOUNT,
        budget=10,
        window_seconds=10.0,
        created_at=0.0,
        startup_hold=True,
    )
    assert gate.in_window(0.0) == 5
    for _ in range(5):
        gate.record(0.0)
    assert not gate.capacity_available(0.0)


@pytest.mark.parametrize(("budget", "reserved"), [(1, 1), (3, 2), (9, 5), (10, 5)])
def test_startup_hold_rounds_the_reservation_up(budget: int, reserved: int) -> None:
    """An odd budget reserves the larger half, not the smaller one.

    Rounding down would leave a budget of 1 with no hold at all, which is
    precisely the case the hold exists for.
    """
    gate = SlidingWindowGate(
        "a",
        GateScope.ACCOUNT,
        budget=budget,
        created_at=0.0,
        startup_hold=True,
    )
    assert gate.in_window(0.0) == reserved


def test_startup_hold_expires_after_one_window() -> None:
    """The hold costs one window and then disappears on its own."""
    gate = SlidingWindowGate(
        "a",
        GateScope.ACCOUNT,
        budget=10,
        window_seconds=10.0,
        created_at=0.0,
        startup_hold=True,
    )
    assert gate.in_window(10.1) == 0
    assert gate.capacity_available(10.1)


def test_reconfigure_does_not_clear_existing_admissions() -> None:
    """Raising the budget must not forget what was already sent."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=2, startup_hold=False)
    gate.record(0.0)
    gate.record(0.0)
    gate.reconfigure(4)
    assert gate.in_window(0.0) == 2
    assert gate.capacity_available(0.0)


def test_reconfigure_below_the_window_count_simply_blocks() -> None:
    """Lowering the budget waits the window out instead of rewriting it."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=4, startup_hold=False)
    for _ in range(4):
        gate.record(0.0)
    gate.reconfigure(2)
    assert gate.in_window(0.0) == 4
    assert not gate.capacity_available(0.0)


def test_reconfigure_does_not_rearm_the_startup_hold() -> None:
    """An options change must not re-impose a restart's conservatism."""
    gate = SlidingWindowGate(
        "a",
        GateScope.ACCOUNT,
        budget=10,
        window_seconds=10.0,
        created_at=0.0,
        startup_hold=True,
    )
    assert gate.in_window(10.1) == 0
    gate.reconfigure(20)
    assert gate.in_window(10.1) == 0


def test_suppression_only_ever_grows() -> None:
    """A later, shorter backoff must not cut an earlier one short."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=2, startup_hold=False)
    gate.suppress_until(100.0)
    gate.suppress_until(50.0)
    assert gate.suppressed_until == pytest.approx(100.0)


def test_suppression_clears_once_it_expires() -> None:
    """Passing the deadline releases the gate."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=2, startup_hold=False)
    gate.suppress_until(10.0)
    assert gate.is_suppressed(5.0)
    assert not gate.is_suppressed(10.0)
    assert gate.suppressed_until is None


def test_reconcile_lowers_the_budget_but_never_raises_it() -> None:
    """The server's view may only ever make us more conservative."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=10, startup_hold=False)
    gate.reconcile(0.0, limit=4, remaining=None)
    assert gate.budget == 4
    gate.reconcile(0.0, limit=50, remaining=None)
    assert gate.budget == 4


def test_reconcile_adds_synthetic_admissions_for_a_shortfall() -> None:
    """Capacity we did not know was spent is charged to the window."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=10, startup_hold=False)
    gate.record(0.0)
    gate.reconcile(0.0, limit=None, remaining=2)
    assert gate.in_window(0.0) == 8
    assert gate.capacity_available(0.0)


@pytest.mark.parametrize("limit", [0, -5, None, True])
def test_reconcile_ignores_an_unusable_limit(limit: object) -> None:
    """A limit of zero would violate the gate's own budget invariant."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=10, startup_hold=False)
    gate.reconcile(0.0, limit=limit, remaining=None)  # type: ignore[arg-type]
    assert gate.budget == 10


@pytest.mark.parametrize("remaining", [-1, None, True])
def test_reconcile_ignores_an_unusable_remaining(remaining: object) -> None:
    """A negative remaining count cannot be satisfied, so it is dropped."""
    gate = SlidingWindowGate("a", GateScope.ACCOUNT, budget=10, startup_hold=False)
    gate.reconcile(0.0, limit=None, remaining=remaining)  # type: ignore[arg-type]
    assert gate.in_window(0.0) == 0


def test_reconcile_cannot_overfill_the_window_past_the_ceiling() -> None:
    """Synthetic admissions stop at the documented ceiling."""
    gate = SlidingWindowGate(
        "a", GateScope.ACCOUNT, budget=10, max_budget=12, startup_hold=False
    )
    gate.reconcile(0.0, limit=None, remaining=0)
    assert gate.in_window(0.0) <= 12


# ---------------------------------------------------------------------------
# T008 - classification and the endpoint registry
# ---------------------------------------------------------------------------


def test_general_requests_are_charged_to_both_counters() -> None:
    """Hostaway checks the account and the IP on every general call."""
    assert classify_request("GET", "/v1/listings") == (
        BucketId.ACCOUNT_GENERAL,
        BucketId.IP_GENERAL,
    )


def test_classification_is_pure() -> None:
    """The classifier takes no limiter state, so it cannot drift."""
    signature = inspect.signature(classify_request)
    assert list(signature.parameters) == ["method", "path"]


def test_endpoint_registry_is_inactive_by_default() -> None:
    """None of the documented endpoint buckets is in use today."""
    assert registered_endpoint_buckets() == {}
    assert len(rate_limit.ENDPOINT_BUCKET_CATALOG) == 3


def test_registering_a_bucket_diverts_matching_requests() -> None:
    """An endpoint counter replaces the general counters, not adds to them."""
    spec = next(
        s
        for s in rate_limit.ENDPOINT_BUCKET_CATALOG
        if s.bucket is BucketId.PRICE_DETAILS
    )
    register_endpoint_bucket(spec)
    assert classify_request("POST", "/v1/listings/42/calendar/priceDetails") == (
        BucketId.PRICE_DETAILS,
    )
    assert classify_request("GET", "/v1/listings/42/calendar/priceDetails") == (
        BucketId.ACCOUNT_GENERAL,
        BucketId.IP_GENERAL,
    )
    unregister_endpoint_bucket(spec.bucket)
    assert classify_request("POST", "/v1/listings/42/calendar/priceDetails") == (
        BucketId.ACCOUNT_GENERAL,
        BucketId.IP_GENERAL,
    )


def test_endpoint_pattern_does_not_match_across_path_segments() -> None:
    """A placeholder stands for one segment, not an arbitrary suffix."""
    spec = EndpointBucketSpec(
        bucket=BucketId.CONVERSATION_MESSAGES,
        method="POST",
        path_pattern="/v1/conversations/{id}/messages",
        window_seconds=60.0,
        max_budget=30,
    )
    assert spec.matches("post", "/v1/conversations/7/messages")
    assert not spec.matches("POST", "/v1/conversations/7/extra/messages")
    assert not spec.matches("POST", "/v1/conversations/7/messages/9")


# ---------------------------------------------------------------------------
# T009 - acquisition, queueing, and the pump
# ---------------------------------------------------------------------------


async def test_acquire_returns_immediately_when_capacity_exists() -> None:
    """The common path costs no timers and no loop iterations."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=2, ip_budget=2)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        await limiter.acquire("GET", "/v1/listings")
    assert limiter.snapshot().admitted_total == 2


async def test_acquire_queues_once_the_budget_is_spent() -> None:
    """The caller over budget waits rather than being refused by Hostaway."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
    await settle()
    assert not task.done()

    clock.advance(RATE_LIMIT_WINDOW_SECONDS + 0.1)
    await settle()
    assert task.done()
    await task
    assert limiter.snapshot().admitted_total == 2


async def test_a_request_is_charged_to_every_gate_it_touches() -> None:
    """One general request consumes both the account and the IP counter."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
    gates = limiter.snapshot().gates
    assert gates["account"].admitted_in_window == 1
    assert gates["ip"].admitted_in_window == 1


async def test_the_ip_gate_is_shared_between_accounts() -> None:
    """Two accounts on one host still share the host's IP counter."""
    clock = FakeClock()
    ip_gate = SlidingWindowGate(
        "ip", GateScope.IP, budget=1, created_at=0.0, startup_hold=False
    )
    suppression = ProviderSuppression()
    first = AccountRateLimiter(
        "a",
        account_budget=10,
        ip_gate=ip_gate,
        provider_suppression=suppression,
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    second = AccountRateLimiter(
        "b",
        account_budget=10,
        ip_gate=ip_gate,
        provider_suppression=suppression,
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    with request_context(start_interactive_context(clock.now())):
        await first.acquire("GET", "/v1/listings")
        task = asyncio.create_task(second.acquire("GET", "/v1/listings"))
    await settle()
    assert not task.done()
    clock.advance(RATE_LIMIT_WINDOW_SECONDS + 0.1)
    await settle()
    await task


async def test_interactive_work_is_admitted_before_scheduled_work() -> None:
    """A service call does not queue behind a routine refresh."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    order: list[str] = []

    async def caller(label: str, ctx: RequestContext) -> None:
        """Acquire under ``ctx`` and record the admission order."""
        with request_context(ctx):
            await limiter.acquire("GET", "/v1/listings")
        order.append(label)

    # Capacity has to return before the scheduled waiter ages, so this uses
    # a short suppression rather than waiting a whole window out.
    limiter.note_rate_limited("account", clock.now() + 0.5, "GET", "/v1/listings")

    scheduled = asyncio.create_task(
        caller(
            "scheduled",
            RequestContext.start(
                RequestPriority.SCHEDULED, FIRST_REFRESH_POLICY, clock.now()
            ),
        )
    )
    await settle()
    interactive = asyncio.create_task(
        caller("interactive", start_interactive_context(clock.now()))
    )
    await settle()
    assert not scheduled.done()

    clock.advance(0.6)
    await settle()
    await asyncio.gather(scheduled, interactive)
    assert order == ["interactive", "scheduled"]


async def test_a_waiting_queue_blocks_the_fast_path() -> None:
    """A late arrival cannot jump a queue that already formed."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        first = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
        second = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
    assert not first.done()
    assert not second.done()

    clock.advance(RATE_LIMIT_WINDOW_SECONDS + 0.1)
    await settle()
    assert first.done()
    await first
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second


async def test_an_aged_scheduled_waiter_is_promoted() -> None:
    """A refresh cannot be starved forever by interactive traffic."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    order: list[str] = []

    async def caller(label: str, ctx: RequestContext) -> None:
        """Acquire under ``ctx`` and record the admission order."""
        with request_context(ctx):
            await limiter.acquire("GET", "/v1/listings")
        order.append(label)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")

    scheduled = asyncio.create_task(
        caller(
            "scheduled",
            RequestContext.start(
                RequestPriority.SCHEDULED, FIRST_REFRESH_POLICY, clock.now()
            ),
        )
    )
    await settle()

    clock.advance(AGING_SECONDS + 0.1)
    await settle()
    interactive = asyncio.create_task(
        caller("interactive", start_interactive_context(clock.now()))
    )
    await settle()

    clock.advance(RATE_LIMIT_WINDOW_SECONDS)
    await settle()
    assert scheduled.done()
    assert not interactive.done()
    clock.advance(RATE_LIMIT_WINDOW_SECONDS)
    await settle()
    await asyncio.gather(scheduled, interactive)
    assert order == ["scheduled", "interactive"]


def test_the_aging_thresholds_are_both_defined() -> None:
    """Ageing has a time trigger and an overtaken-count trigger."""
    assert AGING_SECONDS > 0
    assert AGING_ADMISSIONS > 0


async def test_being_overtaken_ages_a_waiter_without_time_passing() -> None:
    """The second ageing trigger counts admissions rather than seconds.

    This is checked through ``acquire`` with the clock held completely
    still, so only the overtake counter can possibly fire. A pump that
    decided its order once and then admitted a whole backlog would let all
    the interactive waiters through and fail here.
    """
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=1, ip_budget=100)
    order: list[str] = []

    async def caller(label: str, ctx: RequestContext) -> None:
        """Acquire under ``ctx`` and record the admission order."""
        with request_context(ctx):
            await limiter.acquire("GET", "/v1/listings")
        order.append(label)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")

    scheduled = asyncio.create_task(
        caller(
            "scheduled",
            RequestContext.start(
                RequestPriority.SCHEDULED, FIRST_REFRESH_POLICY, clock.now()
            ),
        )
    )
    await settle()
    interactive = [
        asyncio.create_task(
            caller(f"interactive-{index}", start_interactive_context(clock.now()))
        )
        for index in range(AGING_ADMISSIONS + 5)
    ]
    await settle()

    # Raising the budget frees the whole backlog at one instant, so the
    # clock never moves and the time-based trigger cannot fire.
    limiter.configure(account_budget=100, effective_ip_budget=100)
    await settle()
    await asyncio.gather(scheduled, *interactive)
    assert order.index("scheduled") == AGING_ADMISSIONS


async def test_the_waiter_fields_used_for_aging_exist() -> None:
    """Both ageing inputs are recorded on the waiter itself."""
    fields = set(Waiter.__dataclass_fields__)
    assert {"queued_at", "interactive_admissions_at_queue"} <= fields


def test_waiters_never_compare_their_futures() -> None:
    """Heap ordering stops at the unique sequence number."""
    fields = {f.name: f.compare for f in Waiter.__dataclass_fields__.values()}
    assert fields["priority"] is True
    assert fields["sequence"] is True
    assert fields["future"] is False
    assert sum(1 for compare in fields.values() if compare) == 2


# ---------------------------------------------------------------------------
# T010 - operation-wide deadlines
# ---------------------------------------------------------------------------


async def test_a_scheduled_operation_sheds_at_its_deadline() -> None:
    """A refresh that cannot get capacity in time is dropped, not failed."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_scheduled_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    clock.advance(SCHEDULED_POLICY.duration + 0.1)
    await settle()
    with pytest.raises(HostawayRateLimitShedError):
        await task


async def test_an_interactive_operation_times_out_rather_than_sheds() -> None:
    """User-visible work must surface a failure instead of vanishing."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    # Suppression outlasting the deadline is the only way a 30s interactive
    # wait can actually expire: a sliding window alone always frees capacity
    # within one window.
    limiter.note_rate_limited(
        "account", clock.now() + INTERACTIVE_POLICY.duration * 2, "GET", "/v1/listings"
    )
    with request_context(start_interactive_context(clock.now())):
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    clock.advance(INTERACTIVE_POLICY.duration + 0.1)
    await settle()
    with pytest.raises(HostawayRateLimitWaitTimeout):
        await task


async def test_a_first_refresh_is_never_shed() -> None:
    """Shedding a first refresh would publish emptiness as though it were data."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited(
        "account",
        clock.now() + FIRST_REFRESH_POLICY.duration * 2,
        "GET",
        "/v1/listings",
    )
    with request_context(start_first_refresh_context(clock.now())):
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    clock.advance(FIRST_REFRESH_POLICY.duration + 0.1)
    await settle()
    with pytest.raises(HostawayRateLimitWaitTimeout):
        await task


async def test_the_deadline_covers_the_whole_operation() -> None:
    """Ten paginated calls share one budget of patience, not ten."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    ctx = start_scheduled_context(clock.now())
    with request_context(ctx):
        await limiter.acquire("GET", "/v1/reservations")
        clock.advance(SCHEDULED_POLICY.duration + 0.1)
        with pytest.raises(HostawayRateLimitShedError):
            await limiter.acquire("GET", "/v1/reservations")


async def test_an_expired_operation_fails_without_queueing() -> None:
    """There is no point joining a queue you can never reach the front of."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    ctx = RequestContext(
        priority=RequestPriority.SCHEDULED,
        policy=SCHEDULED_POLICY,
        deadline=clock.now(),
    )
    with request_context(ctx):
        await limiter.acquire("GET", "/v1/listings")
        with pytest.raises(HostawayRateLimitShedError):
            await limiter.acquire("GET", "/v1/listings")


async def test_the_shed_error_is_not_an_api_error() -> None:
    """A shed must not be convertible into a coordinator update failure."""
    from custom_components.hostaway.api.exceptions import HostawayApiError

    assert not issubclass(HostawayRateLimitShedError, HostawayApiError)


async def test_a_timed_out_waiter_does_not_consume_capacity() -> None:
    """Giving up must leave the budget exactly as it was."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_scheduled_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
    clock.advance(SCHEDULED_POLICY.duration + 0.1)
    await settle()
    with pytest.raises(HostawayRateLimitShedError):
        await task
    assert limiter.snapshot().admitted_total == 1


async def test_an_expired_waiter_does_not_spin_the_timer() -> None:
    """Expired waiters resolve before any re-arm, so the pump cannot loop."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_scheduled_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    # FakeClock raises if callbacks keep re-arming at one instant, so a
    # spin would fail this test rather than hang it.
    clock.advance(SCHEDULED_POLICY.duration * 3)
    await settle()
    with pytest.raises(HostawayRateLimitShedError):
        await task


# ---------------------------------------------------------------------------
# T011 - cancellation
# ---------------------------------------------------------------------------


async def test_cancelling_a_waiter_frees_the_slot_for_the_next() -> None:
    """A cancelled caller leaves no trace in the queue."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        first = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
        second = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await settle()

    clock.advance(RATE_LIMIT_WINDOW_SECONDS + 0.1)
    await settle()
    await second
    assert limiter.snapshot().admitted_total == 2


async def test_cancellation_leaves_the_budget_untouched() -> None:
    """Nothing was sent, so nothing should have been charged."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=2, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.snapshot().gates["account"].admitted_in_window == 2


# ---------------------------------------------------------------------------
# T012 - learning from a 429
# ---------------------------------------------------------------------------


async def test_an_account_429_suppresses_only_the_account_gate() -> None:
    """The server names the counter it applied; we believe it."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    limiter.note_rate_limited("account", clock.now() + 5.0, "GET", "/v1/listings")
    gates = limiter.snapshot().gates
    assert gates["account"].suppressed
    assert not gates["ip"].suppressed


async def test_an_ip_429_suppresses_only_the_ip_gate() -> None:
    """An IP refusal says nothing about the account's own allowance."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    limiter.note_rate_limited("ip", clock.now() + 5.0, "GET", "/v1/listings")
    gates = limiter.snapshot().gates
    assert gates["ip"].suppressed
    assert not gates["account"].suppressed


async def test_a_provider_429_holds_every_account() -> None:
    """A provider-wide refusal is not about any one account."""
    clock = FakeClock(start=1000.0)
    suppression = ProviderSuppression()
    ip_gate = SlidingWindowGate(
        "ip", GateScope.IP, budget=5, created_at=clock.now(), startup_hold=False
    )
    limiter = AccountRateLimiter(
        "a",
        account_budget=5,
        ip_gate=ip_gate,
        provider_suppression=suppression,
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    limiter.note_rate_limited("provider", clock.now() + 5.0, "GET", "/v1/listings")

    with request_context(start_interactive_context(clock.now())):
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
    await settle()
    assert not task.done()

    clock.advance(5.1)
    await settle()
    await task


async def test_a_suppressed_gate_blocks_admission_until_it_lifts() -> None:
    """Suppression beats available capacity."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", clock.now() + 5.0, "GET", "/v1/listings")

    with request_context(start_interactive_context(clock.now())):
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
    await settle()
    assert not task.done()

    clock.advance(5.1)
    await settle()
    await task


async def test_a_missing_retry_header_uses_the_default_backoff() -> None:
    """An absent deadline still has to produce a sensible pause."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", None, "GET", "/v1/listings")
    snapshot = limiter.snapshot().gates["account"]
    assert snapshot.suppressed_for_seconds == pytest.approx(DEFAULT_SUPPRESSION_SECONDS)


async def test_a_past_retry_timestamp_uses_the_default_backoff() -> None:
    """A timestamp already behind us is corrupt, not an instruction to resume."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", 1.0, "GET", "/v1/listings")
    snapshot = limiter.snapshot().gates["account"]
    assert snapshot.suppressed_for_seconds == pytest.approx(DEFAULT_SUPPRESSION_SECONDS)


async def test_an_absurd_retry_timestamp_is_capped_not_discarded() -> None:
    """A millisecond timestamp must not wedge the gate for millennia."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", 1_700_000_000_000.0, "GET", "/v1/listings")
    snapshot = limiter.snapshot().gates["account"]
    assert snapshot.suppressed_for_seconds == pytest.approx(MAX_SUPPRESSION_SECONDS)


async def test_a_long_but_valid_retry_deadline_is_honoured_in_full() -> None:
    """The gate keeps the server's delay even past the retry layer's ceiling."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", clock.now() + 120.0, "GET", "/v1/listings")
    snapshot = limiter.snapshot().gates["account"]
    assert snapshot.suppressed_for_seconds == pytest.approx(120.0)


async def test_429_counters_are_tallied_by_the_applied_counter() -> None:
    """Diagnostics should show which limit is actually being hit."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", None, "GET", "/v1/listings")
    limiter.note_rate_limited("account", None, "GET", "/v1/listings")
    limiter.note_rate_limited("ip", None, "GET", "/v1/listings")
    limiter.note_rate_limited("mystery", None, "GET", "/v1/listings")
    snapshot = limiter.snapshot()
    assert snapshot.rate_limited_total == 4
    assert snapshot.rate_limited_by_counter == {"account": 2, "ip": 1, "unknown": 1}


async def test_an_unknown_counter_suppresses_everything_it_touched() -> None:
    """An unrecognised counter name is handled conservatively."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("mystery", None, "GET", "/v1/listings")
    gates = limiter.snapshot().gates
    assert gates["account"].suppressed
    assert gates["ip"].suppressed


async def test_a_429_reconciles_the_reported_budget_downward() -> None:
    """A 429 carries the server's own figures; we should use them."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=100, ip_budget=100)
    limiter.note_rate_limited(
        "account", None, "GET", "/v1/listings", limit=40, remaining=0
    )
    account = limiter.snapshot().gates["account"]
    assert account.effective_budget == 40
    assert account.budget == 100


# ---------------------------------------------------------------------------
# T013 - reconfiguration
# ---------------------------------------------------------------------------


async def test_configure_applies_both_budgets() -> None:
    """Options changes take effect without a reload."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    limiter.configure(account_budget=9, effective_ip_budget=7)
    gates = limiter.snapshot().gates
    assert gates["account"].budget == 9
    assert gates["ip"].budget == 7


async def test_configure_preserves_in_flight_admissions() -> None:
    """Changing a budget must not forget requests already sent."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        await limiter.acquire("GET", "/v1/listings")
    limiter.configure(account_budget=20, effective_ip_budget=20)
    assert limiter.snapshot().gates["account"].admitted_in_window == 2


async def test_configure_applies_both_budgets_before_admitting() -> None:
    """A waiter must never be released against half-applied budgets.

    Raising the account budget while lowering the IP budget is the
    combination that exposes it: notifying between the two mutations would
    let the waiter through on the new account budget and the old IP one.
    """
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    limiter.configure(account_budget=5, effective_ip_budget=1)
    await settle()
    assert not task.done()
    gates = limiter.snapshot().gates
    assert gates["ip"].admitted_in_window <= gates["ip"].effective_budget

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("budget", [0, -1, RATE_LIMIT_CEILING + 1])
async def test_configure_rejects_an_out_of_range_budget(budget: int) -> None:
    """An impossible budget is refused rather than clamped silently."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    with pytest.raises(ValueError, match="rate limit budget must be between"):
        limiter.configure(account_budget=budget, effective_ip_budget=4)


async def test_raising_the_budget_releases_waiting_callers() -> None:
    """A reconfigure re-pumps rather than leaving callers stuck."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
    assert not task.done()

    limiter.configure(account_budget=5, effective_ip_budget=5)
    await settle()
    await task


# ---------------------------------------------------------------------------
# T014 - statistics and snapshots
# ---------------------------------------------------------------------------


async def test_an_endpoint_bucket_does_not_consume_the_general_counters() -> None:
    """An endpoint counter is charged instead of, never as well as, the rest."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=4)
    spec = EndpointBucketSpec(
        bucket=BucketId.PRICE_DETAILS,
        method="POST",
        path_pattern="/v1/listings/{id}/calendar/priceDetails",
        window_seconds=10.0,
        max_budget=6,
    )
    register_endpoint_bucket(spec)

    # Three, not six: an endpoint gate gets the same startup hold as any
    # other, so half its budget is reserved for its first window.
    with request_context(start_interactive_context(clock.now())):
        for _ in range(3):
            await limiter.acquire("POST", "/v1/listings/9/calendar/priceDetails")

    gates = limiter.snapshot().gates
    assert gates["account"].admitted_in_window == 0
    assert gates["ip"].admitted_in_window == 0
    assert gates[BucketId.PRICE_DETAILS.value].admitted_in_window == 6


async def test_an_endpoint_waiter_is_not_blocked_by_a_full_general_gate() -> None:
    """Separate counters mean separate queues, not one head-of-line stall."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=1)
    spec = EndpointBucketSpec(
        bucket=BucketId.PRICE_DETAILS,
        method="POST",
        path_pattern="/v1/listings/{id}/calendar/priceDetails",
        window_seconds=10.0,
        max_budget=6,
    )
    register_endpoint_bucket(spec)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        blocked = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
        endpoint = asyncio.create_task(
            limiter.acquire("POST", "/v1/listings/9/calendar/priceDetails")
        )
        await settle()

    assert endpoint.done()
    await endpoint
    assert not blocked.done()
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked


async def test_the_snapshot_counts_waiters_against_their_own_gates() -> None:
    """A waiter is only queued on the counters its request is charged to."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    spec = EndpointBucketSpec(
        bucket=BucketId.PRICE_DETAILS,
        method="POST",
        path_pattern="/v1/listings/{id}/calendar/priceDetails",
        window_seconds=10.0,
        # The gate's own startup hold reserves half of this, so a budget of
        # two leaves exactly one slot: enough to admit one and queue one.
        max_budget=2,
    )
    register_endpoint_bucket(spec)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("POST", "/v1/listings/9/calendar/priceDetails")
        endpoint_waiter = asyncio.create_task(
            limiter.acquire("POST", "/v1/listings/9/calendar/priceDetails")
        )
        await settle()
        await limiter.acquire("GET", "/v1/listings")
        general_waiter = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    gates = limiter.snapshot().gates
    # The endpoint waiter bypasses the general counters entirely, and the
    # general waiter has nothing to do with the endpoint counter, so each
    # gate must report exactly one.
    assert gates["account"].waiting_interactive == 1
    assert gates["ip"].waiting_interactive == 1
    assert gates[BucketId.PRICE_DETAILS.value].waiting_interactive == 1

    for task in (endpoint_waiter, general_waiter):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_closing_a_limiter_fails_everything_still_queued() -> None:
    """Nothing can admit a waiter after close, so nothing may stay pending."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=1)

    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        queued = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()

    assert not queued.done()
    limiter.close()
    with pytest.raises(HostawayRateLimitWaitTimeout):
        await queued


async def test_a_closed_limiter_refuses_new_callers_immediately() -> None:
    """Queueing on a detached limiter would wait for a pump that never runs."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.close()

    with (
        request_context(start_scheduled_context(clock.now())),
        pytest.raises(HostawayRateLimitShedError),
    ):
        await limiter.acquire("GET", "/v1/listings")


async def test_configuring_the_ip_gate_resizes_its_startup_hold() -> None:
    """The shared gate is built before anyone knows the effective budget."""
    reset_shared_state()
    clock = FakeClock()
    ip_gate = shared_ip_gate(clock.now())
    limiter = AccountRateLimiter(
        "acct",
        account_budget=50,
        ip_gate=ip_gate,
        provider_suppression=ProviderSuppression(),
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    limiter.configure(account_budget=50, effective_ip_budget=50)

    # Half of 50, not half of the default the gate was born with.
    assert ip_gate.in_window(clock.now()) == 25
    limiter.close()
    reset_shared_state()


async def test_the_shared_ip_hold_is_dated_in_the_limiters_clock_domain() -> None:
    """A hold dated to zero would prune instantly on a real monotonic clock."""
    reset_shared_state()
    clock = FakeClock(start=86_400.0)
    limiter = make_limiter_on_shared_gate(clock)

    # The hold must still occupy the window a whole day into the process,
    # not have aged out the moment anyone first asked for capacity.
    assert limiter._ip_general_gate.in_window(clock.now()) > 0
    limiter.close()
    reset_shared_state()


def make_limiter_on_shared_gate(clock: FakeClock) -> AccountRateLimiter:
    """Build a limiter that adopts the process-wide IP gate.

    Args:
        clock: The fake clock driving both time and timers.

    Returns:
        A limiter whose IP gate is the shared singleton.
    """
    return AccountRateLimiter(
        "acct",
        provider_suppression=ProviderSuppression(),
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )


async def test_closing_a_limiter_detaches_it_from_shared_state() -> None:
    """A removed entry must not keep pumping, or keep its key alive."""
    clock = FakeClock()
    ip_gate = SlidingWindowGate(
        "ip", GateScope.IP, budget=5, created_at=0.0, startup_hold=False
    )
    suppression = ProviderSuppression()
    limiter = AccountRateLimiter(
        "a",
        account_budget=5,
        ip_gate=ip_gate,
        provider_suppression=suppression,
        clock=clock.now,
        schedule=clock.schedule,
        wall_clock=clock.now,
    )
    assert ip_gate._listeners
    limiter.close()
    assert not ip_gate._listeners
    assert not suppression._listeners
    limiter.close()


async def test_a_provider_429_is_visible_to_every_limiter() -> None:
    """Provider suppression is process-wide, not per account."""
    clock = FakeClock(start=1000.0)
    ip_gate = SlidingWindowGate(
        "ip", GateScope.IP, budget=10, created_at=0.0, startup_hold=False
    )
    suppression = ProviderSuppression()
    limiters = [
        AccountRateLimiter(
            key,
            account_budget=10,
            ip_gate=ip_gate,
            provider_suppression=suppression,
            clock=clock.now,
            schedule=clock.schedule,
            wall_clock=clock.now,
        )
        for key in ("a", "b")
    ]
    limiters[0].note_rate_limited("provider", clock.now() + 5.0, "GET", "/v1/listings")

    with request_context(start_interactive_context(clock.now())):
        task = asyncio.create_task(limiters[1].acquire("GET", "/v1/listings"))
    await settle()
    assert not task.done()

    clock.advance(5.1)
    await settle()
    await task


async def test_an_unrepresentable_retry_timestamp_is_capped() -> None:
    """A value too large to be a float must cap, not raise."""
    clock = FakeClock(start=1000.0)
    limiter = make_limiter(clock, account_budget=5, ip_budget=5)
    limiter.note_rate_limited("account", 10**400, "GET", "/v1/listings")
    snapshot = limiter.snapshot().gates["account"]
    assert snapshot.suppressed_for_seconds == pytest.approx(MAX_SUPPRESSION_SECONDS)


async def test_the_snapshot_carries_no_account_identifier() -> None:
    """The account key is the client id, so it must never be published."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_key="secret-client-id")
    snapshot = limiter.snapshot()
    assert "secret-client-id" not in repr(snapshot)


async def test_the_snapshot_reports_each_gate_separately() -> None:
    """Operators need to see which counter is the binding one."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=4, ip_budget=6)
    gates = limiter.snapshot().gates
    assert set(gates) == {"account", "ip"}
    assert gates["account"].budget == 4
    assert gates["ip"].budget == 6
    assert gates["ip"].window_seconds == pytest.approx(RATE_LIMIT_WINDOW_SECONDS)


async def test_the_snapshot_counts_waiters_by_priority() -> None:
    """Queue depth per priority is what tells an operator to raise a budget."""
    clock = FakeClock()
    limiter = make_limiter(clock, account_budget=1, ip_budget=5)
    with request_context(start_interactive_context(clock.now())):
        await limiter.acquire("GET", "/v1/listings")
        task = asyncio.create_task(limiter.acquire("GET", "/v1/listings"))
        await settle()
    assert limiter.snapshot().gates["account"].waiting_interactive == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_shed_counts_are_recorded() -> None:
    """A coordinator reporting a drop shows up in diagnostics."""
    clock = FakeClock()
    limiter = make_limiter(clock)
    limiter.note_shed()
    limiter.note_shed()
    assert limiter.snapshot().shed_total == 2


# ---------------------------------------------------------------------------
# T015 - the whole point: no 429s under sustained overload
# ---------------------------------------------------------------------------


async def test_sustained_overload_never_exceeds_the_budget() -> None:
    """Far more demand than budget still produces no over-window instant.

    This is the success criterion the whole feature exists for: with
    enforcement on, the integration's own traffic cannot reach the rate at
    which Hostaway would refuse it.
    """
    clock = FakeClock()
    budget = 10
    window = RATE_LIMIT_WINDOW_SECONDS
    limiter = make_limiter(clock, account_budget=budget, ip_budget=budget)
    sent: list[float] = []

    async def caller() -> None:
        """Acquire once under a generous deadline and log the send time."""
        ctx = RequestContext(
            priority=RequestPriority.SCHEDULED,
            policy=FIRST_REFRESH_POLICY,
            deadline=clock.now() + 10_000.0,
        )
        with request_context(ctx):
            await limiter.acquire("GET", "/v1/listings")
        sent.append(clock.now())

    tasks = [asyncio.create_task(caller()) for _ in range(60)]
    await settle()
    for _ in range(20):
        clock.advance(window / 2)
        await settle()
    await asyncio.gather(*tasks)

    assert len(sent) == 60
    server = FakeHostaway(budget, window)
    for moment in sent:
        assert server.request(moment), "the limiter let a refusable request through"
    assert server.refused == 0


class FakeHostaway:
    """The smallest model of Hostaway's sliding-window counter.

    It exists so the overload simulation can be run with and without
    enforcement and compared against the same judge.
    """

    def __init__(self, budget: int, window: float) -> None:
        """Create a server that refuses anything over ``budget`` per window.

        Args:
            budget: Requests permitted per window.
            window: Length of the sliding window in seconds.
        """
        self.budget = budget
        self.window = window
        self.seen: list[float] = []
        self.refused = 0

    def request(self, now: float) -> bool:
        """Send one request at ``now``.

        Args:
            now: Monotonic time of the request.

        Returns:
            True if accepted, False if the server would answer 429.
        """
        self.seen = [t for t in self.seen if t > now - self.window]
        if len(self.seen) >= self.budget:
            self.refused += 1
            return False
        self.seen.append(now)
        return True


async def test_the_same_load_without_enforcement_is_refused() -> None:
    """The simulation only means something if the unpaced case really fails.

    The identical workload is replayed against the same server model with
    no pacing at all, which is what the integration did before this
    feature.
    """
    budget = 10
    window = RATE_LIMIT_WINDOW_SECONDS
    server = FakeHostaway(budget, window)
    for _ in range(60):
        server.request(0.0)
    assert server.refused == 50


# ---------------------------------------------------------------------------
# Shared process-wide state
# ---------------------------------------------------------------------------


def test_the_ip_gate_and_provider_state_are_process_wide_singletons() -> None:
    """Every limiter in the process must see the same host-level counter."""
    assert shared_ip_gate() is shared_ip_gate()
    assert shared_provider_suppression() is shared_provider_suppression()
    first = shared_ip_gate()
    reset_shared_state()
    assert shared_ip_gate() is not first


def test_a_limiter_defaults_to_the_shared_state() -> None:
    """Forgetting to pass the shared gate must not create a private one."""
    limiter = AccountRateLimiter("a")
    assert limiter._ip_general_gate is shared_ip_gate()
    assert limiter._provider_suppression is shared_provider_suppression()


# ---------------------------------------------------------------------------
# Limiter label (T034)
# ---------------------------------------------------------------------------


def test_a_limiter_label_is_stable_for_its_lifetime() -> None:
    """Diagnostics pulled twice must name the same limiter the same way."""
    limiter = AccountRateLimiter("a")
    assert limiter.label == limiter.label


def test_two_limiters_for_the_same_account_get_different_labels() -> None:
    """The label is allocated, not derived.

    A Hostaway client id is a short numeric account identifier, so any
    digest of it would be an offline-testable verifier for half the
    credential. Two limiters built from the same key must therefore not
    agree on a label - including across processes, which is what this
    asserts in miniature.
    """
    first = AccountRateLimiter("same-account")
    second = AccountRateLimiter("same-account")

    assert first.label != second.label


def test_a_label_reveals_nothing_about_the_account_key() -> None:
    """The label must not contain, or be, the key it belongs to."""
    limiter = AccountRateLimiter("12345678")

    assert limiter.label != limiter.account_key
    assert limiter.account_key not in limiter.label
    assert len(limiter.label) == 12
    assert set(limiter.label) <= set("0123456789abcdef")
