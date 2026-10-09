# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sliding-window counters, the unit of rate accounting.

Every method here is synchronous. An awaitable gate would let the event loop
interleave in the middle of an admission decision, which is precisely the
race the limiter's single pump exists to prevent.
"""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from collections.abc import Callable
from itertools import chain
from typing import Protocol

from ..const import (
    DEFAULT_RATE_LIMIT_BUDGET,
    RATE_LIMIT_CEILING,
    RATE_LIMIT_WINDOW_SECONDS,
)
from .buckets import GateScope

_LOGGER = logging.getLogger(__name__)


class BudgetGate(Protocol):
    """One counter the limiter must satisfy before admitting a request.

    Every method is synchronous on purpose. An awaitable gate would let the
    loop interleave inside the admission decision, which is exactly the race
    the single pump exists to prevent.
    """

    name: str
    scope: GateScope
    suppressed_until: float | None

    def capacity_available(self, now: float) -> bool:
        """Report whether this gate could admit a request at ``now``."""

    def record(self, now: float) -> None:
        """Consume one unit of capacity at ``now``."""

    def next_available(self, now: float) -> float | None:
        """Return when capacity next frees, or ``None`` if it is free now."""

    def suppress_until(self, until: float) -> None:
        """Extend suppression of this gate to ``until``."""


class SlidingWindowGate:
    """A single sliding-window counter.

    Timestamps are monotonic, so a wall-clock correction cannot make the
    window appear to jump. Admissions the server has told us about but we
    did not make ourselves are tracked separately as *synthetic* entries, so
    reconciliation never corrupts our own record of what we sent.
    """

    def __init__(
        self,
        name: str,
        scope: GateScope,
        *,
        budget: int = DEFAULT_RATE_LIMIT_BUDGET,
        max_budget: int = RATE_LIMIT_CEILING,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
        created_at: float = 0.0,
        startup_hold: bool = True,
    ) -> None:
        """Create a gate.

        Args:
            name: Diagnostics label.
            scope: What this counter is keyed on.
            budget: Admissions permitted per window.
            max_budget: Documented ceiling for this counter.
            window_seconds: Length of the sliding window.
            created_at: Monotonic creation time, used for the startup hold.
            startup_hold: Whether to begin half-consumed. See below.

        Raises:
            ValueError: If the window is not positive or the budget is
                outside ``1..max_budget``.
        """
        if window_seconds <= 0:
            msg = "window_seconds must be positive"
            raise ValueError(msg)
        self.name = name
        self.scope = scope
        self._max_budget = max_budget
        self._window = window_seconds
        self._budget = 0
        self._configured_budget = 0
        self._admissions: deque[float] = deque()
        self._synthetic_admissions: deque[float] = deque()
        self.suppressed_until: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._startup_hold_at = created_at
        self._startup_hold_count = 0
        self.reconfigure(budget)

        # Limiter state is deliberately not persisted across a restart, so a
        # fresh gate has no memory of traffic sent moments ago. Starting at
        # full capacity would let a restart inside the server's live window
        # admit a second full budget. Half the budget is parked as synthetic
        # admissions dated to creation; they prune themselves once the first
        # window has passed, so the hold expires with no extra bookkeeping.
        # An odd budget rounds the reservation up, because the point of the
        # hold is to be conservative about what we cannot remember sending.
        if startup_hold:
            self._startup_hold_count = -(-budget // 2)
            for _ in range(self._startup_hold_count):
                self._synthetic_admissions.appendleft(created_at)

    @property
    def budget(self) -> int:
        """The admissions permitted per window right now."""
        return self._budget

    @property
    def configured_budget(self) -> int:
        """The operator-configured budget, ignoring server reconciliation."""
        return self._configured_budget

    @property
    def max_budget(self) -> int:
        """The documented ceiling for this counter."""
        return self._max_budget

    @property
    def window_seconds(self) -> float:
        """The length of this gate's sliding window."""
        return self._window

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when this gate's state changes.

        A shared gate is watched by every limiter using it, so a change made
        through one limiter re-pumps the others.

        Args:
            callback: Zero-argument callable invoked on change.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Stop notifying ``callback``.

        A limiter that is torn down must detach, or the shared gate keeps it
        and its account key alive for the life of the process.

        Args:
            callback: The previously registered callable.
        """
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _notify(self) -> None:
        """Tell every watching limiter that this gate changed."""
        for callback in list(self._listeners):
            callback()

    def _prune(self, now: float) -> None:
        """Discard timestamps that have fallen out of the window.

        Args:
            now: Current monotonic time.
        """
        cutoff = now - self._window
        while self._admissions and self._admissions[0] <= cutoff:
            self._admissions.popleft()
        while self._synthetic_admissions and self._synthetic_admissions[0] <= cutoff:
            self._synthetic_admissions.popleft()
            if self._startup_hold_count:
                # Hold entries are dated to creation, so they are always the
                # oldest and always prune first.
                self._startup_hold_count -= 1

    def in_window(self, now: float) -> int:
        """Return how many admissions currently occupy the window.

        Args:
            now: Current monotonic time.

        Returns:
            Combined count of real and synthetic in-window admissions.
        """
        self._prune(now)
        return len(self._admissions) + len(self._synthetic_admissions)

    def capacity_available(self, now: float) -> bool:
        """Report whether this gate could admit a request at ``now``.

        Logically read-only, though it may discard expired timestamps.

        Args:
            now: Current monotonic time.

        Returns:
            True when the window has room.
        """
        return self.in_window(now) < self._budget

    def record(self, now: float) -> None:
        """Consume one unit of capacity.

        Called once per admission on every selected gate, and only after all
        of them reported capacity.

        Args:
            now: Current monotonic time.
        """
        self._prune(now)
        self._admissions.append(now)

    def next_available(self, now: float) -> float | None:
        """Return when capacity next frees.

        Args:
            now: Current monotonic time.

        Returns:
            The monotonic instant at which enough of the window expires for
            one admission to fit, or ``None`` if capacity is available now.
        """
        occupied = self.in_window(now)
        if occupied < self._budget:
            return None
        if not occupied:
            # Capacity is unavailable yet nothing occupies the window, which
            # means the budget itself is the blocker rather than time.
            return None
        # A budget lowered below what is already in flight needs more than
        # the oldest entry to expire. Waking at the oldest would just find
        # the gate still full and re-arm, once per surplus admission.
        surplus = occupied - self._budget
        merged = sorted(chain(self._admissions, self._synthetic_admissions))
        return merged[surplus] + self._window

    def reconfigure(self, budget: int, *, notify: bool = True) -> None:
        """Set the operator-configured budget without disturbing the window.

        Lowering the budget below the current in-window count is legal and
        simply means nothing is admitted until the window drains. Discarding
        timestamps to make the new budget fit would amount to pretending we
        never sent those requests.

        Args:
            budget: The new budget.
            notify: Whether to wake watchers immediately. A caller changing
                several gates together must pass ``False`` and notify once
                afterwards, so no watcher can act on half-applied budgets.

        Raises:
            ValueError: If the budget is outside ``1..max_budget``.
        """
        if not 1 <= budget <= self._max_budget:
            msg = f"budget must be between 1 and {self._max_budget}, got {budget}"
            raise ValueError(msg)
        if budget == self._configured_budget:
            return
        self._configured_budget = budget
        self._budget = budget
        self._resize_startup_hold(budget)
        if notify:
            self._notify()

    def _resize_startup_hold(self, budget: int) -> None:
        """Re-size an untouched startup hold to match a new budget.

        The shared IP gate is created before anyone knows the effective
        process-wide budget, so its hold is initially sized against the
        default. Leaving it there would reserve half of 180 against a
        configured budget of 50. Only a hold that has neither expired nor
        seen a real admission is re-sized; once traffic has flowed the hold
        is history and must be preserved exactly as it is.

        Args:
            budget: The newly configured budget.
        """
        if not self._startup_hold_count or self._admissions:
            return
        target = -(-budget // 2)
        while self._startup_hold_count > target:
            self._synthetic_admissions.popleft()
            self._startup_hold_count -= 1
        while self._startup_hold_count < target:
            self._synthetic_admissions.appendleft(self._startup_hold_at)
            self._startup_hold_count += 1

    def notify(self) -> None:
        """Wake every watcher of this gate."""
        self._notify()

    def suppress_until(self, until: float) -> None:
        """Extend suppression of this gate.

        Suppression only ever grows: two 429s in flight must not let the
        second shorten the first one's backoff.

        Args:
            until: Monotonic instant until which the gate must not admit.
        """
        if self.suppressed_until is not None and until <= self.suppressed_until:
            return
        self.suppressed_until = until
        self._notify()

    def is_suppressed(self, now: float) -> bool:
        """Report whether suppression is currently in force.

        Args:
            now: Current monotonic time.

        Returns:
            True while suppressed.
        """
        if self.suppressed_until is None:
            return False
        if now >= self.suppressed_until:
            self.suppressed_until = None
            return False
        return True

    def reconcile(self, now: float, limit: int | None, remaining: int | None) -> None:
        """Apply the server's own view of this counter.

        Both adjustments are one-way downward. Letting a server-reported
        figure raise capacity would allow the two views to oscillate, and
        could override an operator's deliberately conservative budget.

        Args:
            now: Current monotonic time.
            limit: Server-reported ceiling, or ``None``.
            remaining: Server-reported remaining allowance, or ``None``.
        """
        changed = False
        usable_limit = _usable_limit(limit)
        if usable_limit is not None:
            lowered = min(self._budget, self._configured_budget, usable_limit)
            if lowered != self._budget:
                self._budget = lowered
                changed = True
        elif limit is not None:
            _LOGGER.debug(
                "Ignoring unusable X-RateLimit-Limit %r for gate %s", limit, self.name
            )

        usable_remaining = _usable_remaining(remaining)
        if usable_remaining is not None:
            available = self._budget - self.in_window(now)
            shortfall = available - usable_remaining
            room = self._max_budget - self.in_window(now)
            for _ in range(max(min(shortfall, room), 0)):
                self._synthetic_admissions.append(now)
                changed = True
        elif remaining is not None:
            _LOGGER.debug(
                "Ignoring unusable X-RateLimit-Remaining %r for gate %s",
                remaining,
                self.name,
            )

        if changed:
            self._notify()


def _usable_limit(limit: int | None) -> int | None:
    """Return a reported limit if it may be acted on.

    Args:
        limit: Candidate value from ``X-RateLimit-Limit``.

    Returns:
        The value when it is an integer of at least 1, else ``None``. A
        limit of zero is discarded rather than applied: it would violate
        the gate's own ``1 <= budget`` invariant.
    """
    value = _header_int(limit)
    return value if value is not None and value >= 1 else None


def _usable_remaining(remaining: int | None) -> int | None:
    """Return a reported remaining count if it may be acted on.

    Args:
        remaining: Candidate value from ``X-RateLimit-Remaining``.

    Returns:
        The value when it is a non-negative integer, else ``None``. A
        negative count describes a state no budget could satisfy.
    """
    value = _header_int(remaining)
    return value if value is not None and value >= 0 else None


def _header_int(value: int | None) -> int | None:
    """Return a header value as an integer if it is one.

    Booleans are rejected: ``True`` is an ``int`` to Python but never a
    meaningful header value, and accepting it would silently mean 1.

    Args:
        value: Candidate value.

    Returns:
        The integer, or ``None`` if the value is unusable.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def gate_suppressed(gate: BudgetGate, now: float) -> bool:
    """Report whether a gate is currently suppressed.

    Args:
        gate: The gate to test.
        now: Current monotonic time.

    Returns:
        True while suppressed.
    """
    if isinstance(gate, SlidingWindowGate):
        return gate.is_suppressed(now)
    until = gate.suppressed_until
    return until is not None and now < until
