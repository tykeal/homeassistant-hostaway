# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Retry and backoff helpers for the Hostaway API client."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

import httpx

from custom_components.hostaway.api.const import MAX_BACKOFF

#: Hostaway reports when a refused caller may try again as an absolute Unix
#: timestamp, not as the delay in seconds that the HTTP ``Retry-After``
#: header carries. Reading one as the other asks for a sleep of roughly
#: fifty-five years.
RETRY_AFTER_HEADER = "X-RateLimit-Retry-After"
LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
APPLIED_HEADER = "X-RateLimit-Applied"


@dataclass(frozen=True, slots=True)
class RateLimitHeaders:
    """What a Hostaway response says about the counter it was charged to."""

    limit: int | None = None
    remaining: int | None = None
    retry_at: float | None = None
    applied: str | None = None


def _header_float(response: httpx.Response, name: str) -> float | None:
    """Return a finite float header value, or None.

    Args:
        response: The response to read.
        name: Header name.

    Returns:
        The parsed value, or None when absent, unparsable, or not finite.
    """
    header = response.headers.get(name)
    if header is None:
        return None
    try:
        value = float(header)
    except TypeError, ValueError, OverflowError:
        return None
    return value if math.isfinite(value) else None


def _header_int(response: httpx.Response, name: str) -> int | None:
    """Return a non-negative whole-number header value, or None.

    Counter headers are integers. A fractional value is malformed, and
    truncating it would quietly turn a nonsense ``1.9`` ceiling into a
    budget of one request per window, so it is rejected instead.

    Args:
        response: The response to read.
        name: Header name.

    Returns:
        The parsed count, or None when absent, not a whole number, or
        negative.
    """
    header = response.headers.get(name)
    if header is None:
        return None
    try:
        value = int(header.strip())
    except TypeError, ValueError:
        return None
    return value if value >= 0 else None


def parse_rate_limit_headers(response: httpx.Response) -> RateLimitHeaders:
    """Read Hostaway's rate-limit headers off a response.

    Args:
        response: The response to read.

    Returns:
        Whatever of the four headers was present and usable.
    """
    applied = response.headers.get(APPLIED_HEADER)
    return RateLimitHeaders(
        limit=_header_int(response, LIMIT_HEADER),
        remaining=_header_int(response, REMAINING_HEADER),
        retry_at=_header_float(response, RETRY_AFTER_HEADER),
        applied=applied or None,
    )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Return how long to wait before retrying, in seconds.

    Args:
        response: The refused response.

    Returns:
        Seconds until Hostaway says the caller may retry, or None when the
        header is absent, unusable, or already in the past. A timestamp
        that has passed is reported as None rather than zero so the caller
        falls back to its own backoff instead of retrying instantly.
    """
    retry_at = _header_float(response, RETRY_AFTER_HEADER)
    if retry_at is None:
        return None
    delay = retry_at - time.time()
    return delay if delay > 0 else None


def _calculate_backoff(base_backoff: float, response: httpx.Response) -> float:
    """Return a retry delay honoring Retry-After when provided."""
    retry_after = _parse_retry_after(response)
    return (
        max(0.1, min(retry_after, MAX_BACKOFF))
        if retry_after is not None
        else _jittered_delay(base_backoff)
    )


def _jittered_delay(base_backoff: float) -> float:
    """Return a base backoff with ±25% jitter, floored at 0.1s."""
    delay = min(base_backoff, MAX_BACKOFF)
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


def _is_server_error(status_code: int) -> bool:
    """Return whether an HTTP status code is a 5xx response."""
    return 500 <= status_code < 600
