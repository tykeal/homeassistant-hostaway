# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exception hierarchy for the Hostaway API client."""

from __future__ import annotations


class HostawayApiError(Exception):
    """Base exception for all Hostaway API errors."""

    def __init__(self, message: str) -> None:
        """Initialize with error message.

        Args:
            message: Human-readable error description.
        """
        self.message = message
        super().__init__(message)


class HostawayAuthError(HostawayApiError):
    """Authentication error: invalid credentials or expired token."""


class HostawayReservationLockedError(HostawayApiError):
    """Hostaway refused a write because the reservation is in a
    non-writable state (e.g., channel-managed by an OTA, cancelled,
    or in conflict with another reservation).

    Distinct from HostawayAuthError so callers can treat this as a
    normal, expected outcome rather than a credential failure.
    """


class HostawayRateLimitError(HostawayApiError):
    """Rate limit exceeded: HTTP 429."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        """Initialize with retry information.

        Args:
            message: Human-readable error description.
            retry_after: Seconds to wait before retrying, or None.
        """
        super().__init__(message)
        self.retry_after = retry_after


class HostawayConnectionError(HostawayApiError):
    """Network-level failure: DNS, TCP, TLS, or timeout."""


class HostawayResponseError(HostawayApiError):
    """Unexpected response format: missing fields, invalid JSON."""


class HostawayMutationResultError(HostawayResponseError):
    """Successful mutation response had an unusable result payload."""


class HostawayRateLimitWaitTimeout(HostawayRateLimitError):
    """A caller's local wait for rate-limit budget expired.

    Raised by the limiter, not by Hostaway: the request never left the
    process. It subclasses HostawayRateLimitError so coordinators convert
    it to an update failure like any other rate-limit problem, but callers
    that retry on a server 429 must re-raise this first, since replaying a
    request the server never saw cannot help.
    """

    def __init__(self, message: str, *, waited: float) -> None:
        """Initialize with how long the caller queued.

        Args:
            message: Human-readable error description.
            waited: Seconds spent waiting before the deadline expired.
        """
        super().__init__(message)
        self.waited = waited


class HostawayRateLimitShedError(Exception):
    """A scheduled refresh was dropped to protect the rate-limit budget.

    Deliberately **not** a HostawayApiError. Every coordinator turns that
    base class into an UpdateFailed, which marks entities unavailable, but
    shedding a low-priority refresh is a healthy outcome: the previous data
    stays published and the next cycle tries again. Changing this base
    class would silently break that guarantee.
    """

    def __init__(self, message: str, *, waited: float) -> None:
        """Initialize with how long the refresh queued before being shed.

        Args:
            message: Human-readable description of what was shed.
            waited: Seconds spent waiting before the refresh was dropped.
        """
        self.message = message
        self.waited = waited
        super().__init__(message)
