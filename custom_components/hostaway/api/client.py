# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Hostaway API requests."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports
# aislop-ignore-file complexity/file-too-large -- cohesive HTTP client abstraction

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from custom_components.hostaway.api import redaction as _redaction
from custom_components.hostaway.api import responses as _responses
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
from custom_components.hostaway.api.custom_fields import (
    read_listing_with_custom_fields,
    read_reservation_with_custom_fields,
)
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
    HostawayMutationResultError,
    HostawayRateLimitError,
    HostawayReservationLockedError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayListing, HostawayReservation
from custom_components.hostaway.api.rate_limit import (
    AccountRateLimiter,
    RequestContext,
    current_request_context,
    ensure_request_context,
    sleep_within_deadline,
)
from custom_components.hostaway.api.reservations import (
    fetch_all_reservations,
    fetch_reservation_items,
    parse_reservations,
)

_LOGGER = logging.getLogger(__name__)


class HostawayApiClient:
    """HTTP client for authenticated Hostaway API requests."""

    def __init__(
        self,
        token_manager: HostawayTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
        limiter: AccountRateLimiter | None = None,
    ) -> None:
        """Initialize the API client.

        Args:
            token_manager: Supplies bearer tokens.
            http_client: Home Assistant's shared httpx client. It is used
                as given: the limiter sits in front of it rather than
                wrapping it, so nothing here may subclass, re-configure or
                attach hooks to a client the rest of Home Assistant shares.
            base_url: API root.
            limiter: Paces outbound requests. When omitted the client is
                unlimited, which is what every non-Home-Assistant caller
                and every existing test gets.
        """
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._limiter = limiter

    @property
    def limiter(self) -> AccountRateLimiter | None:
        """Return the limiter pacing this client, if it has one.

        Returns:
            The limiter, or ``None`` when the client is unpaced.
        """
        return self._limiter

    async def test_connection(self) -> bool:
        """Validate credentials with a lightweight API call."""
        await self._request("GET", "/v1/listings", params={"limit": 1})
        return True

    async def get_listings_page(
        self, offset: int = 0, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[HostawayListing]:
        """Return one page of listings."""
        items = await self._request_results(
            "/v1/listings",
            params={"offset": offset, "limit": limit, "includeResources": 1},
        )
        return [HostawayListing.from_api_response(item) for item in items]

    async def get_all_listings(self) -> list[HostawayListing]:
        """Return all listings."""
        items = await self._paginate_offset(
            "/v1/listings", params={"includeResources": 1}
        )
        return [HostawayListing.from_api_response(item) for item in items]

    async def get_reservations_page(
        self,
        listing_id: int,
        after_id: int | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[HostawayReservation]:
        """Return one page of reservations for a listing."""
        items = await fetch_reservation_items(
            self._request_results, listing_id, after_id=after_id, limit=limit
        )
        return parse_reservations(items, listing_id)

    async def get_all_reservations(self, listing_id: int) -> list[HostawayReservation]:
        """Return all reservations for a listing."""
        return await fetch_all_reservations(
            lambda after_id, limit: fetch_reservation_items(
                self._request_results, listing_id, after_id=after_id, limit=limit
            ),
            listing_id,
        )

    async def get_listing(self, listing_id: int) -> HostawayListing:
        """Return one listing with custom-field resources included."""
        return HostawayListing.from_api_response(
            await self.get_listing_payload(listing_id)
        )

    async def get_reservation(self, reservation_id: int) -> HostawayReservation:
        """Return one reservation with custom-field resources included."""
        return HostawayReservation.from_api_response(
            await self.get_reservation_payload(reservation_id)
        )

    async def get_listing_payload(self, listing_id: int) -> dict[str, Any]:
        """Return one raw listing payload with custom-field resources included."""
        return await read_listing_with_custom_fields(self._request, listing_id)

    async def get_reservation_payload(self, reservation_id: int) -> dict[str, Any]:
        """Return one raw reservation payload with custom-field resources included."""
        return await read_reservation_with_custom_fields(self._request, reservation_id)

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
        _responses.ensure_success(
            _responses.parse_response(
                await self._request("DELETE", f"/v1/tasks/{task_id}")
            ),
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

    async def update_reservation_custom_fields(
        self, reservation_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update reservation custom fields without ambiguous blind retries."""
        return await self._mutate(
            "PUT",
            f"/v1/reservations/{reservation_id}",
            data,
            "Update failed",
            "Update response missing 'result' object",
            retry_ambiguous=False,
        )

    async def update_listing(
        self, listing_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a listing."""
        return await self._mutate(
            "PUT",
            f"/v1/listings/{listing_id}",
            data,
            "Update failed",
            "Update response missing 'result' object",
        )

    async def update_listing_custom_fields(
        self, listing_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update listing custom fields without ambiguous blind retries."""
        return await self._mutate(
            "PUT",
            f"/v1/listings/{listing_id}",
            data,
            "Update failed",
            "Update response missing 'result' object",
            retry_ambiguous=False,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        _retried_auth: bool = False,
        retry_ambiguous: bool = True,
    ) -> httpx.Response:
        """Make an authenticated API request with retries."""
        # Installed once: the deadline covers the whole logical operation,
        # so a caller that established no context must not have a fresh one
        # synthesised for the token refresh, for every retry, and again for
        # the recursive re-entry after a 403.
        with ensure_request_context() as ctx:
            return await self._attempt_request(
                method,
                path,
                ctx,
                params=params,
                json=json,
                _retried_auth=_retried_auth,
                retry_ambiguous=retry_ambiguous,
            )

    async def _attempt_request(
        self,
        method: str,
        path: str,
        ctx: RequestContext,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        _retried_auth: bool = False,
        retry_ambiguous: bool = True,
    ) -> httpx.Response:
        """Run the retry loop for one request under a fixed deadline.

        Args:
            method: HTTP method.
            path: API path below the base URL.
            ctx: The ambient operation context, already installed.
            params: Query parameters.
            json: JSON body.
            _retried_auth: Whether a token refresh has already been tried.
            retry_ambiguous: Whether a request whose outcome is unknown may
                be retried.

        Returns:
            The successful response.
        """
        url = f"{self._base_url}{path}"
        backoff = INITIAL_BACKOFF
        for attempt in range(MAX_RETRIES + 1):
            token = await self._token_manager.get_token()
            # Inside the loop on purpose. Every attempt is a request the
            # server will see and count, so every attempt must be paced —
            # including the recursive re-entry after a 403 refresh.
            await self._acquire(method, path)
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
                if not retry_ambiguous:
                    raise HostawayConnectionError(
                        f"Failed to connect to Hostaway API: {exc}"
                    ) from exc
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
                await self._backoff_sleep(delay, ctx)
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
                    retry_ambiguous=retry_ambiguous,
                )
            if response.status_code == 404:
                raise HostawayResponseError(f"Resource not found: {path}")
            if response.status_code == 429:
                # Feed the server's own view back before deciding whether
                # to retry: the limiter must learn about the refusal even
                # when this attempt is the one that gives up.
                self._note_rate_limited(response, method, path)
                delay = self._handle_rate_limit_response(
                    response,
                    attempt,
                    MAX_RETRIES if retry_ambiguous else 0,
                    backoff,
                )
                await self._backoff_sleep(delay, ctx)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue
            if _retry._is_server_error(response.status_code):
                if not retry_ambiguous:
                    raise HostawayConnectionError(
                        f"Server error {response.status_code}"
                    )
                delay = self._handle_server_error(
                    response, attempt, MAX_RETRIES, backoff
                )
                await self._backoff_sleep(delay, ctx)
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

    async def _acquire(self, method: str, path: str) -> None:
        """Wait for rate-limit capacity before a request leaves the process.

        Args:
            method: HTTP method.
            path: Request path.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation ran out of
                time while queued.
            HostawayRateLimitWaitTimeout: If any other operation did.
        """
        if self._limiter is None:
            return
        started = time.monotonic()
        await self._limiter.acquire(method, path)
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        ctx = current_request_context()
        gates = self._limiter.snapshot().gates
        _LOGGER.debug(
            "Admitted %s %s (%s) after %.3fs; remaining this window: %s",
            method,
            path,
            ctx.priority.name.lower(),
            time.monotonic() - started,
            ", ".join(
                f"{name} {max(gate.effective_budget - gate.admitted_in_window, 0)}"
                f"/{gate.effective_budget}"
                for name, gate in sorted(gates.items())
            ),
        )

    async def _backoff_sleep(self, delay: float, ctx: RequestContext) -> None:
        """Sleep before a retry without outliving the operation's deadline.

        An unlimited client keeps its historical behaviour: the deadline is
        a rate-limiting concept, and a caller that never opted into pacing
        must not start failing on it.

        Args:
            delay: The backoff the retry policy asked for.
            ctx: The ambient operation context.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation cannot
                finish the sleep in the time it has left.
            HostawayRateLimitWaitTimeout: If any other operation cannot.
        """
        if self._limiter is None:
            await asyncio.sleep(delay)
            return
        await sleep_within_deadline(delay, ctx)

    def _note_rate_limited(
        self, response: httpx.Response, method: str, path: str
    ) -> None:
        """Report a server refusal to the limiter.

        Args:
            response: The 429 response.
            method: HTTP method of the refused request.
            path: Path of the refused request.
        """
        headers = _retry.parse_rate_limit_headers(response)
        if self._limiter is None:
            _LOGGER.warning(
                "Hostaway refused %s %s with HTTP 429. Counter: %s",
                method,
                path,
                headers.applied or "unreported",
            )
            return
        _LOGGER.warning(
            "Hostaway refused %s %s with HTTP 429: this is server pushback, "
            "not the integration's own pacing. Counter: %s",
            method,
            path,
            headers.applied or "unreported",
        )
        self._limiter.note_rate_limited(
            headers.applied,
            headers.retry_at,
            method,
            path,
            limit=headers.limit,
            remaining=headers.remaining,
        )

    # aislop-ignore-next-line complexity/too-many-params -- carries request context
    async def _handle_forbidden_response(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        _retried_auth: bool,
        retry_ambiguous: bool,
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
            if not path.startswith("/v1/reservations/"):
                raise HostawayResponseError(
                    f"Forbidden: {method} {path} returned 403; body: {body}"
                )
            raise HostawayReservationLockedError(
                f"Reservation locked: {method} {path} returned 403; body: {body}"
            )
        if not retry_ambiguous:
            self._token_manager.invalidate()
            raise HostawayAuthError(
                "Forbidden response for custom-field mutation was not retried: "
                f"{method} {path} returned 403; body: {body}"
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
            retry_ambiguous=retry_ambiguous,
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
        return _responses.extract_results(
            _responses.parse_response(await self._request("GET", path, params=params)),
            error_prefix=error_prefix,
        )

    async def _mutate(
        self,
        method: str,
        path: str,
        data: dict[str, Any],
        error_prefix: str,
        missing_result: str,
        *,
        retry_ambiguous: bool = True,
    ) -> dict[str, Any]:
        """Return a successful mutation payload that must be an object."""
        response = await self._request(
            method,
            path,
            json=data,
            retry_ambiguous=retry_ambiguous,
        )
        try:
            parsed = _responses.parse_response(response)
        except HostawayResponseError as exc:
            if not retry_ambiguous:
                raise HostawayMutationResultError(f"{missing_result}: {exc}") from exc
            raise
        result = _responses.ensure_success(parsed, error_prefix)
        if not isinstance(result, dict):
            raise HostawayMutationResultError(missing_result)
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
