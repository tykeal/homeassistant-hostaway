# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Ambient priority and deadline for one logical Hostaway operation.

A "logical operation" is a service call, a config-flow validation, or one
coordinator refresh cycle — not a single HTTP request. Carrying the
classification in a context variable rather than a parameter matters because
some call sites receive the request function as a bare callable and have
nowhere to put an extra argument.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import IntEnum


class RequestPriority(IntEnum):
    """Admission ordering class.

    Lower sorts first. This governs *ordering* only; what happens when the
    budget is exhausted is the wait policy's job.
    """

    INTERACTIVE = 0
    SCHEDULED = 1


@dataclass(frozen=True, slots=True)
class WaitPolicy:
    """How long a logical operation may queue, and how it fails.

    The duration creates a single deadline for the whole operation. It is
    deliberately not a per-acquisition timeout: an operation that makes ten
    paginated calls must not be able to wait ten times as long.
    """

    duration: float
    shed_on_timeout: bool

    def __post_init__(self) -> None:
        """Reject a non-positive duration.

        Raises:
            ValueError: If the duration is not greater than zero.
        """
        if self.duration <= 0:
            msg = "WaitPolicy duration must be positive"
            raise ValueError(msg)


#: User-visible work: service calls, config-flow validation, setup probes.
INTERACTIVE_POLICY = WaitPolicy(duration=30.0, shed_on_timeout=False)

#: Routine coordinator refreshes, which may be dropped to protect the budget.
SCHEDULED_POLICY = WaitPolicy(duration=2.0, shed_on_timeout=True)

#: A coordinator's very first refresh, which must never be shed because
#: shedding it would publish an empty placeholder as though it were data.
FIRST_REFRESH_POLICY = WaitPolicy(duration=30.0, shed_on_timeout=False)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The ambient priority and deadline of one logical operation."""

    priority: RequestPriority
    policy: WaitPolicy
    deadline: float

    @classmethod
    def start(
        cls,
        priority: RequestPriority,
        policy: WaitPolicy,
        now: float,
    ) -> RequestContext:
        """Begin an operation, fixing its deadline once.

        Args:
            priority: Ordering class for every request in the operation.
            policy: Wait policy supplying the duration and failure mode.
            now: Current monotonic time.

        Returns:
            A context whose deadline covers the whole operation.
        """
        return cls(priority=priority, policy=policy, deadline=now + policy.duration)

    def remaining(self, now: float) -> float:
        """Return the seconds left before this operation must give up.

        Args:
            now: Current monotonic time.

        Returns:
            Seconds remaining, never negative.
        """
        return max(self.deadline - now, 0.0)


_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "hostaway_request_context",
    default=None,
)


@contextmanager
def request_context(ctx: RequestContext) -> Iterator[None]:
    """Make ``ctx`` the ambient context for the duration of the block.

    The previous context is restored on the way out, including when the
    block raises. Tasks created inside inherit a copy, which is how a
    paginated fan-out keeps the priority of the operation that started it.

    Args:
        ctx: The context to install.

    Yields:
        None.
    """
    token = _REQUEST_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _REQUEST_CONTEXT.reset(token)


def current_request_context(now: float | None = None) -> RequestContext:
    """Return the ambient context, synthesising a conservative one if absent.

    A call site that forgot to establish a context gets scheduled priority
    and a short, sheddable deadline. That way an oversight degrades into
    cautious behaviour rather than silently jumping the interactive queue.

    Args:
        now: Current monotonic time, used only when synthesising. Defaults
            to ``time.monotonic()``.

    Returns:
        The ambient context, or a fresh scheduled one.
    """
    ctx = _REQUEST_CONTEXT.get()
    if ctx is not None:
        return ctx
    return RequestContext.start(
        RequestPriority.SCHEDULED,
        SCHEDULED_POLICY,
        time.monotonic() if now is None else now,
    )


@contextmanager
def ensure_request_context(now: float | None = None) -> Iterator[RequestContext]:
    """Install a synthesised context when the caller established none.

    ``current_request_context`` synthesises a fresh context on every call,
    which would hand each stage of a multi-request operation its own
    deadline. Installing the synthesised context once means a token
    refresh, every retry, and a recursive re-entry after a 403 all share
    the single deadline the operation is entitled to.

    Args:
        now: Current monotonic time, used only when synthesising. Defaults
            to ``time.monotonic()``.

    Yields:
        The ambient context, which is left untouched when one already
        exists.
    """
    existing = _REQUEST_CONTEXT.get()
    if existing is not None:
        yield existing
        return
    ctx = RequestContext.start(
        RequestPriority.SCHEDULED,
        SCHEDULED_POLICY,
        time.monotonic() if now is None else now,
    )
    token = _REQUEST_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _REQUEST_CONTEXT.reset(token)


def start_interactive_context(now: float | None = None) -> RequestContext:
    """Create a context for user-visible work.

    Args:
        now: Current monotonic time; defaults to ``time.monotonic()``.

    Returns:
        A new interactive context.
    """
    return RequestContext.start(
        RequestPriority.INTERACTIVE,
        INTERACTIVE_POLICY,
        time.monotonic() if now is None else now,
    )


def start_scheduled_context(now: float | None = None) -> RequestContext:
    """Create a context for a routine refresh cycle.

    Args:
        now: Current monotonic time; defaults to ``time.monotonic()``.

    Returns:
        A new scheduled context.
    """
    return RequestContext.start(
        RequestPriority.SCHEDULED,
        SCHEDULED_POLICY,
        time.monotonic() if now is None else now,
    )


def start_first_refresh_context(now: float | None = None) -> RequestContext:
    """Create a context for a coordinator's first refresh.

    Args:
        now: Current monotonic time; defaults to ``time.monotonic()``.

    Returns:
        A new first-refresh context, which is never shed.
    """
    return RequestContext.start(
        RequestPriority.SCHEDULED,
        FIRST_REFRESH_POLICY,
        time.monotonic() if now is None else now,
    )
