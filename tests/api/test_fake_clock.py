# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Self-tests for the deterministic clock harness.

The limiter suite trusts this harness completely, so it is tested on its
own before anything depends on it.
"""

from __future__ import annotations

import pytest

from tests.helpers import FakeClock


class TestClockMovement:
    """The clock moves only when told to."""

    def test_starts_where_asked(self) -> None:
        """A clock reports its configured start value."""
        assert FakeClock(start=100.0).now() == 100.0

    def test_does_not_move_on_its_own(self) -> None:
        """Repeated reads return the same value."""
        clock = FakeClock()
        assert clock.now() == clock.now()

    def test_advance_moves_forward(self) -> None:
        """Advancing adds exactly the requested span."""
        clock = FakeClock()
        clock.advance(5.0)
        assert clock.now() == 5.0

    def test_refuses_to_move_backwards(self) -> None:
        """The clock is monotonic, like time.monotonic."""
        clock = FakeClock()
        with pytest.raises(ValueError, match="backwards"):
            clock.advance(-1.0)


class TestTimerFiring:
    """Timers fire in due order, never early."""

    def test_timer_does_not_fire_early(self) -> None:
        """A timer stays pending until its due time is reached."""
        clock = FakeClock()
        fired: list[str] = []
        clock.schedule(10.0, lambda: fired.append("a"))
        clock.advance(9.0)
        assert fired == []

    def test_timer_fires_when_due(self) -> None:
        """Reaching the due time exactly fires the timer."""
        clock = FakeClock()
        fired: list[str] = []
        clock.schedule(10.0, lambda: fired.append("a"))
        clock.advance(10.0)
        assert fired == ["a"]

    def test_fires_in_due_order_not_scheduling_order(self) -> None:
        """Due order wins even when scheduled out of order."""
        clock = FakeClock()
        fired: list[str] = []
        clock.schedule(30.0, lambda: fired.append("late"))
        clock.schedule(10.0, lambda: fired.append("early"))
        clock.schedule(20.0, lambda: fired.append("middle"))
        clock.advance(60.0)
        assert fired == ["early", "middle", "late"]

    def test_ties_break_by_scheduling_order(self) -> None:
        """Equal due times preserve the order they were scheduled."""
        clock = FakeClock()
        fired: list[str] = []
        clock.schedule(5.0, lambda: fired.append("first"))
        clock.schedule(5.0, lambda: fired.append("second"))
        clock.advance(5.0)
        assert fired == ["first", "second"]

    def test_callback_sees_its_own_due_time(self) -> None:
        """A callback observes the moment it was scheduled for.

        A limiter waking at a window boundary must not see the clock
        already past the end of the whole advance.
        """
        clock = FakeClock()
        seen: list[float] = []
        clock.schedule(3.0, lambda: seen.append(clock.now()))
        clock.advance(100.0)
        assert seen == [3.0]

    def test_advance_reports_how_many_fired(self) -> None:
        """The fired count lets tests assert nothing extra ran."""
        clock = FakeClock()
        clock.schedule(1.0, lambda: None)
        clock.schedule(2.0, lambda: None)
        clock.schedule(99.0, lambda: None)
        assert clock.advance(5.0) == 2

    def test_zero_delay_is_due_immediately(self) -> None:
        """A zero delay matches call_later's immediate semantics."""
        clock = FakeClock()
        fired: list[str] = []
        clock.schedule(0.0, lambda: fired.append("now"))
        clock.advance(0.0)
        assert fired == ["now"]


class TestReentrantScheduling:
    """A callback may re-arm the timer, as the pump does."""

    def test_timer_scheduled_from_a_callback_still_fires(self) -> None:
        """A chained timer inside the span fires in the same advance."""
        clock = FakeClock()
        fired: list[str] = []

        def first() -> None:
            """Record this run and arm a second timer inside the span."""
            fired.append("first")
            clock.schedule(1.0, lambda: fired.append("second"))

        clock.schedule(1.0, first)
        clock.advance(5.0)
        assert fired == ["first", "second"]

    def test_chained_timer_beyond_the_span_stays_pending(self) -> None:
        """A re-armed timer past the span does not fire early."""
        clock = FakeClock()
        fired: list[str] = []

        def first() -> None:
            """Record this run and arm a second timer beyond the span."""
            fired.append("first")
            clock.schedule(100.0, lambda: fired.append("second"))

        clock.schedule(1.0, first)
        clock.advance(5.0)
        assert fired == ["first"]
        assert clock.pending == 1


class TestCancellation:
    """Cancelled timers never fire."""

    def test_cancelled_timer_does_not_fire(self) -> None:
        """Cancelling before the due time suppresses the callback."""
        clock = FakeClock()
        fired: list[str] = []
        handle = clock.schedule(5.0, lambda: fired.append("a"))
        handle.cancel()
        clock.advance(10.0)
        assert fired == []

    def test_cancelling_one_leaves_others(self) -> None:
        """Cancellation is per timer, not global."""
        clock = FakeClock()
        fired: list[str] = []
        handle = clock.schedule(5.0, lambda: fired.append("cancelled"))
        clock.schedule(5.0, lambda: fired.append("kept"))
        handle.cancel()
        clock.advance(10.0)
        assert fired == ["kept"]

    def test_handle_reports_its_state(self) -> None:
        """The handle exposes due time and cancellation."""
        clock = FakeClock()
        handle = clock.schedule(7.0, lambda: None)
        assert handle.due == 7.0
        assert not handle.cancelled
        handle.cancel()
        assert handle.cancelled

    def test_pending_count_excludes_cancelled(self) -> None:
        """Cancelled timers do not count as pending."""
        clock = FakeClock()
        handle = clock.schedule(5.0, lambda: None)
        clock.schedule(5.0, lambda: None)
        assert clock.pending == 2
        handle.cancel()
        assert clock.pending == 1


class TestNoRealWaiting:
    """The harness exists so limiter tests never sleep."""

    def test_harness_never_calls_sleep(self) -> None:
        """A real sleep here would reintroduce the flakiness we avoid."""
        import ast
        from pathlib import Path

        source = (Path(__file__).parents[1] / "helpers.py").read_text()
        harness = [
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef)
            and node.name in {"FakeClock", "FakeTimerHandle", "_Timer"}
        ]
        assert harness, "the harness classes must live in tests/helpers.py"

        called = set()
        for cls in harness:
            for node in ast.walk(cls):
                func = node.func if isinstance(node, ast.Call) else None
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    called.add(f"{func.value.id}.{func.attr}")

        assert "asyncio.sleep" not in called
        assert "time.sleep" not in called
        assert "time.monotonic" not in called


class TestRunawayCallbacks:
    """A callback that never lets time move must fail, not hang."""

    def test_zero_delay_self_rescheduling_raises(self) -> None:
        """Spinning at one instant is reported, not left to hang.

        A limiter pump that wakes itself without making progress is a real
        bug class, so the harness must surface it as a test failure rather
        than freezing the suite.
        """
        clock = FakeClock()
        count = {"n": 0}

        def respawn() -> None:
            """Re-arm immediately, never letting the clock move on."""
            count["n"] += 1
            clock.schedule(0.0, respawn)

        clock.schedule(0.0, respawn)
        with pytest.raises(RuntimeError, match="without the clock advancing"):
            clock.advance(1.0)
        assert count["n"] > 0

    def test_many_callbacks_at_one_instant_are_allowed(self) -> None:
        """Firing a batch of waiters together is normal pump behaviour."""
        clock = FakeClock()
        fired: list[int] = []
        for i in range(50):
            clock.schedule(1.0, lambda i=i: fired.append(i))  # type: ignore[misc]
        clock.advance(1.0)
        assert len(fired) == 50

    def test_progress_resets_the_stall_counter(self) -> None:
        """Re-arming at a real delay is fine however often it happens."""
        clock = FakeClock()
        fired: list[float] = []

        def step() -> None:
            """Record the time and re-arm at a real delay, up to a limit."""
            fired.append(clock.now())
            if len(fired) < 20:
                clock.schedule(1.0, step)

        clock.schedule(1.0, step)
        clock.advance(100.0)
        assert len(fired) == 20
