# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Token manager for Hostaway OAuth 2.0 Client Credentials flow.

OQ-001 (assumption, **not** documented Hostaway behaviour): Hostaway does
not say whether ``POST /v1/accessTokens`` is charged against the general
request counters. This module assumes it is. Assuming wrongly in this
direction costs one request's worth of budget roughly twice a day;
assuming wrongly in the other direction would spend budget the server is
counting and we are not, which is exactly the failure the limiter exists
to prevent. Revisit if Hostaway ever documents it.
"""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import httpx

from custom_components.hostaway.api import retry as _retry
from custom_components.hostaway.api.const import (
    GRANT_TYPE,
    SCOPE,
    TOKEN_URL,
)
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import AccessToken
from custom_components.hostaway.api.rate_limit import (
    AccountRateLimiter,
    RequestContext,
    deadline_exception,
    ensure_request_context,
    sleep_within_deadline,
)
from custom_components.hostaway.api.retry import parse_rate_limit_headers

#: The token endpoint, as the limiter classifies it.
_TOKEN_METHOD = "POST"
_TOKEN_PATH = "/v1/accessTokens"

# Buffer seconds before expiry to trigger proactive refresh
_REFRESH_BUFFER = 300


class HostawayTokenManager:
    """Manage OAuth 2.0 token lifecycle for the Hostaway API.

    Handles token acquisition via Client Credentials grant,
    in-memory caching, proactive refresh with buffer, concurrent
    access serialization via asyncio.Lock double-checked locking,
    and the mandatory 1-second post-generation delay.

    Attributes:
        _client_id: Hostaway API client ID.
        _client_secret: Client secret.
        _http: Injected async HTTP client.
        _token_url: OAuth token endpoint URL.
        _cached_token: In-memory token cache.
        _lock: Concurrent access guard.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient,
        *,
        token_url: str = TOKEN_URL,
        limiter: AccountRateLimiter | None = None,
    ) -> None:
        """Initialize HostawayTokenManager.

        Args:
            client_id: Hostaway API client ID.
            client_secret: Client secret.
            http_client: Async HTTP client for token requests.
            token_url: OAuth token endpoint URL.
            limiter: Paces token requests. Omitted means unlimited.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._token_url = token_url
        self._cached_token: AccessToken | None = None
        self._limiter = limiter
        self._lock = asyncio.Lock()

    def seed_token(self, token: AccessToken) -> None:
        """Seed the in-memory cache with a pre-existing token.

        Called during startup to avoid a token request if a valid
        persisted token exists.

        Args:
            token: A previously persisted AccessToken.
        """
        self._cached_token = token

    def invalidate(self) -> None:
        """Clear the cached token, forcing re-acquisition.

        Used when a 401/403 response indicates the token is invalid.
        """
        self._cached_token = None

    async def get_token(self) -> str:
        """Get a valid access token, acquiring or refreshing as needed.

        Uses double-checked locking: first check without lock avoids
        contention in the common case; lock serializes only actual
        token acquisition; second check inside lock prevents redundant
        requests from concurrent callers.

        Returns:
            A valid Bearer access token string.

        Raises:
            HostawayAuthError: If credentials are invalid.
            HostawayConnectionError: If the token endpoint is
                unreachable.
            HostawayRateLimitError: If the token endpoint returns
                HTTP 429.
            HostawayResponseError: If the response format is
                unexpected.
        """
        cached = self._cached_token
        if cached is not None and not cached.is_expired(
            buffer_seconds=_REFRESH_BUFFER,
        ):
            return await self._ready_token(cached)

        with ensure_request_context() as ctx:
            await self._acquire_lock(ctx)
            try:
                cached = self._cached_token
                if cached is not None and not cached.is_expired(
                    buffer_seconds=_REFRESH_BUFFER,
                ):
                    return await self._ready_token(cached)

                token = await self._request_token()
                # Cached before the readiness wait, not after. The token is
                # already paid for in budget; discarding it because this
                # caller ran out of time would buy another one on the next
                # cycle and run out of time again.
                self._cached_token = token
                return await self._ready_token(token)
            finally:
                self._lock.release()

    async def _ready_token(self, token: AccessToken) -> str:
        """Return a token's value once Hostaway will accept it.

        Args:
            token: The token to hand out.

        Returns:
            The token string, usable immediately.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation has too
                little time left to wait out the readiness delay.
            HostawayRateLimitWaitTimeout: If any other operation has.
        """
        delay = token.seconds_until_ready
        if delay > 0:
            # Charged against the ambient operation's deadline like any
            # other wait: a caller with one second left must not spend it
            # sleeping here and then fail at the next acquisition anyway.
            with ensure_request_context() as ctx:
                await self._post_generation_delay(delay, ctx)
        return token.access_token

    async def _acquire_lock(self, ctx: RequestContext) -> None:
        """Take the refresh lock without outliving the operation's deadline.

        Waiting behind another caller's token refresh is waiting all the
        same. A sheddable refresh that queues here for longer than its
        deadline would otherwise ignore the budget it was given, and a
        later interactive caller could not overtake it.

        Args:
            ctx: The ambient operation context.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation runs out
                of time waiting for the lock.
            HostawayRateLimitWaitTimeout: If any other operation does.
        """
        if self._limiter is None:
            await self._lock.acquire()
            return
        remaining = ctx.remaining(time.monotonic())
        if remaining <= 0:
            raise deadline_exception(
                shed_on_timeout=ctx.policy.shed_on_timeout,
                waited=ctx.policy.duration,
            )
        try:
            async with asyncio.timeout(remaining):
                await self._lock.acquire()
        except TimeoutError as exc:
            raise deadline_exception(
                shed_on_timeout=ctx.policy.shed_on_timeout,
                waited=ctx.policy.duration - ctx.remaining(time.monotonic()),
            ) from exc

    async def _post_generation_delay(self, delay: float, ctx: RequestContext) -> None:
        """Wait out Hostaway's mandatory post-generation delay.

        Args:
            delay: Seconds the token is not yet usable for.
            ctx: The ambient operation context.

        Raises:
            HostawayRateLimitShedError: If a sheddable operation has too
                little time left to wait it out.
            HostawayRateLimitWaitTimeout: If any other operation has.
        """
        if self._limiter is None:
            await asyncio.sleep(delay)
            return
        await sleep_within_deadline(delay, ctx)

    async def _request_token(self) -> AccessToken:
        """Request a new token from the Hostaway token endpoint.

        Returns:
            A new AccessToken from the token endpoint response.

        Raises:
            HostawayAuthError: On 401 Unauthorized.
            HostawayConnectionError: On network failure.
            HostawayResponseError: On unexpected response format.
        """
        # OQ-001: assumed to count against the general counters. See the
        # module docstring; this is a conservative guess, not documented.
        if self._limiter is not None:
            await self._limiter.acquire(_TOKEN_METHOD, _TOKEN_PATH)
        try:
            response = await self._http.post(
                self._token_url,
                data={
                    "grant_type": GRANT_TYPE,
                    "scope": SCOPE,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
        except httpx.RequestError as exc:
            raise HostawayConnectionError(
                f"Failed to connect to token endpoint: {exc}",
            ) from exc

        if response.status_code == 401:
            raise HostawayAuthError("Invalid client credentials")

        if response.status_code == 429:
            headers = parse_rate_limit_headers(response)
            if self._limiter is not None:
                self._limiter.note_rate_limited(
                    headers.applied,
                    headers.retry_at,
                    _TOKEN_METHOD,
                    _TOKEN_PATH,
                    limit=headers.limit,
                    remaining=headers.remaining,
                )
            raise HostawayRateLimitError(
                "Token endpoint rate limited",
                retry_after=_retry._parse_retry_after(response),
            )

        if response.status_code != 200:
            raise HostawayResponseError(
                f"Unexpected token response status: {response.status_code}",
            )

        # Capture issued_at after successful response so
        # post-generation delay is measured from acquisition time
        now = datetime.now(UTC)

        try:
            data = response.json()
        except Exception as exc:
            raise HostawayResponseError(
                "Token response is not valid JSON",
            ) from exc

        if not isinstance(data, dict):
            raise HostawayResponseError(
                "Token response must be a JSON object",
            )

        # Hostaway wraps token in {"status":"success","result":{...}}
        if "result" in data:
            if data.get("status") != "success":
                err_detail = data.get("result", "unknown error")
                raise HostawayResponseError(
                    f"Token request failed: {err_detail}",
                )
            result = data["result"]
            if not isinstance(result, dict):
                raise HostawayResponseError(
                    "Token result must be a JSON object",
                )
            data = result

        try:
            token = AccessToken(
                access_token=data["access_token"],
                token_type=data.get("token_type", "Bearer"),
                expires_in=data["expires_in"],
                issued_at=now,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HostawayResponseError(
                f"Malformed token response: {exc}",
            ) from exc

        return token
