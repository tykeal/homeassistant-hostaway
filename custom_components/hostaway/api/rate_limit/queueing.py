# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""The queued caller, and the figures reported about the queue.

A waiter is one ``acquire()`` that could not be satisfied immediately. The
snapshot types alongside it are what diagnostics publishes, so they are
deliberately free of anything credential-bearing.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..exceptions import HostawayRateLimitShedError, HostawayRateLimitWaitTimeout
from .context import RequestContext, RequestPriority


def deadline_exception(*, shed_on_timeout: bool, waited: float) -> Exception:
    """Build the error an operation fails with when its deadline expires.

    The chokepoints enforce the same deadline the limiter does — a retry
    backoff must not sleep past it — so they must fail in exactly the same
    way, or a caller would see two different outcomes for one condition
    depending on where it happened to be noticed.

    Args:
        shed_on_timeout: Whether the operation may be dropped rather than
            reported as a failure.
        waited: Real seconds spent waiting.

    Returns:
        A shed signal for sheddable work, otherwise a wait timeout.
    """
    if shed_on_timeout:
        return HostawayRateLimitShedError(
            "Skipped a scheduled Hostaway refresh to stay within the "
            "API rate limit; the previous data is still current",
            waited=waited,
        )
    return HostawayRateLimitWaitTimeout(
        "Timed out waiting for Hostaway API rate limit capacity after "
        f"{waited:.1f}s. The integration paces its own requests to stay "
        "within Hostaway's published limits; raising the rate limit "
        "budget option or reducing polling frequency may help",
        waited=waited,
    )


async def sleep_within_deadline(delay: float, ctx: RequestContext) -> None:
    """Sleep, unless doing so would outlive the operation's deadline.

    Sleeping first and failing afterwards would only postpone the same
    outcome, so a sleep that cannot complete in the time left is refused
    outright.

    Args:
        delay: Seconds the caller wants to wait.
        ctx: The ambient operation context.

    Raises:
        HostawayRateLimitShedError: If a sheddable operation has too little
            time left.
        HostawayRateLimitWaitTimeout: If any other operation has.
    """
    remaining = ctx.remaining(time.monotonic())
    if delay >= remaining:
        raise deadline_exception(
            shed_on_timeout=ctx.policy.shed_on_timeout,
            waited=ctx.policy.duration - remaining,
        )
    # The check above is not enough on its own: a busy event loop can
    # resume the sleep well after the deadline, and returning then would
    # break the contract just as surely as never checking.
    try:
        async with asyncio.timeout(remaining):
            await asyncio.sleep(delay)
    except TimeoutError as exc:
        raise deadline_exception(
            shed_on_timeout=ctx.policy.shed_on_timeout,
            waited=ctx.policy.duration - ctx.remaining(time.monotonic()),
        ) from exc


@dataclass(order=True)
class Waiter:
    """One queued ``acquire()`` that could not be admitted immediately.

    Only ``priority`` and ``sequence`` take part in ordering, and sequence
    numbers never repeat, so the heap can never reach the future and attempt
    to compare two of them.
    """

    priority: RequestPriority
    sequence: int
    queued_at: float = field(compare=False)
    deadline: float = field(compare=False)
    shed_on_timeout: bool = field(compare=False)
    method: str = field(compare=False)
    path: str = field(compare=False)
    interactive_admissions_at_queue: int = field(compare=False)
    future: asyncio.Future[None] = field(compare=False)

    def timeout_exception(self, waited: float) -> Exception:
        """Build the error this waiter fails with.

        Args:
            waited: Real seconds spent queued.

        Returns:
            A shed signal for sheddable work, otherwise a wait timeout.
        """
        return deadline_exception(shed_on_timeout=self.shed_on_timeout, waited=waited)


@dataclass(slots=True)
class LimiterStats:
    """Cumulative counters surfaced through diagnostics."""

    admitted_total: int = 0
    shed_total: int = 0
    rate_limited_total: int = 0
    rate_limited_by_counter: dict[str, int] = field(default_factory=dict)
    interactive_wait_total: float = 0.0
    scheduled_wait_total: float = 0.0


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    """A point-in-time read of one gate."""

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
    """A point-in-time read of a limiter, safe to publish.

    Carries no account key and no credential: the account id is the client
    id, so emitting it would leak half the integration's credentials.
    """

    gates: dict[str, GateSnapshot]
    admitted_total: int
    shed_total: int
    rate_limited_total: int
    rate_limited_by_counter: dict[str, int]


class _NeverAwaitedFuture:
    """Stand-in future for an error built outside the queue.

    ``_timeout_for`` reuses the waiter's message construction for a caller
    that never queued, so it needs a future-shaped placeholder that is never
    awaited or resolved.
    """

    def done(self) -> bool:
        """Report completion. Always true, since it is never used."""
        return True


NEVER_AWAITED: asyncio.Future[None] = _NeverAwaitedFuture()  # type: ignore[assignment]
