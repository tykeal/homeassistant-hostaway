# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Retry and backoff helpers for the Hostaway API client."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import random

import httpx

from custom_components.hostaway.api.const import MAX_BACKOFF


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Return a Retry-After delay in seconds, if present."""
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


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
