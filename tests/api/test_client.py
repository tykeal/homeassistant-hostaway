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
    HostawayReservationLockedError,
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
        body = '{"status":"fail","result":"invalid_token: token has expired"}'
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
        assert "expired" in msg

    async def test_persistent_403_truncates_long_body(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test persistent 403 truncates response body in error message."""
        # Prefix with an auth phrase so the body is classified as auth
        # and exercises the post-refresh persistent-403 path. The bulk
        # padding then verifies truncation in the resulting message.
        long_body = "invalid_token " + ("X" * 2000)
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

    async def test_403_locked_body_raises_locked_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """First 403 with locked-style body raises immediately, no retry."""
        body = '{"status":"fail","result":"Cannot modify reservation"}'
        route = respx.put(f"{FAKE_BASE_URL}/v1/reservations/59426054").mock(
            return_value=httpx.Response(403, text=body)
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayReservationLockedError):
            await client._request(
                "PUT", "/v1/reservations/59426054", json={"doorCode": "9999"}
            )

        tm.invalidate.assert_not_called()
        assert route.call_count == 1

    async def test_403_auth_body_still_refreshes_and_retries(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """403 with auth-phrase body follows token-refresh + retry path."""
        body = '{"status":"fail","result":"invalid_token: please reauthenticate"}'
        route = respx.put(f"{FAKE_BASE_URL}/v1/reservations/77")
        route.side_effect = [
            httpx.Response(403, text=body),
            httpx.Response(200, json={"status": "success", "result": {"id": 77}}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        response = await client._request(
            "PUT", "/v1/reservations/77", json={"doorCode": "1234"}
        )

        assert response.status_code == 200
        tm.invalidate.assert_called_once()
        assert route.call_count == 2

    async def test_403_unreadable_body_defaults_to_auth_refresh(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """403 with unreadable body falls back to auth path (back-compat)."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [
            httpx.Response(403),
            httpx.Response(200, json={"status": "success", "result": []}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with patch(
            "custom_components.hostaway.api.client._safe_response_body",
            return_value="<unavailable>",
        ):
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        tm.invalidate.assert_called_once()

    async def test_locked_error_message_includes_path_and_body(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Raised locked error contains method, path, status, body snippet."""
        body = '{"status":"fail","result":"Reservation is channel-managed"}'
        respx.put(f"{FAKE_BASE_URL}/v1/reservations/42").mock(
            return_value=httpx.Response(403, text=body)
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayReservationLockedError) as exc_info:
            await client._request(
                "PUT", "/v1/reservations/42", json={"doorCode": "0000"}
            )

        msg = str(exc_info.value)
        assert "Reservation locked" in msg
        assert "PUT" in msg
        assert "/v1/reservations/42" in msg
        assert "403" in msg
        assert "channel-managed" in msg

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
        response = httpx.Response(200, text="short body")

        assert _safe_response_body(response) == "short body"

    def test_safe_response_body_redacts_sensitive_json_fields(self) -> None:
        """Test sensitive JSON fields are replaced with <redacted>."""
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
        response = httpx.Response(200, text=json.dumps(payload))

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
        payload = {
            "data": {"doorCode": "9999", "name": "Front Door"},
            "items": [{"token": "t1"}, {"id": 1}],
        }
        response = httpx.Response(200, text=json.dumps(payload))

        result = _safe_response_body(response)

        parsed = json.loads(result)
        assert parsed["data"]["doorCode"] == "<redacted>"
        assert parsed["data"]["name"] == "Front Door"
        assert parsed["items"][0]["token"] == "<redacted>"
        assert parsed["items"][1]["id"] == 1

    def test_safe_response_body_redacts_secrets_inside_json_strings(self) -> None:
        """Test JSON string values are scrubbed for embedded secrets."""
        payload = {
            "result": "Authorization: Bearer top-secret-value rejected",
            "detail": "doorCode=1234 was reused",
        }
        response = httpx.Response(200, text=json.dumps(payload))

        result = _safe_response_body(response)

        parsed = json.loads(result)
        assert "top-secret-value" not in parsed["result"]
        assert "1234" not in parsed["detail"]
        assert "<redacted>" in parsed["result"]
        assert "<redacted>" in parsed["detail"]

    def test_safe_response_body_escapes_newlines_in_plain_text(self) -> None:
        """Test CR/LF in non-JSON bodies are escaped to prevent log injection."""
        response = httpx.Response(200, text="oops\nFAKE WARNING: pwned\r\nmore")

        result = _safe_response_body(response)

        assert "\n" not in result
        assert "\r" not in result
        assert "\\n" in result
        assert "\\r" in result

    def test_safe_response_body_strips_other_control_chars(self) -> None:
        """Test C0 control characters are stripped from bodies."""
        response = httpx.Response(200, text="ok\x00\x07\x1bhello")

        result = _safe_response_body(response)

        assert result == "okhello"

    def test_safe_response_body_redacts_plain_text_sensitive_fields(self) -> None:
        """Test pattern-based redaction for non-JSON bodies."""
        response = httpx.Response(
            200,
            text=(
                "doorCode=1234&reservationId=42 "
                "password: hunter2, token=abc.def, "
                'Authorization: "Bearer top-secret-value"'
            ),
        )

        result = _safe_response_body(response)

        assert "1234" not in result
        assert "hunter2" not in result
        assert "abc.def" not in result
        assert "top-secret-value" not in result
        assert "<redacted>" in result
        # Non-sensitive values must survive.
        assert "reservationId=42" in result

    def test_safe_response_body_redacts_bare_bearer_tokens(self) -> None:
        """Test bare 'Bearer <token>' fragments are redacted."""
        response = httpx.Response(
            200,
            text="Unauthorized: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        )

        result = _safe_response_body(response)

        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "Bearer <redacted>" in result

    def test_safe_response_body_redacts_unquoted_authorization_header(self) -> None:
        """Test unquoted 'Authorization: Bearer <token>' fully redacts the token."""
        response = httpx.Response(
            200,
            text="Authorization: Bearer top-secret-value\nother: keep",
        )

        result = _safe_response_body(response)

        assert "top-secret-value" not in result
        assert "<redacted>" in result
        assert "other: keep" in result

    def test_safe_response_body_returns_unavailable_on_redaction_failure(
        self,
    ) -> None:
        """Test secondary failures during redaction fall back to <unavailable>."""
        response = httpx.Response(200, text='{"x": 1}')

        with patch(
            "custom_components.hostaway.api.client._redact_sensitive",
            side_effect=RecursionError("too deep"),
        ):
            assert _safe_response_body(response) == "<unavailable>"

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


# --- Task API client method tests (T003-T006) ---


class TestCreateTask:
    """Tests for HostawayApiClient.create_task()."""

    async def test_create_task_success(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful task creation returns result dict."""
        respx.post(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"id": 1, "title": "Test Task"},
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.create_task({"title": "Test Task"})

        assert result == {"id": 1, "title": "Test Task"}

    async def test_create_task_sends_payload(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test that the JSON payload is sent correctly."""
        route = respx.post(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"id": 1, "title": "Test"},
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        await client.create_task({"title": "Test", "listingMapId": 123})

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["title"] == "Test"
        assert body["listingMapId"] == 123

    async def test_create_task_api_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test non-success status raises HostawayResponseError."""
        respx.post(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "fail",
                    "result": "validation error",
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="Create failed"):
            await client.create_task({"title": "Test"})

    async def test_create_task_missing_result(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test missing result object raises HostawayResponseError."""
        respx.post(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": []},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="missing 'result' object"):
            await client.create_task({"title": "Test"})

    async def test_create_task_404(self, mock_httpx_client: httpx.AsyncClient) -> None:
        """Test 404 response raises HostawayResponseError."""
        respx.post(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError):
            await client.create_task({"title": "Test"})


class TestUpdateTask:
    """Tests for HostawayApiClient.update_task()."""

    async def test_update_task_success(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful task update returns result dict."""
        respx.put(f"{FAKE_BASE_URL}/v1/tasks/42").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "id": 42,
                        "title": "Updated",
                        "status": "completed",
                    },
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.update_task(42, {"status": "completed"})

        assert result["id"] == 42
        assert result["status"] == "completed"

    async def test_update_task_sends_payload(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test that task_id is in URL and payload is sent."""
        route = respx.put(f"{FAKE_BASE_URL}/v1/tasks/99").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"id": 99, "status": "inProgress"},
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        await client.update_task(99, {"status": "inProgress"})

        request = route.calls[0].request
        assert "/v1/tasks/99" in str(request.url)
        body = json.loads(request.content)
        assert body["status"] == "inProgress"

    async def test_update_task_api_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test non-success status raises HostawayResponseError."""
        respx.put(f"{FAKE_BASE_URL}/v1/tasks/42").mock(
            return_value=httpx.Response(
                200,
                json={"status": "fail", "result": "not found"},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="Update failed"):
            await client.update_task(42, {"status": "completed"})

    async def test_update_task_missing_result(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test missing result raises HostawayResponseError."""
        respx.put(f"{FAKE_BASE_URL}/v1/tasks/42").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": "string"},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="missing 'result' object"):
            await client.update_task(42, {"title": "New"})

    async def test_update_task_404(self, mock_httpx_client: httpx.AsyncClient) -> None:
        """Test 404 raises HostawayResponseError."""
        respx.put(f"{FAKE_BASE_URL}/v1/tasks/999").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError):
            await client.update_task(999, {"title": "New"})


class TestDeleteTask:
    """Tests for HostawayApiClient.delete_task()."""

    async def test_delete_task_success(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful task deletion returns None."""
        respx.delete(f"{FAKE_BASE_URL}/v1/tasks/42").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": []},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        await client.delete_task(42)

    async def test_delete_task_sends_correct_request(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test DELETE is sent to correct URL."""
        route = respx.delete(f"{FAKE_BASE_URL}/v1/tasks/77").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": []},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        await client.delete_task(77)

        request = route.calls[0].request
        assert "/v1/tasks/77" in str(request.url)
        assert request.method == "DELETE"

    async def test_delete_task_api_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test non-success status raises HostawayResponseError."""
        respx.delete(f"{FAKE_BASE_URL}/v1/tasks/42").mock(
            return_value=httpx.Response(
                200,
                json={"status": "fail", "result": "not found"},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="Delete failed"):
            await client.delete_task(42)

    async def test_delete_task_404(self, mock_httpx_client: httpx.AsyncClient) -> None:
        """Test 404 raises HostawayResponseError."""
        respx.delete(f"{FAKE_BASE_URL}/v1/tasks/999").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError):
            await client.delete_task(999)


class TestGetTasks:
    """Tests for HostawayApiClient.get_tasks()."""

    async def test_get_tasks_success_no_params(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test successful get tasks returns list."""
        respx.get(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": [
                        {"id": 1, "title": "Task 1"},
                        {"id": 2, "title": "Task 2"},
                    ],
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.get_tasks()

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["title"] == "Task 2"

    async def test_get_tasks_paginates_with_offset(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test get_tasks() aggregates all offset pages."""
        page1 = [
            {"id": i, "title": f"Task {i}"} for i in range(1, DEFAULT_PAGE_LIMIT + 1)
        ]
        page2 = [{"id": DEFAULT_PAGE_LIMIT + 1, "title": "Task final"}]

        route = respx.get(f"{FAKE_BASE_URL}/v1/tasks")
        route.side_effect = [
            httpx.Response(200, json={"status": "success", "result": page1}),
            httpx.Response(200, json={"status": "success", "result": page2}),
        ]

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.get_tasks({"listingMapId": 123, "status": "pending"})

        assert len(result) == DEFAULT_PAGE_LIMIT + 1
        assert route.call_count == 2
        first_request = route.calls[0].request
        second_request = route.calls[1].request
        assert "listingMapId=123" in str(first_request.url)
        assert "status=pending" in str(first_request.url)
        assert f"limit={DEFAULT_PAGE_LIMIT}" in str(first_request.url)
        assert "offset=0" in str(first_request.url)
        assert f"offset={DEFAULT_PAGE_LIMIT}" in str(second_request.url)

    async def test_get_tasks_empty_list(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test empty result returns empty list."""
        respx.get(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": []},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.get_tasks()

        assert result == []

    async def test_get_tasks_api_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test non-success status raises HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"status": "fail", "result": "error"},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="Get tasks failed"):
            await client.get_tasks()

    async def test_get_tasks_missing_result_list(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test missing result list raises HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": {"bad": "data"}},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="'result' must be a list"):
            await client.get_tasks()

    async def test_get_tasks_rejects_non_object_items(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test malformed task items raise HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": [{"id": 1}, "bad-item"]},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="items must be JSON objects"):
            await client.get_tasks()


class TestGetUsers:
    """Tests for HostawayApiClient.get_users()."""

    async def test_get_users_success(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test get_users() calls GET /v1/users and returns the result list."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/users").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": [
                        {"id": 1, "name": "Alice"},
                        {"id": 2, "name": "Bob"},
                    ],
                },
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        result = await client.get_users()

        assert route.called
        assert result == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    async def test_get_users_api_error(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test non-success status raises HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/users").mock(
            return_value=httpx.Response(
                200,
                json={"status": "fail", "result": "error"},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="API error"):
            await client.get_users()

    async def test_get_users_missing_result_list(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test missing result list raises HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/users").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": {"bad": "data"}},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="'result' must be a list"):
            await client.get_users()

    async def test_get_users_rejects_non_object_items(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Test malformed user items raise HostawayResponseError."""
        respx.get(f"{FAKE_BASE_URL}/v1/users").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "result": [{"id": 1}, "bad-item"]},
            )
        )

        tm = _make_mock_token_manager()
        client = HostawayApiClient(tm, mock_httpx_client, base_url=FAKE_BASE_URL)

        with pytest.raises(HostawayResponseError, match="items must be JSON objects"):
            await client.get_users()
