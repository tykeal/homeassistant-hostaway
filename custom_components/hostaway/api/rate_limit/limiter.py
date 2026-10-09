# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
# aislop-ignore-file complexity/file-too-large -- one cohesive admission
# decision point; splitting the class would scatter the invariant it holds
"""The limiter itself: one admission decision point per account.

Admission is a *rate* reservation rather than a concurrency slot. There is no
release, because a request that has finished does not give anything back; the
sliding window returns the capacity on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import logging
import math
import time
from collections.abc import Callable

from ..const import (
    DEFAULT_RATE_LIMIT_BUDGET,
    DEFAULT_SUPPRESSION_SECONDS,
    MAX_SUPPRESSION_SECONDS,
    RATE_LIMIT_CEILING,
)
from .buckets import (
    BucketId,
    GateScope,
    classify_request,
    registered_endpoint_buckets,
)
from .context import (
    RequestContext,
    RequestPriority,
    current_request_context,
)
from .gates import BudgetGate, SlidingWindowGate, gate_suppressed
from .queueing import (
    NEVER_AWAITED,
    GateSnapshot,
    LimiterSnapshot,
    LimiterStats,
    Waiter,
)
from .suppression import ProviderSuppression

_LOGGER = logging.getLogger(__name__)

TimerHandle = object
TimerScheduler = Callable[[float, Callable[[], None]], TimerHandle]

#: A scheduled waiter is promoted ahead of interactive traffic once it has
#: waited this long, so a steady interactive stream cannot starve refreshes.
AGING_SECONDS: float = 1.0

#: ...or once this many interactive callers have been admitted ahead of it,
#: whichever happens first.
AGING_ADMISSIONS: int = 20


def _default_schedule(delay: float, callback: Callable[[], None]) -> TimerHandle:
    """Arm a timer on the running event loop.

    Args:
        delay: Seconds until the callback should run.
        callback: Zero-argument callable to invoke.

    Returns:
        The loop's timer handle.
    """
    return asyncio.get_running_loop().call_later(max(delay, 0.0), callback)


_SHARED_IP_GATE: SlidingWindowGate | None = None
_SHARED_PROVIDER_SUPPRESSION: ProviderSuppression | None = None


def shared_ip_gate() -> SlidingWindowGate:
    """Return the process-wide IP gate, creating it on first use.

    Hostaway counts per originating IP address, which every limiter in the
    process shares regardless of account.

    Returns:
        The shared gate.
    """
    global _SHARED_IP_GATE
    if _SHARED_IP_GATE is None:
        _SHARED_IP_GATE = SlidingWindowGate("ip", GateScope.IP)
    return _SHARED_IP_GATE


def shared_provider_suppression() -> ProviderSuppression:
    """Return the process-wide provider suppression state.

    Returns:
        The shared suppression holder.
    """
    global _SHARED_PROVIDER_SUPPRESSION
    if _SHARED_PROVIDER_SUPPRESSION is None:
        _SHARED_PROVIDER_SUPPRESSION = ProviderSuppression()
    return _SHARED_PROVIDER_SUPPRESSION


def reset_shared_state() -> None:
    """Discard the process-wide gate and suppression state.

    Exists for tests, which must not inherit one another's windows.
    """
    global _SHARED_IP_GATE, _SHARED_PROVIDER_SUPPRESSION
    _SHARED_IP_GATE = None
    _SHARED_PROVIDER_SUPPRESSION = None


class AccountRateLimiter:
    """Paces one Hostaway account's traffic against its counters.

    One instance exists per account. It owns the account's general gate and
    shares the IP gate and provider suppression with every other limiter in
    the process.
    """

    def __init__(
        self,
        account_key: str,
        *,
        account_budget: int = DEFAULT_RATE_LIMIT_BUDGET,
        ip_gate: SlidingWindowGate | None = None,
        provider_suppression: ProviderSuppression | None = None,
        clock: Callable[[], float] = time.monotonic,
        schedule: TimerScheduler | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Create a limiter.

        Args:
            account_key: The Hostaway client id this limiter paces.
            account_budget: Admissions permitted per window for the account.
            ip_gate: Shared IP gate; defaults to the process-wide one.
            provider_suppression: Shared provider state; defaults to the
                process-wide one.
            clock: Monotonic clock, injected for testing.
            schedule: Timer hook, injected for testing.
            wall_clock: Wall clock, used only to interpret the server's
                absolute ``Retry-After`` timestamps.
        """
        self.account_key = account_key
        self._clock = clock
        self._wall_clock = wall_clock
        self._schedule = schedule or _default_schedule
        self._account_general_gate = SlidingWindowGate(
            "account",
            GateScope.ACCOUNT,
            budget=account_budget,
            created_at=clock(),
        )
        self._ip_general_gate = ip_gate if ip_gate is not None else shared_ip_gate()
        self._provider_suppression = (
            provider_suppression
            if provider_suppression is not None
            else shared_provider_suppression()
        )
        self._endpoint_gates: dict[BucketId, SlidingWindowGate] = {}
        self._waiters: list[Waiter] = []
        self._sequence = 0
        self._timer: TimerHandle | None = None
        self._interactive_admissions = 0
        self._closed = False
        self._stats = LimiterStats()

        self._account_general_gate.add_listener(self._pump)
        self._ip_general_gate.add_listener(self._pump)
        self._provider_suppression.add_listener(self._pump)

    def _gates_for(self, method: str, path: str) -> tuple[BudgetGate, ...]:
        """Resolve a request's symbolic buckets to this limiter's gates.

        Args:
            method: HTTP method.
            path: Request path.

        Returns:
            Every gate the request must satisfy.
        """
        return tuple(
            self._gate_for_bucket(bucket) for bucket in classify_request(method, path)
        )

    def _gate_for_bucket(self, bucket: BucketId) -> BudgetGate:
        """Return the gate object a symbolic bucket refers to.

        Args:
            bucket: Symbolic bucket identifier.

        Returns:
            The gate instance, created on first use for endpoint buckets.

        Raises:
            KeyError: If an endpoint bucket is not registered.
        """
        if bucket is BucketId.ACCOUNT_GENERAL:
            return self._account_general_gate
        if bucket is BucketId.IP_GENERAL:
            return self._ip_general_gate
        gate = self._endpoint_gates.get(bucket)
        if gate is None:
            spec = registered_endpoint_buckets()[bucket]
            gate = SlidingWindowGate(
                spec.bucket.value,
                GateScope.ENDPOINT,
                budget=spec.max_budget,
                max_budget=spec.max_budget,
                window_seconds=spec.window_seconds,
                created_at=self._clock(),
            )
            gate.add_listener(self._pump)
            self._endpoint_gates[bucket] = gate
        return gate

    async def acquire(self, method: str, path: str) -> None:
        """Wait until this request may be sent.

        Returns immediately, without touching the event loop's timers, when
        every selected gate has room. Otherwise the caller queues and is
        resolved by the pump.

        Args:
            method: HTTP method.
            path: Request path.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation's deadline
                expires while queued, or if the limiter is closed.
            HostawayRateLimitWaitTimeout: If any other operation's deadline
                expires while queued, or if the limiter is closed.
        """
        now = self._clock()
        ctx = current_request_context(now)
        if self._closed:
            # A closed limiter no longer hears from the shared gates, so a
            # caller that queued here would wait forever. Failing with the
            # caller's own timeout policy keeps teardown on paths every
            # caller already handles.
            raise self._timeout_for(ctx, waited=0.0)
        gates = self._gates_for(method, path)

        if self._can_admit_now(gates, now):
            self._admit(gates, ctx.priority, now)
            return

        if ctx.remaining(now) <= 0:
            raise self._timeout_for(ctx, waited=0.0)

        self._sequence += 1
        waiter = Waiter(
            priority=ctx.priority,
            sequence=self._sequence,
            queued_at=now,
            deadline=ctx.deadline,
            shed_on_timeout=ctx.policy.shed_on_timeout,
            method=method,
            path=path,
            interactive_admissions_at_queue=self._interactive_admissions,
            future=asyncio.get_running_loop().create_future(),
        )
        heapq.heappush(self._waiters, waiter)
        self._pump()

        try:
            await waiter.future
        except asyncio.CancelledError:
            # A caller that gives up before admission has consumed nothing.
            # If the pump already resolved it in this same iteration the
            # admission is recorded and stays recorded: un-recording would
            # over-admit against a server that never heard about the
            # cancellation.
            self._remove_waiter(waiter)
            self._pump()
            raise
        finally:
            self._record_wait(ctx.priority, self._clock() - waiter.queued_at)

    def _can_admit_now(self, gates: tuple[BudgetGate, ...], now: float) -> bool:
        """Report whether a request could be admitted this instant.

        Args:
            gates: The gates the request must satisfy.
            now: Current monotonic time.

        Returns:
            True when nothing is queued ahead, nothing is suppressed, and
            every gate has room.
        """
        if self._waiters or self._provider_suppression.active(now):
            return False
        return all(
            not gate_suppressed(gate, now) and gate.capacity_available(now)
            for gate in gates
        )

    def _admit(
        self,
        gates: tuple[BudgetGate, ...],
        priority: RequestPriority,
        now: float,
    ) -> None:
        """Record an admission against every selected gate.

        Args:
            gates: The gates the request is charged to.
            priority: Ordering class of the admitted caller.
            now: Current monotonic time.
        """
        for gate in gates:
            gate.record(now)
        self._stats.admitted_total += 1
        if priority is RequestPriority.INTERACTIVE:
            self._interactive_admissions += 1

    def _record_wait(self, priority: RequestPriority, waited: float) -> None:
        """Accumulate time spent queued, for diagnostics.

        Args:
            priority: Ordering class of the caller.
            waited: Seconds spent queued.
        """
        if priority is RequestPriority.INTERACTIVE:
            self._stats.interactive_wait_total += waited
        else:
            self._stats.scheduled_wait_total += waited

    def _timeout_for(self, ctx: RequestContext, *, waited: float) -> Exception:
        """Build the error an operation fails with when its deadline expires.

        Args:
            ctx: The operation's context.
            waited: Real seconds spent queued.

        Returns:
            A shed signal or a wait timeout, per the policy.
        """
        placeholder = Waiter(
            priority=ctx.priority,
            sequence=-1,
            queued_at=0.0,
            deadline=ctx.deadline,
            shed_on_timeout=ctx.policy.shed_on_timeout,
            method="",
            path="",
            interactive_admissions_at_queue=0,
            future=NEVER_AWAITED,
        )
        return placeholder.timeout_exception(waited)

    def _remove_waiter(self, waiter: Waiter) -> None:
        """Drop a waiter from the queue, restoring the heap invariant.

        Args:
            waiter: The waiter to remove.
        """
        with contextlib.suppress(ValueError):
            self._waiters.remove(waiter)
            heapq.heapify(self._waiters)

    def _pump(self) -> None:
        """Resolve as many queued waiters as the gates currently allow.

        Expired waiters are resolved first and unconditionally. Without
        that, the shared timer would wake at an already-elapsed deadline,
        find nothing it could act on, and re-arm at the same instant,
        spinning in a zero-delay callback loop.
        """
        self._cancel_timer()
        if not self._waiters:
            return

        now = self._clock()
        self._resolve_expired(now)
        if not self._waiters:
            return

        wakeups: list[float] = [w.deadline for w in self._waiters]

        if self._provider_suppression.active(now):
            if self._provider_suppression.until is not None:
                wakeups.append(self._provider_suppression.until)
            self._arm_timer(min(wakeups), now)
            return

        # Admission order is re-derived after every admission rather than
        # snapshotted once. The aging rule counts interactive admissions,
        # and those happen inside this very loop, so a frozen order could
        # let a whole pump's worth of interactive traffic overtake a
        # scheduled waiter that had already earned promotion.
        blocked: set[int] = set()
        while True:
            candidate = self._next_candidate(now, blocked)
            if candidate is None:
                break
            gates = self._gates_for(candidate.method, candidate.path)

            # A gate that is not suppressed has no deadline at all, so the
            # Nones are filtered out before anything is compared.
            deadlines = [
                gate.suppressed_until
                for gate in gates
                if gate_suppressed(gate, now) and gate.suppressed_until is not None
            ]
            if deadlines:
                wakeups.append(max(deadlines))
                blocked.add(candidate.sequence)
                continue

            if not all(gate.capacity_available(now) for gate in gates):
                frees = [gate.next_available(now) for gate in gates]
                future_frees = [f for f in frees if f is not None]
                if future_frees:
                    wakeups.append(max(future_frees))
                blocked.add(candidate.sequence)
                continue

            self._remove_waiter(candidate)
            self._admit(gates, candidate.priority, now)
            if not candidate.future.done():
                candidate.future.set_result(None)

        if self._waiters:
            self._arm_timer(min(w for w in wakeups if w > now), now)

    def _next_candidate(self, now: float, blocked: set[int]) -> Waiter | None:
        """Return the waiter the pump should consider next.

        Args:
            now: Current monotonic time.
            blocked: Sequence numbers already found unable to proceed in
                this pump run, so the loop cannot reconsider them forever.

        Returns:
            The next waiter to try, or ``None`` when none is left.
        """
        return next(
            (w for w in self._ordered_candidates(now) if w.sequence not in blocked),
            None,
        )

    def _resolve_expired(self, now: float) -> None:
        """Fail every waiter whose operation deadline has passed.

        Args:
            now: Current monotonic time.
        """
        for waiter in list(self._waiters):
            if waiter.future.done():
                self._remove_waiter(waiter)
                continue
            if now >= waiter.deadline:
                self._remove_waiter(waiter)
                waiter.future.set_exception(
                    waiter.timeout_exception(now - waiter.queued_at)
                )

    def _ordered_candidates(self, now: float) -> list[Waiter]:
        """Order queued waiters for consideration.

        Priority decides, except that a scheduled waiter which has aged past
        the threshold is promoted ahead of interactive traffic. Without that
        promotion a steady stream of service calls could starve refreshes
        indefinitely.

        Args:
            now: Current monotonic time.

        Returns:
            The waiters in the order the pump should consider them.
        """
        ordered = sorted(self._waiters)
        aged = [
            w
            for w in ordered
            if w.priority is RequestPriority.SCHEDULED and self._has_aged(w, now)
        ]
        if not aged or not ordered or ordered[0].priority is RequestPriority.SCHEDULED:
            return ordered
        promoted = aged[0]
        return [promoted, *[w for w in ordered if w is not promoted]]

    def _has_aged(self, waiter: Waiter, now: float) -> bool:
        """Report whether a scheduled waiter has waited long enough to jump.

        Args:
            waiter: The queued scheduled waiter.
            now: Current monotonic time.

        Returns:
            True once either ageing threshold is met.
        """
        if now - waiter.queued_at >= AGING_SECONDS:
            return True
        queued_at_count = waiter.interactive_admissions_at_queue
        overtaken = self._interactive_admissions - queued_at_count
        return overtaken >= AGING_ADMISSIONS

    def _arm_timer(self, when: float, now: float) -> None:
        """Arm the single shared timer.

        Arming replaces any existing timer rather than stacking, so an idle
        limiter costs no event-loop wakeups at all.

        Args:
            when: Monotonic instant to wake at.
            now: Current monotonic time.
        """
        self._cancel_timer()
        self._timer = self._schedule(max(when - now, 0.0), self._on_timer)

    def _cancel_timer(self) -> None:
        """Cancel the shared timer if one is armed."""
        if self._timer is not None:
            cancel = getattr(self._timer, "cancel", None)
            if callable(cancel):
                cancel()
            self._timer = None

    def _on_timer(self) -> None:
        """Run the pump after a scheduled wake-up."""
        self._timer = None
        self._pump()

    def note_rate_limited(
        self,
        applied_counter: str | None,
        retry_at: float | None,
        method: str,
        path: str,
        limit: int | None = None,
        remaining: int | None = None,
    ) -> None:
        """Record a 429 and suppress whichever counter the server named.

        Args:
            applied_counter: Hostaway's ``X-RateLimit-Applied`` value.
            retry_at: ``X-RateLimit-Retry-After`` as a Unix timestamp.
            method: HTTP method of the refused request.
            path: Path of the refused request.
            limit: ``X-RateLimit-Limit``, if present.
            remaining: ``X-RateLimit-Remaining``, if present.
        """
        now = self._clock()
        delay = self._suppression_delay(retry_at)
        counter = (applied_counter or "unknown").strip().lower()

        self._stats.rate_limited_total += 1
        bucket = counter if counter in _KNOWN_COUNTERS else "unknown"
        self._stats.rate_limited_by_counter[bucket] = (
            self._stats.rate_limited_by_counter.get(bucket, 0) + 1
        )

        if counter == "provider":
            self._provider_suppression.suppress_until(now + delay)
            return

        gates = self._gates_for(method, path)
        targets: tuple[BudgetGate, ...]
        if counter == "account":
            targets = (self._account_general_gate,)
        elif counter == "ip":
            targets = (self._ip_general_gate,)
        elif counter == "endpoint":
            endpoint_gates = tuple(
                gate for gate in gates if gate.scope is GateScope.ENDPOINT
            )
            # An endpoint 429 for a request we classify as general means our
            # classifier is behind the API. Suppressing everything the
            # request touches is the conservative reading.
            targets = endpoint_gates or gates
        else:
            targets = gates

        for gate in targets:
            gate.suppress_until(now + delay)
            if isinstance(gate, SlidingWindowGate):
                gate.reconcile(now, limit, remaining)
        self._pump()

    def _suppression_delay(self, retry_at: float | None) -> float:
        """Turn the server's absolute retry timestamp into a delay.

        Hostaway sends a Unix timestamp rather than a number of seconds, so
        a value that is merely in the past is a corrupt header rather than
        an instruction to resume immediately.

        Args:
            retry_at: Candidate ``X-RateLimit-Retry-After`` value.

        Returns:
            Seconds to suppress for.
        """
        if retry_at is None or isinstance(retry_at, bool):
            return DEFAULT_SUPPRESSION_SECONDS
        if not isinstance(retry_at, (int, float)):
            return DEFAULT_SUPPRESSION_SECONDS
        try:
            deadline = float(retry_at)
        except OverflowError, ValueError:
            # An integer too large to be a float is still unambiguously a
            # distant future instant, so it is capped like any other absurd
            # timestamp rather than being allowed to raise.
            return (
                MAX_SUPPRESSION_SECONDS if retry_at > 0 else DEFAULT_SUPPRESSION_SECONDS
            )
        if math.isnan(deadline):
            return DEFAULT_SUPPRESSION_SECONDS
        delay = deadline - self._wall_clock()
        if delay <= 0:
            return DEFAULT_SUPPRESSION_SECONDS
        return min(delay, MAX_SUPPRESSION_SECONDS)

    def note_shed(self) -> None:
        """Record that a coordinator dropped a refresh. Admission unaffected."""
        self._stats.shed_total += 1

    def close(self) -> None:
        """Detach from shared state and stop this limiter's timer.

        The shared IP gate and provider suppression outlive any one limiter
        and hold bound-method listeners, so a limiter that is simply dropped
        would stay reachable — along with its account key — for the life of
        the process. Idempotent, and safe to call on an unused limiter.

        Anything still queued is failed with its own timeout policy rather
        than left pending: once the timer is cancelled and the listeners are
        gone there is nothing left that could ever admit it.
        """
        self._closed = True
        self._cancel_timer()
        self._account_general_gate.remove_listener(self._pump)
        self._ip_general_gate.remove_listener(self._pump)
        self._provider_suppression.remove_listener(self._pump)
        for gate in self._endpoint_gates.values():
            gate.remove_listener(self._pump)

        now = self._clock()
        stranded, self._waiters = self._waiters, []
        for waiter in stranded:
            if not waiter.future.done():
                waiter.future.set_exception(
                    waiter.timeout_exception(now - waiter.queued_at)
                )

    def configure(self, *, account_budget: int, effective_ip_budget: int) -> None:
        """Apply new budgets without disturbing either window.

        Args:
            account_budget: Effective budget for this account's gate.
            effective_ip_budget: Process-wide minimum for the shared IP gate.

        Raises:
            ValueError: If either budget is outside ``1..200``.
        """
        for value in (account_budget, effective_ip_budget):
            if not 1 <= value <= RATE_LIMIT_CEILING:
                msg = (
                    f"rate limit budget must be between 1 and {RATE_LIMIT_CEILING}, "
                    f"got {value}"
                )
                raise ValueError(msg)
        # Both budgets are applied before anything is woken. Notifying
        # between the two would let a waiter be admitted against the new
        # account budget while the IP budget was still the old one, which is
        # exactly the combination a lowered IP budget is meant to prevent.
        self._account_general_gate.reconfigure(account_budget, notify=False)
        self._ip_general_gate.reconfigure(effective_ip_budget, notify=False)
        self._account_general_gate.notify()
        self._ip_general_gate.notify()
        self._pump()

    def snapshot(self) -> LimiterSnapshot:
        """Return a credential-free read of the limiter's current state.

        Returns:
            A snapshot with each gate reported independently.
        """
        now = self._clock()
        gates: dict[str, SlidingWindowGate] = {
            "account": self._account_general_gate,
            "ip": self._ip_general_gate,
        }
        for bucket, gate in self._endpoint_gates.items():
            gates[bucket.value] = gate

        # A waiter is only queued on the gates its own request is charged
        # to. Reporting the whole queue against every gate would tell an
        # operator that endpoint-bucket traffic is blocked on the account
        # gate it deliberately bypasses.
        names_by_gate = {id(gate): name for name, gate in gates.items()}
        waiting: dict[str, list[int]] = {name: [0, 0] for name in gates}
        for waiter in self._waiters:
            index = 0 if waiter.priority is RequestPriority.INTERACTIVE else 1
            for charged in self._gates_for(waiter.method, waiter.path):
                name = names_by_gate.get(id(charged))
                if name is not None:
                    waiting[name][index] += 1

        return LimiterSnapshot(
            gates={
                name: GateSnapshot(
                    budget=gate.configured_budget,
                    effective_budget=gate.budget,
                    window_seconds=gate.window_seconds,
                    admitted_in_window=gate.in_window(now),
                    waiting_interactive=waiting[name][0],
                    waiting_scheduled=waiting[name][1],
                    suppressed=gate.is_suppressed(now),
                    suppressed_for_seconds=(
                        gate.suppressed_until - now
                        if gate.suppressed_until is not None
                        else None
                    ),
                )
                for name, gate in gates.items()
            },
            admitted_total=self._stats.admitted_total,
            shed_total=self._stats.shed_total,
            rate_limited_total=self._stats.rate_limited_total,
            rate_limited_by_counter=dict(self._stats.rate_limited_by_counter),
        )


_KNOWN_COUNTERS = frozenset({"account", "ip", "endpoint", "provider"})
