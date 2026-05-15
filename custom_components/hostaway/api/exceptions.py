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
