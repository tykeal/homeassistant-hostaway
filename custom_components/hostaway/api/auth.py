# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Token manager for Hostaway OAuth 2.0 Client Credentials flow."""

# aislop-ignore-file ai-slop/hallucinated-import -- in-repo component imports

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import httpx

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
    ) -> None:
        """Initialize HostawayTokenManager.

        Args:
            client_id: Hostaway API client ID.
            client_secret: Client secret.
            http_client: Async HTTP client for token requests.
            token_url: OAuth token endpoint URL.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._token_url = token_url
        self._cached_token: AccessToken | None = None
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
            return cached.access_token

        async with self._lock:
            cached = self._cached_token
            if cached is not None and not cached.is_expired(
                buffer_seconds=_REFRESH_BUFFER,
            ):
                return cached.access_token

            token = await self._request_token()
            # Enforce post-generation delay
            delay = token.seconds_until_ready
            if delay > 0:
                await asyncio.sleep(delay)
            self._cached_token = token
            return token.access_token

    async def _request_token(self) -> AccessToken:
        """Request a new token from the Hostaway token endpoint.

        Returns:
            A new AccessToken from the token endpoint response.

        Raises:
            HostawayAuthError: On 401 Unauthorized.
            HostawayConnectionError: On network failure.
            HostawayResponseError: On unexpected response format.
        """
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
            retry_after: float | None = None
            header = response.headers.get("Retry-After")
            if header is not None:
                with contextlib.suppress(ValueError):
                    retry_after = float(header)
            raise HostawayRateLimitError(
                "Token endpoint rate limited",
                retry_after=retry_after,
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
