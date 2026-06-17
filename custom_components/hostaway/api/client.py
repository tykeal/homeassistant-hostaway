# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Hostaway API requests."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from custom_components.hostaway.api import redaction as _redaction
from custom_components.hostaway.api import retry as _retry
from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.const import (
    BACKOFF_MULTIPLIER,
    BASE_URL,
    DEFAULT_PAGE_LIMIT,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    MAX_RETRIES,
)
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
    HostawayReservationLockedError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayListing, HostawayReservation

_LOGGER = logging.getLogger(__name__)


class HostawayApiClient:
    """HTTP client for authenticated Hostaway API requests."""

    def __init__(
        self,
        token_manager: HostawayTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize the API client."""
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    async def test_connection(self) -> bool:
        """Validate credentials with a lightweight API call."""
        await self._request("GET", "/v1/listings", params={"limit": 1})
        return True

    async def get_listings_page(
        self, offset: int = 0, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[HostawayListing]:
        """Return one page of listings."""
        items = await self._request_results(
            "/v1/listings", params={"offset": offset, "limit": limit}
        )
        return [HostawayListing.from_api_response(item) for item in items]

    async def get_all_listings(self) -> list[HostawayListing]:
        """Return all listings."""
        items = await self._paginate_offset("/v1/listings")
        return [HostawayListing.from_api_response(item) for item in items]

    async def get_reservations_page(
        self,
        listing_id: int,
        after_id: int | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[HostawayReservation]:
        """Return one page of reservations for a listing."""
        items = await self._get_reservation_items(
            listing_id,
            after_id=after_id,
            limit=limit,
        )
        return self._parse_reservations(items, listing_id)

    async def get_all_reservations(self, listing_id: int) -> list[HostawayReservation]:
        """Return all reservations for a listing."""
        reservations: list[HostawayReservation] = []
        after_id: int | None = None
        while True:
            items = await self._get_reservation_items(listing_id, after_id=after_id)
            reservations.extend(self._parse_reservations(items, listing_id))
            if len(items) < DEFAULT_PAGE_LIMIT:
                return reservations
            after_id = self._reservation_page_cursor(items, listing_id)
            if after_id is None:
                return reservations

    async def _get_reservation_items(
        self,
        listing_id: int,
        after_id: int | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return one raw page of reservation payloads."""
        params: dict[str, int] = {"listingId": listing_id, "limit": limit}
        if after_id is not None:
            params["afterId"] = after_id
        return await self._request_results("/v1/reservations", params=params)

    def _parse_reservations(
        self, items: list[dict[str, Any]], listing_id: int
    ) -> list[HostawayReservation]:
        """Parse reservation records, skipping malformed API records."""
        reservations: list[HostawayReservation] = []
        for item in items:
            try:
                reservations.append(HostawayReservation.from_api_response(item))
            except ValueError as exc:
                reservation_id = item.get("id")
                if reservation_id is None:
                    _LOGGER.warning(
                        "Skipping malformed Hostaway reservation for listing %s: %s",
                        listing_id,
                        exc,
                    )
                else:
                    _LOGGER.warning(
                        "Skipping malformed Hostaway reservation %r for listing %s: %s",
                        reservation_id,
                        listing_id,
                        exc,
                    )
        return reservations

    def _reservation_page_cursor(
        self, items: list[dict[str, Any]], listing_id: int
    ) -> int | None:
        """Return the raw cursor ID for a full reservation page."""
        cursor = items[-1].get("id")
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor <= 0:
            _LOGGER.warning(
                "Stopping reservation pagination for listing %s because "
                "the last raw reservation has invalid id %r",
                listing_id,
                cursor,
            )
            return None
        return cursor

    async def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a task."""
        return await self._mutate(
            "POST",
            "/v1/tasks",
            data,
            "Create failed",
            "Create response missing 'result' object",
        )

    async def update_task(self, task_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """Update a task."""
        return await self._mutate(
            "PUT",
            f"/v1/tasks/{task_id}",
            data,
            "Update failed",
            "Update response missing 'result' object",
        )

    async def delete_task(self, task_id: int) -> None:
        """Delete a task."""
        self._ensure_success(
            self._parse_response(await self._request("DELETE", f"/v1/tasks/{task_id}")),
            "Delete failed",
        )

    async def get_tasks(
        self, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Return all tasks."""
        return await self._paginate_offset(
            "/v1/tasks", params=params, error_prefix="Get tasks failed"
        )

    async def get_users(self) -> list[dict[str, Any]]:
        """Return all users."""
        return await self._request_results("/v1/users")

    async def get_groups(self) -> list[dict[str, Any]]:
        """Return all user groups."""
        return await self._request_results("/v1/userGroups")

    async def update_reservation(
        self, reservation_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a reservation."""
        return await self._mutate(
            "PUT",
            f"/v1/reservations/{reservation_id}",
            data,
            "Update failed",
            "Update response missing 'result' object",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        _retried_auth: bool = False,
    ) -> httpx.Response:
        """Make an authenticated API request with retries."""
        url = f"{self._base_url}{path}"
        backoff = INITIAL_BACKOFF
        for attempt in range(MAX_RETRIES + 1):
            token = await self._token_manager.get_token()
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
            except httpx.RequestError as exc:
                if attempt >= MAX_RETRIES:
                    raise HostawayConnectionError(
                        "Failed to connect to Hostaway API after "
                        f"{MAX_RETRIES} retries: {exc}"
                    ) from exc
                delay = _retry._jittered_delay(backoff)
                _LOGGER.warning(
                    "Network error, retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue
            if response.status_code == 403:
                return await self._handle_forbidden_response(
                    response,
                    method,
                    path,
                    params=params,
                    json_body=json,
                    _retried_auth=_retried_auth,
                )
            if response.status_code == 404:
                raise HostawayResponseError(f"Resource not found: {path}")
            if response.status_code == 429:
                delay = self._handle_rate_limit_response(
                    response, attempt, MAX_RETRIES, backoff
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue
            if _retry._is_server_error(response.status_code):
                delay = self._handle_server_error(
                    response, attempt, MAX_RETRIES, backoff
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue
            if not response.is_success:
                raise HostawayResponseError(
                    f"Unexpected response status: {response.status_code}"
                )
            return response
        raise HostawayResponseError(
            "Request loop exited without returning"
        )  # pragma: no cover

    async def _handle_forbidden_response(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        _retried_auth: bool,
    ) -> httpx.Response:
        """Handle 403 responses as auth or reservation-lock failures."""
        body = _redaction._safe_response_body(response)
        if _retried_auth:
            raise HostawayAuthError(
                "Forbidden after token refresh: "
                f"{method} {path} returned 403; body: {body}"
            )
        if not _redaction._is_auth_403_body(body):
            _LOGGER.debug(
                "Hostaway returned 403 for %s %s "
                "(classified as locked/non-writable); response body: %s",
                method,
                path,
                body,
            )
            raise HostawayReservationLockedError(
                f"Reservation locked: {method} {path} returned 403; body: {body}"
            )
        _LOGGER.warning(
            "Hostaway returned 403 for %s %s "
            "(classified as auth failure); refreshing token "
            "and retrying once. Response body: %s",
            method,
            path,
            body,
        )
        self._token_manager.invalidate()
        return await self._request(
            method,
            path,
            params=params,
            json=json_body,
            _retried_auth=True,
        )

    def _handle_rate_limit_response(
        self,
        response: httpx.Response,
        attempt: int,
        max_retries: int,
        backoff: float,
    ) -> float:
        """Handle a 429 response and return the retry delay."""
        if attempt >= max_retries:
            raise HostawayRateLimitError(
                "Rate limit exceeded after max retries",
                retry_after=_retry._parse_retry_after(response),
            )
        delay = _retry._calculate_backoff(backoff, response)
        _LOGGER.warning(
            "Rate limited, retrying in %.1fs (attempt %d/%d)",
            delay,
            attempt + 1,
            max_retries,
        )
        return delay

    def _handle_server_error(
        self,
        response: httpx.Response,
        attempt: int,
        max_retries: int,
        backoff: float,
    ) -> float:
        """Handle a 5xx response and return the retry delay."""
        if attempt >= max_retries:
            raise HostawayConnectionError(
                f"Server error {response.status_code} after {max_retries} retries"
            )
        delay = _retry._jittered_delay(backoff)
        _LOGGER.warning(
            "Server error %d, retrying in %.1fs (attempt %d/%d)",
            response.status_code,
            delay,
            attempt + 1,
            max_retries,
        )
        return delay

    async def _request_results(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        error_prefix: str = "API error",
    ) -> list[dict[str, Any]]:
        """Return a validated result list from a GET endpoint."""
        return self._extract_results(
            self._parse_response(await self._request("GET", path, params=params)),
            error_prefix=error_prefix,
        )

    async def _mutate(
        self,
        method: str,
        path: str,
        data: dict[str, Any],
        error_prefix: str,
        missing_result: str,
    ) -> dict[str, Any]:
        """Return a successful mutation payload that must be an object."""
        result = self._ensure_success(
            self._parse_response(await self._request(method, path, json=data)),
            error_prefix,
        )
        if not isinstance(result, dict):
            raise HostawayResponseError(missing_result)
        return result

    async def _paginate_offset(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        error_prefix: str = "API error",
    ) -> list[dict[str, Any]]:
        """Collect all offset-paginated results from an endpoint."""
        items: list[dict[str, Any]] = []
        offset = 0
        base_params = dict(params or {})
        while True:
            page = await self._request_results(
                path,
                params={**base_params, "offset": offset, "limit": DEFAULT_PAGE_LIMIT},
                error_prefix=error_prefix,
            )
            items.extend(page)
            if len(page) < DEFAULT_PAGE_LIMIT:
                return items
            offset += DEFAULT_PAGE_LIMIT

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse response JSON into a dictionary."""
        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise HostawayResponseError("Response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise HostawayResponseError("Response must be a JSON object")
        return data

    def _ensure_success(
        self, data: dict[str, Any], error_prefix: str = "API error"
    ) -> Any:
        """Validate the Hostaway status field and return the result payload."""
        status = data.get("status")
        if status is not None and status != "success":
            raise HostawayResponseError(f"{error_prefix}: {data.get('result', status)}")
        return data.get("result")

    def _extract_results(
        self, data: dict[str, Any], *, error_prefix: str = "API error"
    ) -> list[dict[str, Any]]:
        """Return a successful result payload that must be a list of objects."""
        results = self._ensure_success(data, error_prefix)
        if results is None:
            raise HostawayResponseError("Response missing 'result' field")
        if not isinstance(results, list):
            raise HostawayResponseError("'result' must be a list")
        if any(not isinstance(item, dict) for item in results):
            raise HostawayResponseError("'result' items must be JSON objects")
        return results
