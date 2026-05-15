# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for HostawayApiClient (T021-T023)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import httpx
import pytest
import respx

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient, _safe_response_body
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

    async def test_persistent_403_includes_response_body(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test persistent 403 raises HostawayAuthError with diagnostic context."""
        body = (
            '{"status":"fail","result":"You don\'t have permission to '
            'modify this reservation"}'
        )
        respx.put(f"{FAKE_BASE_URL}/v1/reservations/12345").mock(
            return_value=httpx.Response(403, text=body)
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayAuthError) as exc_info:
            await client._request(
                "PUT", "/v1/reservations/12345", json={"doorCode": "9999"}
            )

        msg = str(exc_info.value)
        assert "Forbidden after token refresh" in msg
        assert "PUT" in msg
        assert "/v1/reservations/12345" in msg
        assert "403" in msg
        assert "permission to modify" in msg

    async def test_persistent_403_truncates_long_body(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test persistent 403 truncates response body in error message."""
        long_body = "X" * 2000
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            return_value=httpx.Response(403, text=long_body)
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayAuthError) as exc_info:
            await client._request("GET", "/v1/listings")

        msg = str(exc_info.value)
        # Full body must not appear; truncation suffix must be present.
        assert long_body not in msg
        assert "..." in msg
        # Loose upper bound: prefix + 500 truncated body + suffix.
        assert len(msg) < 1000

    async def test_403_logs_first_response_body(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test first 403 emits a WARNING log with method, path, and body snippet."""
        body = '{"status":"fail","result":"token expired"}'
        route = respx.put(f"{FAKE_BASE_URL}/v1/reservations/77")
        route.side_effect = [
            httpx.Response(403, text=body),
            httpx.Response(200, json={"status": "success", "result": {"id": 77}}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with caplog.at_level("WARNING", logger="custom_components.hostaway.api.client"):
            response = await client._request(
                "PUT", "/v1/reservations/77", json={"doorCode": "1234"}
            )

        assert response.status_code == 200
        matching = [
            r
            for r in caplog.records
            if r.levelname == "WARNING"
            and "PUT" in r.getMessage()
            and "/v1/reservations/77" in r.getMessage()
            and "token expired" in r.getMessage()
        ]
        messages = [r.getMessage() for r in caplog.records]
        assert matching, f"Expected WARNING log; got: {messages}"

    async def test_403_body_unavailable_does_not_crash(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test 403 with unreadable body still raises HostawayAuthError cleanly."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=httpx.Response(403))

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        # Force _safe_response_body to simulate unreadable body.
        with (
            patch(
                "custom_components.hostaway.api.client._safe_response_body",
                return_value="<unavailable>",
            ),
            pytest.raises(HostawayAuthError) as exc_info,
        ):
            await client._request("GET", "/v1/listings")

        msg = str(exc_info.value)
        assert "Forbidden after token refresh" in msg
        assert "<unavailable>" in msg

    def test_safe_response_body_returns_unavailable_on_read_error(self) -> None:
        """Test _safe_response_body returns "<unavailable>" if reading body raises."""
        response = Mock(spec=httpx.Response)
        # Accessing .text raises, simulating a decode/IO failure.
        type(response).text = PropertyMock(side_effect=RuntimeError("decode boom"))

        assert _safe_response_body(response) == "<unavailable>"

    def test_safe_response_body_truncates_long_text(self) -> None:
        """Test _safe_response_body truncates with "..." suffix beyond max_len."""
        response = Mock(spec=httpx.Response)
        type(response).text = PropertyMock(return_value="A" * 1000)

        result = _safe_response_body(response, max_len=100)

        assert result == "A" * 100 + "..."

    def test_safe_response_body_returns_short_text_verbatim(self) -> None:
        """Test _safe_response_body returns short bodies without truncation."""
        response = Mock(spec=httpx.Response)
        type(response).text = PropertyMock(return_value="short body")

        assert _safe_response_body(response) == "short body"

    def test_safe_response_body_redacts_sensitive_json_fields(self) -> None:
        """Test sensitive JSON fields are replaced with <redacted>."""
        response = Mock(spec=httpx.Response)
        payload = {
            "doorCode": "1234",
            "password": "hunter2",
            "access_token": "abc",
            "client_secret": "shh",
            "apiKey": "xyz",
            "Authorization": "Bearer foo",
            "reservationId": 42,
            "statusCode": 403,
        }
        type(response).text = PropertyMock(return_value=json.dumps(payload))

        result = _safe_response_body(response)

        parsed = json.loads(result)
        assert parsed["doorCode"] == "<redacted>"
        assert parsed["password"] == "<redacted>"
        assert parsed["access_token"] == "<redacted>"
        assert parsed["client_secret"] == "<redacted>"
        assert parsed["apiKey"] == "<redacted>"
        assert parsed["Authorization"] == "<redacted>"
        # Non-sensitive fields must be preserved.
        assert parsed["reservationId"] == 42
        assert parsed["statusCode"] == 403

    def test_safe_response_body_redacts_nested_sensitive_fields(self) -> None:
        """Test nested dict/list sensitive fields are redacted."""
        response = Mock(spec=httpx.Response)
        payload = {
            "data": {"doorCode": "9999", "name": "Front Door"},
            "items": [{"token": "t1"}, {"id": 1}],
        }
        type(response).text = PropertyMock(return_value=json.dumps(payload))

        result = _safe_response_body(response)

        parsed = json.loads(result)
        assert parsed["data"]["doorCode"] == "<redacted>"
        assert parsed["data"]["name"] == "Front Door"
        assert parsed["items"][0]["token"] == "<redacted>"
        assert parsed["items"][1]["id"] == 1

    def test_safe_response_body_escapes_newlines_in_plain_text(self) -> None:
        """Test CR/LF in non-JSON bodies are escaped to prevent log injection."""
        response = Mock(spec=httpx.Response)
        type(response).text = PropertyMock(
            return_value="oops\nFAKE WARNING: pwned\r\nmore"
        )

        result = _safe_response_body(response)

        assert "\n" not in result
        assert "\r" not in result
        assert "\\n" in result
        assert "\\r" in result

    def test_safe_response_body_strips_other_control_chars(self) -> None:
        """Test C0 control characters are stripped from bodies."""
        response = Mock(spec=httpx.Response)
        type(response).text = PropertyMock(return_value="ok\x00\x07\x1bhello")

        result = _safe_response_body(response)

        assert result == "okhello"

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

        route = respx.get(f"{FAKE_BASE_URL}/v1/reservations")
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
