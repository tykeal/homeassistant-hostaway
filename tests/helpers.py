# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test helper factories for the Hostaway integration tests."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from custom_components.hostaway.api.const import BASE_URL, TOKEN_URL

# Common test constants
FAKE_CLIENT_ID = "test-client-id-12345"
FAKE_CLIENT_SECRET = "test-client-secret-abcdef"
FAKE_TOKEN = "test-access-token-jwt"
FAKE_TOKEN_URL = TOKEN_URL
FAKE_BASE_URL = BASE_URL


def make_token_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway token endpoint response.

    Returns the wrapped format matching the real API:
    {"status": "success", "result": {token fields}}.

    Args:
        **overrides: Fields to override on the token result.

    Returns:
        Dictionary matching the Hostaway token endpoint response format.
    """
    defaults: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": FAKE_TOKEN,
        "expires_in": 86400,
    }
    defaults.update(overrides)
    return {"status": "success", "result": defaults}


def make_listing_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway listing API response dict.

    Uses camelCase keys matching the Hostaway API format.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching Hostaway listing API response format.
    """
    defaults: dict[str, Any] = {
        "id": 12345,
        "name": "Oceanview Suite",
        "internalListingName": "ocean-suite-1",
        "isActive": 1,
        "address": "123 Beach Road",
        "city": "Miami",
        "countryCode": "US",
        "propertyType": "apartment",
        "bedroomsNumber": 2,
        "bathroomsNumber": 1.5,
        "personCapacity": 4,
        "price": 150.00,
        "currencyCode": "USD",
        "checkInTimeStart": "15:00",
        "checkInTimeEnd": "20:00",
        "checkOutTime": "11:00",
        "isListed": 1,
    }
    defaults.update(overrides)
    return defaults


def make_reservation_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Hostaway reservation API response dict.

    Uses camelCase keys matching the Hostaway API format.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching Hostaway reservation API response format.
    """
    defaults: dict[str, Any] = {
        "id": 99001,
        "listingMapId": 12345,
        "guestName": "John Doe",
        "arrivalDate": "2025-08-01",
        "departureDate": "2025-08-05",
        "status": "confirmed",
        "channelName": "airbnb",
        "numberOfGuests": 3,
        "totalPrice": 600.00,
        "currency": "USD",
        "doorCode": "1234",
        "doorCodeVendor": "smartlock",
        "doorCodeInstruction": "Use keypad on front door",
        "confirmationCode": "ABC123",
        "nights": 4,
    }
    defaults.update(overrides)
    return defaults


# --- Deterministic clock harness for rate-limiter tests ---------------------
#
# The limiter takes its clock and its timer-scheduling hook as injected
# callables precisely so tests never have to wait. Every limiter test drives
# time with ``FakeClock.advance`` instead of ``asyncio.sleep``, which keeps
# the suite fast and free of timing flakes.


# A pump may legitimately fire several callbacks at one instant, but never
# an unbounded number. Chosen generously so no honest test trips it.
_MAX_CALLBACKS_PER_INSTANT = 1000


@dataclass(order=True)
class _Timer:
    """A single scheduled callback awaiting its due time."""

    due: float
    sequence: int
    callback: Callable[[], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class FakeTimerHandle:
    """Minimal stand-in for the handle ``loop.call_later`` returns."""

    def __init__(self, timer: _Timer) -> None:
        """Wrap a scheduled timer.

        Args:
            timer: The scheduled entry this handle controls.
        """
        self._timer = timer

    def cancel(self) -> None:
        """Prevent the timer from firing."""
        self._timer.cancelled = True

    @property
    def cancelled(self) -> bool:
        """Whether this timer has been cancelled."""
        return self._timer.cancelled

    @property
    def due(self) -> float:
        """The clock value at which this timer fires."""
        return self._timer.due


class FakeClock:
    """A monotonic clock whose only movement is explicit.

    Mirrors the two injection points the limiter needs: ``now`` replaces
    ``time.monotonic`` and ``schedule`` replaces the running loop's
    ``call_later``.
    """

    def __init__(self, start: float = 0.0) -> None:
        """Create a clock stopped at ``start``.

        Args:
            start: Initial monotonic value.
        """
        self._now = start
        self._timers: list[_Timer] = []
        self._sequence = itertools.count()

    def now(self) -> float:
        """Return the current monotonic value.

        Returns:
            The clock's current value; it never moves on its own.
        """
        return self._now

    def schedule(self, delay: float, callback: Callable[[], None]) -> FakeTimerHandle:
        """Schedule ``callback`` to fire ``delay`` seconds from now.

        A non-positive delay is treated as due immediately, matching
        ``call_later``.

        Args:
            delay: Seconds from now until the callback is due.
            callback: Zero-argument callable to invoke when due.

        Returns:
            A handle that can cancel the pending callback.
        """
        timer = _Timer(
            due=self._now + max(delay, 0.0),
            sequence=next(self._sequence),
            callback=callback,
        )
        self._timers.append(timer)
        return FakeTimerHandle(timer)

    def advance(self, seconds: float) -> int:
        """Move the clock forward, firing due timers in order.

        Timers are fired strictly in due order, ties broken by scheduling
        order. The clock is set to each timer's due time *before* its
        callback runs, so a callback observing ``now()`` sees the moment it
        was scheduled for rather than the end of the advance. A callback may
        schedule further timers; any that fall inside the remaining span fire
        within this same call.

        Args:
            seconds: How far to move the clock forward. Must not be negative,
                since the clock is monotonic.

        Returns:
            The number of callbacks fired.

        Raises:
            ValueError: If ``seconds`` is negative.
            RuntimeError: If callbacks keep re-arming at the same instant,
                which would otherwise spin forever.
        """
        if seconds < 0:
            msg = "FakeClock cannot move backwards"
            raise ValueError(msg)

        target = self._now + seconds
        fired = 0
        stalled = 0
        last_now = self._now
        while True:
            due = [t for t in self._timers if not t.cancelled and t.due <= target]
            if not due:
                break
            nxt = min(due)
            self._timers.remove(nxt)
            self._now = max(self._now, nxt.due)

            # A callback that re-arms itself at the same instant would spin
            # here forever. Fail loudly instead of hanging the suite: a
            # limiter pump that wakes without making progress is exactly the
            # bug this harness exists to expose.
            stalled = stalled + 1 if self._now == last_now else 0
            last_now = self._now
            if stalled > _MAX_CALLBACKS_PER_INSTANT:
                msg = (
                    f"FakeClock fired {stalled} callbacks at t={self._now} "
                    "without the clock advancing; a callback is almost "
                    "certainly re-arming itself at zero delay"
                )
                raise RuntimeError(msg)

            nxt.callback()
            fired += 1

        self._now = target
        self._timers = [t for t in self._timers if not t.cancelled]
        return fired

    @property
    def pending(self) -> int:
        """How many timers are scheduled and not cancelled."""
        return len([t for t in self._timers if not t.cancelled])
