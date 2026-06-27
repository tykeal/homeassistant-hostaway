# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Response parsing helpers for the Hostaway API client."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

from typing import Any

import httpx

from custom_components.hostaway.api.exceptions import HostawayResponseError


def parse_response(response: httpx.Response) -> dict[str, Any]:
    """Parse response JSON into a dictionary."""
    try:
        data: dict[str, Any] = response.json()
    except Exception as exc:
        raise HostawayResponseError("Response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise HostawayResponseError("Response must be a JSON object")
    return data


def ensure_success(data: dict[str, Any], error_prefix: str = "API error") -> Any:
    """Validate the Hostaway status field and return the result payload."""
    status = data.get("status")
    if status is not None and status != "success":
        raise HostawayResponseError(f"{error_prefix}: {data.get('result', status)}")
    return data.get("result")


def extract_results(
    data: dict[str, Any], *, error_prefix: str = "API error"
) -> list[dict[str, Any]]:
    """Return a successful result payload that must be a list of objects."""
    results = ensure_success(data, error_prefix)
    if results is None:
        raise HostawayResponseError("Response missing 'result' field")
    if not isinstance(results, list):
        raise HostawayResponseError("'result' must be a list")
    if any(not isinstance(item, dict) for item in results):
        raise HostawayResponseError("'result' items must be JSON objects")
    return results
