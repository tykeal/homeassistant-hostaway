# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Hostaway API requests."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any

import httpx

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
    HostawayResponseError,
)
from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)

_LOGGER = logging.getLogger(__name__)

_MAX_RESPONSE_BODY_LOG = 500

_REDACTED = "<redacted>"

_SENSITIVE_KEY_TOKENS = (
    "doorcode",
    "password",
    "secret",
    "token",
    "apikey",
    "authorization",
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_SENSITIVE_KEY_PATTERN = "|".join(_SENSITIVE_KEY_TOKENS)

# Match key/value pairs in plain text (JSON-ish or form-encoded) where the
# key name contains a sensitive token. Captures the key + separator so we
# can preserve them while replacing the value with <redacted>. Designed to
# stop at the next delimiter (comma, ampersand, whitespace, brace, bracket,
# or matching quote) so a single sensitive value doesn't swallow the rest
# of the body.
_TEXT_REDACT_RE = re.compile(
    r"(?ix)"
    rf"(\"?[A-Za-z0-9_\-]*(?:{_SENSITIVE_KEY_PATTERN})[A-Za-z0-9_\-]*\"?"
    r"\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^,&\s}\]]+)"
)

# Match bearer-style auth tokens that may appear in error bodies even
# without a surrounding key/value structure.
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+\S+")


def _is_sensitive_key(key: str) -> bool:
    """Return True if ``key`` names a field that may carry secrets."""
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def _redact_sensitive(value: Any) -> Any:
    """Recursively redact values for sensitive keys in JSON-like data.

    Dict keys matching the sensitive-token list have their value
    replaced with ``"<redacted>"``. String values are additionally
    scrubbed with :func:`_redact_plain_text` so embedded
    ``key=value`` fragments or ``Bearer <token>`` substrings inside
    otherwise-innocuous fields are still redacted.
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(str(k)) else _redact_sensitive(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_plain_text(value)
    return value


def _redact_plain_text(text: str) -> str:
    """Apply pattern-based redaction to non-JSON bodies.

    Bearer-token fragments are redacted first so an unquoted
    ``Authorization: Bearer <token>`` value is fully scrubbed before
    the key/value pass — otherwise the key/value regex would only
    consume the literal word ``Bearer`` and leave the secret behind.
    """
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
    return _TEXT_REDACT_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)


def _sanitize_for_log(text: str) -> str:
    """Escape CR/LF and strip other control chars to prevent log injection."""
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return _CONTROL_CHAR_RE.sub("", text)


def _safe_response_body(
    response: httpx.Response,
    max_len: int = _MAX_RESPONSE_BODY_LOG,
) -> str:
    """Return response body text, sanitized, redacted, and safe to log.

    Reads ``response.text`` defensively; if reading fails for any
    reason, returns ``"<unavailable>"`` so callers can safely embed
    the result in log messages or exception strings without risking
    a secondary failure in the request path.

    When the body parses as JSON, sensitive fields (door codes,
    passwords, tokens, secrets, API keys, authorization headers)
    are replaced with ``"<redacted>"`` before serialization. When
    the body is not JSON, regex-based redaction is applied so the
    same keys are redacted in plain-text or form-encoded payloads,
    along with any ``Bearer <token>`` fragments. The entire parse/
    redact/serialize stage is wrapped in a defensive ``try`` so a
    secondary failure (e.g. ``RecursionError`` on pathological
    input) cannot escape into the 403 logging or error path. CR/LF
    and other ASCII control characters are escaped or stripped to
    prevent log-forging via attacker-controlled response content.

    Args:
        response: The httpx response to read.
        max_len: Maximum body length to return. Longer bodies are
            truncated and suffixed with ``"..."``.

    Returns:
        The sanitized, redacted, and (possibly truncated) response
        body, or ``"<unavailable>"`` if the body could not be read.
    """
    try:
        body = response.text
    except Exception:
        return "<unavailable>"
    try:
        try:
            parsed = json.loads(body)
        except ValueError:
            redacted = _redact_plain_text(body)
        else:
            redacted = json.dumps(_redact_sensitive(parsed))
        sanitized = _sanitize_for_log(redacted)
    except Exception:
        return "<unavailable>"
    if len(sanitized) > max_len:
        return sanitized[:max_len] + "..."
    return sanitized


class HostawayApiClient:
    """HTTP client for authenticated Hostaway API requests.

    Wraps httpx.AsyncClient (injected) with automatic token
    management, exponential backoff on 429/5xx responses, and
    reactive token refresh on 403 responses.

    Attributes:
        _token_manager: Token provider for authentication.
        _http: Injected async HTTP client.
        _base_url: Hostaway API base URL.
    """

    def __init__(
        self,
        token_manager: HostawayTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize HostawayApiClient.

        Args:
            token_manager: Token manager for authentication.
            http_client: Async HTTP client for API requests.
            base_url: Hostaway API base URL.
        """
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    async def test_connection(self) -> bool:
        """Validate credentials and API access.

        Acquires a token and makes a lightweight API call to verify
        the credentials work end-to-end.

        Returns:
            True if connection test succeeds.

        Raises:
            HostawayAuthError: If credentials are invalid.
            HostawayConnectionError: If the API is unreachable.
            HostawayRateLimitError: If rate limited.
            HostawayResponseError: If the API returns unexpected data.
        """
        await self._request(
            "GET",
            "/v1/listings",
            params={"limit": 1},
        )
        return True

    async def get_listings_page(
        self,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[HostawayListing]:
        """Fetch a single page of listings.

        Args:
            offset: Pagination offset.
            limit: Maximum items per page.

        Returns:
            List of HostawayListing objects for this page.

        Raises:
            HostawayResponseError: On unexpected response format.
            HostawayAuthError: On authentication failure.
            HostawayConnectionError: On network failure.
            HostawayRateLimitError: On rate limit exhaustion.
        """
        response = await self._request(
            "GET",
            "/v1/listings",
            params={"offset": offset, "limit": limit},
        )
        data = self._parse_response(response)
        results = self._extract_results(data)
        return [HostawayListing.from_api_response(item) for item in results]

    async def get_all_listings(self) -> list[HostawayListing]:
        """Fetch all listings with automatic offset pagination.

        Iterates through all pages until fewer results than the
        page size are returned.

        Returns:
            Complete list of HostawayListing objects.

        Raises:
            HostawayResponseError: On unexpected response format.
            HostawayAuthError: On authentication failure.
            HostawayConnectionError: On network failure.
            HostawayRateLimitError: On rate limit exhaustion.
        """
        all_listings: list[HostawayListing] = []
        offset = 0

        while True:
            page = await self.get_listings_page(offset=offset, limit=DEFAULT_PAGE_LIMIT)
            all_listings.extend(page)

            if len(page) < DEFAULT_PAGE_LIMIT:
                break

            offset += DEFAULT_PAGE_LIMIT

        return all_listings

    async def get_reservations_page(
        self,
        listing_id: int,
        after_id: int | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[HostawayReservation]:
        """Fetch a single page of reservations for a listing.

        Args:
            listing_id: The listing ID to fetch reservations for.
            after_id: Cursor for pagination (last item's id).
            limit: Maximum items per page.

        Returns:
            List of HostawayReservation objects for this page.

        Raises:
            HostawayResponseError: On unexpected response format.
            HostawayAuthError: On authentication failure.
            HostawayConnectionError: On network failure.
            HostawayRateLimitError: On rate limit exhaustion.
        """
        params: dict[str, int] = {
            "listingId": listing_id,
            "limit": limit,
        }
        if after_id is not None:
            params["afterId"] = after_id

        response = await self._request(
            "GET",
            "/v1/reservations",
            params=params,
        )
        data = self._parse_response(response)
        results = self._extract_results(data)
        return [HostawayReservation.from_api_response(item) for item in results]

    async def get_all_reservations(self, listing_id: int) -> list[HostawayReservation]:
        """Fetch all reservations for a listing with cursor pagination.

        Uses afterId cursor (last item's id) to paginate through
        all results.

        Args:
            listing_id: The listing ID to fetch reservations for.

        Returns:
            Complete list of HostawayReservation objects.

        Raises:
            HostawayResponseError: On unexpected response format.
            HostawayAuthError: On authentication failure.
            HostawayConnectionError: On network failure.
            HostawayRateLimitError: On rate limit exhaustion.
        """
        all_reservations: list[HostawayReservation] = []
        after_id: int | None = None

        while True:
            page = await self.get_reservations_page(
                listing_id, after_id=after_id, limit=DEFAULT_PAGE_LIMIT
            )
            all_reservations.extend(page)

            if len(page) < DEFAULT_PAGE_LIMIT:
                break

            after_id = page[-1].id

        return all_reservations

    async def update_reservation(
        self, reservation_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a reservation via PUT.

        Args:
            reservation_id: The reservation ID to update.
            data: JSON body with fields to update.

        Returns:
            Updated reservation data from the API response.

        Raises:
            HostawayResponseError: On unexpected response format.
            HostawayAuthError: On authentication failure.
            HostawayConnectionError: On network failure.
            HostawayRateLimitError: On rate limit exhaustion.
        """
        response = await self._request(
            "PUT",
            f"/v1/reservations/{reservation_id}",
            json=data,
        )
        parsed = self._parse_response(response)
        status = parsed.get("status")
        if status is not None and status != "success":
            raise HostawayResponseError(
                f"Update failed: {parsed.get('result', status)}",
            )
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise HostawayResponseError(
                "Update response missing 'result' object",
            )
        return result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        _retried_auth: bool = False,
    ) -> httpx.Response:
        """Make an authenticated API request with retry logic.

        Adds Authorization header, retries on 429 and transient
        failures with exponential backoff, and reactively refreshes
        token on 403.

        Args:
            method: HTTP method (GET, PUT, etc.).
            path: API path relative to base URL.
            params: Optional query parameters.
            json: Optional JSON body.
            _retried_auth: Internal flag to prevent infinite 403 loop.

        Returns:
            The httpx.Response from the API.

        Raises:
            HostawayAuthError: On persistent auth failure.
            HostawayConnectionError: On network failures after retries.
            HostawayRateLimitError: On 429 after max retries.
            HostawayResponseError: On 404 or other client errors.
        """
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
                        f"Failed to connect to Hostaway API "
                        f"after {MAX_RETRIES} retries: {exc}",
                    ) from exc

                delay = _jittered_delay(backoff)
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
                if _retried_auth:
                    body = _safe_response_body(response)
                    raise HostawayAuthError(
                        f"Forbidden after token refresh: "
                        f"{method} {path} returned 403; body: {body}",
                    )
                body = _safe_response_body(response)
                _LOGGER.warning(
                    "Hostaway returned 403 for %s %s; refreshing token "
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
                    json=json,
                    _retried_auth=True,
                )

            if response.status_code == 404:
                raise HostawayResponseError(
                    f"Resource not found: {path}",
                )

            if response.status_code == 429:
                if attempt >= MAX_RETRIES:
                    retry_after = _parse_retry_after(response)
                    raise HostawayRateLimitError(
                        "Rate limit exceeded after max retries",
                        retry_after=retry_after,
                    )

                delay = _calculate_backoff(backoff, response)
                _LOGGER.warning(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue

            if _is_server_error(response.status_code):
                if attempt >= MAX_RETRIES:
                    raise HostawayConnectionError(
                        f"Server error {response.status_code} "
                        f"after {MAX_RETRIES} retries",
                    )

                delay = _jittered_delay(backoff)
                _LOGGER.warning(
                    "Server error %d, retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue

            # Raise on any other non-success status
            if not response.is_success:
                raise HostawayResponseError(
                    f"Unexpected response status: {response.status_code}",
                )

            return response

        raise HostawayResponseError(  # pragma: no cover
            "Request loop exited without returning",
        )

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse JSON response body.

        Args:
            response: The HTTP response to parse.

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            HostawayResponseError: If response is not valid JSON or
                not a dict.
        """
        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise HostawayResponseError(
                "Response is not valid JSON",
            ) from exc

        if not isinstance(data, dict):
            raise HostawayResponseError(
                "Response must be a JSON object",
            )
        return data

    def _extract_results(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the result list from a Hostaway API response.

        Args:
            data: Parsed response dictionary.

        Returns:
            The result list from the response wrapper.

        Raises:
            HostawayResponseError: If status is not success, result
                field is missing, or not a list of dicts.
        """
        status = data.get("status")
        if status is not None and status != "success":
            raise HostawayResponseError(
                f"API error: {data.get('result', status)}",
            )
        results = data.get("result")
        if results is None:
            raise HostawayResponseError("Response missing 'result' field")
        if not isinstance(results, list):
            raise HostawayResponseError("'result' must be a list")
        for item in results:
            if not isinstance(item, dict):
                raise HostawayResponseError(
                    "'result' items must be JSON objects",
                )
        return results


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After header from a 429 response.

    Args:
        response: The HTTP response to parse.

    Returns:
        The retry-after value in seconds, or None if not present.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _calculate_backoff(
    base_backoff: float,
    response: httpx.Response,
) -> float:
    """Calculate backoff delay with Retry-After support.

    Args:
        base_backoff: Current backoff delay in seconds.
        response: The HTTP response (may contain Retry-After).

    Returns:
        Delay in seconds before next retry.
    """
    retry_after = _parse_retry_after(response)
    if retry_after is not None:
        return max(0.1, min(retry_after, MAX_BACKOFF))
    return _jittered_delay(base_backoff)


def _jittered_delay(base_backoff: float) -> float:
    """Apply ±25% jitter to a base backoff delay.

    Args:
        base_backoff: Current backoff delay in seconds.

    Returns:
        Delay in seconds with jitter applied, minimum 0.1s.
    """
    delay = min(base_backoff, MAX_BACKOFF)
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


def _is_server_error(status_code: int) -> bool:
    """Check if status code is a 5xx server error.

    Args:
        status_code: HTTP status code.

    Returns:
        True if status code is 500-599.
    """
    return 500 <= status_code < 600
