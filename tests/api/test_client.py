# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for HostawayApiClient (T021-T023)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.const import DEFAULT_PAGE_LIMIT
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
    HostawayResponseError,
)
from tests.helpers import (
    FAKE_BASE_URL,
    FAKE_TOKEN,
    make_listing_response,
    make_reservation_response,
)


def _make_mock_token_manager() -> Mock:
    """Create a mock token manager that returns FAKE_TOKEN."""
    tm = Mock(spec=HostawayTokenManager)
    tm.get_token = AsyncMock(return_value=FAKE_TOKEN)
    tm.invalidate = Mock()
    return tm


# --- T021: HTTP client core tests ---


class TestHttpClientCore:
    """Tests for HostawayApiClient request handling."""

    async def test_successful_get_request(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful GET request with Bearer auth header."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(200, json={"status": "success", "result": []})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Verify Bearer auth header was sent
        request = route.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"

    async def test_successful_put_request(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful PUT request with Bearer auth and JSON body."""
        route = respx.put(f"{FAKE_BASE_URL}/v1/reservations/123").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": {"id": 123}},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        response = await client._request(
            "PUT", "/v1/reservations/123", json={"doorCode": "9999"}
        )

        assert response.status_code == 200
        # Verify Bearer auth header and JSON body were sent
        request = route.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert b"doorCode" in request.content

    async def test_429_triggers_backoff_retry(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 429 response triggers exponential backoff retry."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"status": "success", "result": []}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        mock_sleep.assert_called()

    async def test_429_max_retries_raises_rate_limit_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test after max 3 retries on 429, raises HostawayRateLimitError."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HostawayRateLimitError),
        ):
            await client._request("GET", "/v1/listings")

    async def test_403_triggers_token_refresh_retry(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 403 triggers token invalidation + refresh + single retry."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [
            httpx.Response(403),
            httpx.Response(200, json={"status": "success", "result": []}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        tm.invalidate.assert_called_once()

    async def test_404_raises_response_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 404 raises HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError):
            await client._request("GET", "/v1/listings")

    async def test_5xx_triggers_retry_with_backoff(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 5xx triggers retry with backoff."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [
            httpx.Response(502),
            httpx.Response(200, json={"status": "success", "result": []}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        mock_sleep.assert_called()

    async def test_network_error_raises_connection_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test network error raises HostawayConnectionError."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HostawayConnectionError),
        ):
            await client._request("GET", "/v1/listings")


# --- T022: Pagination tests ---


class TestPagination:
    """Tests for pagination helpers."""

    async def test_get_all_listings_paginates_with_offset(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test get_all_listings() paginates until fewer results."""
        # First page: full (DEFAULT_PAGE_LIMIT items)
        page1 = [
            make_listing_response(id=i, name=f"Listing {i}")
            for i in range(1, DEFAULT_PAGE_LIMIT + 1)
        ]
        # Second page: partial (fewer than limit)
        page2 = [
            make_listing_response(id=i, name=f"Listing {i}")
            for i in range(DEFAULT_PAGE_LIMIT + 1, DEFAULT_PAGE_LIMIT + 11)
        ]

        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [
            httpx.Response(200, json={"status": "success", "result": page1}),
            httpx.Response(200, json={"status": "success", "result": page2}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        listings = await client.get_all_listings()

        assert len(listings) == DEFAULT_PAGE_LIMIT + 10
        assert route.call_count == 2

    async def test_get_all_reservations_paginates_with_after_id(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test get_all_reservations() paginates with afterId cursor."""
        # First page: full
        page1 = [
            make_reservation_response(id=i, listingMapId=100)
            for i in range(1, DEFAULT_PAGE_LIMIT + 1)
        ]
        # Second page: partial
        page2 = [
            make_reservation_response(id=i, listingMapId=100)
            for i in range(DEFAULT_PAGE_LIMIT + 1, DEFAULT_PAGE_LIMIT + 6)
        ]

        route = respx.get(f"{FAKE_BASE_URL}/v1/listings/100/reservations")
        route.side_effect = [
            httpx.Response(200, json={"status": "success", "result": page1}),
            httpx.Response(200, json={"status": "success", "result": page2}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        reservations = await client.get_all_reservations(100)

        assert len(reservations) == DEFAULT_PAGE_LIMIT + 5
        assert route.call_count == 2

    async def test_empty_result_returns_empty_list(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test handles empty result set (returns empty list)."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(200, json={"status": "success", "result": []})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        listings = await client.get_all_listings()

        assert listings == []

    async def test_single_page_no_extra_requests(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test single page result makes no extra requests."""
        page = [make_listing_response(id=i, name=f"Listing {i}") for i in range(1, 11)]

        route = respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(200, json={"status": "success", "result": page})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        listings = await client.get_all_listings()

        assert len(listings) == 10
        assert route.call_count == 1


# --- T023: test_connection tests ---


class TestConnection:
    """Tests for test_connection()."""

    async def test_successful_connection(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful connection validates credentials."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(200, json={"status": "success", "result": []})
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.test_connection()

        assert result is True

    async def test_auth_failure_propagates(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test auth failure propagates HostawayAuthError."""
        tm = _make_mock_token_manager()
        tm.get_token.side_effect = HostawayAuthError("Invalid credentials")

        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayAuthError):
            await client.test_connection()

    async def test_connection_failure_propagates(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test connection failure propagates HostawayConnectionError."""
        tm = _make_mock_token_manager()
        tm.get_token.side_effect = HostawayConnectionError("Network error")

        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayConnectionError):
            await client.test_connection()
