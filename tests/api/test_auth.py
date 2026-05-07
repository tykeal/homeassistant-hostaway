# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for HostawayTokenManager (T019-T020)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.const import TOKEN_READY_DELAY
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import AccessToken
from tests.helpers import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_TOKEN,
    FAKE_TOKEN_URL,
    make_token_response,
)

# --- T019: Token acquisition tests ---


class TestTokenAcquisition:
    """Tests for initial token acquisition via get_token()."""

    async def test_successful_get_token(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful token acquisition via POST."""
        respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            token = await manager.get_token()

        assert token == FAKE_TOKEN
        mock_sleep.assert_called_once()

    async def test_post_generation_delay(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 1-second post-generation delay is enforced."""
        respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await manager.get_token()

        # sleep should be called with a value <= TOKEN_READY_DELAY
        call_args = mock_sleep.call_args[0][0]
        assert 0 <= call_args <= TOKEN_READY_DELAY

    async def test_cached_token_reuse(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test second get_token() uses cache, no second HTTP request."""
        route = respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            token1 = await manager.get_token()
            token2 = await manager.get_token()

        assert token1 == token2
        assert route.call_count == 1

    async def test_auth_error_on_401(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test HostawayAuthError on 401 invalid credentials."""
        respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with pytest.raises(HostawayAuthError):
            await manager.get_token()

    async def test_connection_error_on_network_failure(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test HostawayConnectionError on network failure."""
        respx.post(FAKE_TOKEN_URL).mock(side_effect=httpx.ConnectError("fail"))

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with pytest.raises(HostawayConnectionError):
            await manager.get_token()

    async def test_connection_error_on_timeout(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test HostawayConnectionError on timeout."""
        respx.post(FAKE_TOKEN_URL).mock(side_effect=httpx.TimeoutException("timeout"))

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with pytest.raises(HostawayConnectionError):
            await manager.get_token()

    async def test_response_error_on_malformed_response(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test HostawayResponseError on missing access_token field."""
        respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "token_type": "Bearer",
                        "expires_in": 86400,
                    },
                },
            )
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with pytest.raises(HostawayResponseError):
            await manager.get_token()


# --- T020: Token refresh and persistence tests ---


class TestTokenRefreshAndPersistence:
    """Tests for token refresh, invalidation, and concurrency."""

    async def test_proactive_refresh_near_expiry(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test proactive refresh when token nears expiry."""
        route = respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        # Seed with a nearly-expired token
        expired_token = AccessToken(
            access_token="old-token",
            token_type="Bearer",
            expires_in=60,
            issued_at=datetime.now(UTC) - timedelta(seconds=55),
        )
        manager.seed_token(expired_token)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            token = await manager.get_token()

        # Should have fetched a new token
        assert token == FAKE_TOKEN
        assert route.call_count == 1

    async def test_invalidate_clears_cache(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test invalidate() clears cached token."""
        route = respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await manager.get_token()

        manager.invalidate()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await manager.get_token()

        assert route.call_count == 2

    async def test_concurrent_get_token_single_request(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test concurrent get_token() calls share single HTTP request."""
        route = respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await asyncio.gather(
                manager.get_token(),
                manager.get_token(),
                manager.get_token(),
            )

        assert all(t == FAKE_TOKEN for t in results)
        assert route.call_count == 1

    async def test_seed_token_no_http_call(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test seed_token() loads token without HTTP call."""
        route = respx.post(FAKE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response())
        )

        manager = HostawayTokenManager(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, mock_httpx_client
        )

        valid_token = AccessToken(
            access_token="seeded-token",
            token_type="Bearer",
            expires_in=86400,
            issued_at=datetime.now(UTC),
        )
        manager.seed_token(valid_token)

        token = await manager.get_token()

        assert token == "seeded-token"
        assert route.call_count == 0
