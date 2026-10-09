# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Process-wide suppression that overrides every counter at once.

A ``provider`` 429 is a statement about Hostaway as a whole rather than about
the account that happened to observe it, so it cannot live on a gate.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable


class ProviderSuppression:
    """Process-wide suppression covering every Hostaway limiter.

    A ``provider`` 429 is a statement about Hostaway as a whole, so it is
    held here rather than on the account that happened to observe it.
    """

    def __init__(self) -> None:
        """Create an inactive suppression holder."""
        self.until: float | None = None
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when suppression changes.

        Args:
            callback: Zero-argument callable invoked on change.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Stop notifying ``callback``.

        Args:
            callback: The previously registered callable.
        """
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def suppress_until(self, until: float) -> None:
        """Extend process-wide suppression.

        Args:
            until: Monotonic instant until which nothing may be sent.
        """
        if self.until is not None and until <= self.until:
            return
        self.until = until
        for callback in list(self._listeners):
            callback()

    def active(self, now: float) -> bool:
        """Report whether suppression is currently in force.

        Args:
            now: Current monotonic time.

        Returns:
            True while suppressed.
        """
        if self.until is None:
            return False
        if now >= self.until:
            self.until = None
            return False
        return True
