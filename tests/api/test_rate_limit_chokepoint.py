# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the two rate-limit enforcement points (T016-T021).

Everything the integration sends leaves through one of exactly two calls:
``HostawayApiClient._request`` and ``HostawayTokenManager._request_token``.
These tests exist to prove that a new API method cannot accidentally escape
pacing, and that a retry is paced as the separate request the server sees
it as.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx

from custom_components.hostaway.api import retry as _retry
from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.const import (
    BACKOFF_MULTIPLIER,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    MAX_RETRIES,
)
from custom_components.hostaway.api.exceptions import (
    HostawayRateLimitError,
    HostawayRateLimitShedError,
    HostawayRateLimitWaitTimeout,
)
from custom_components.hostaway.api.rate_limit import (
    SCHEDULED_POLICY,
    AccountRateLimiter,
    GateScope,
    ProviderSuppression,
    RequestContext,
    RequestPriority,
    SlidingWindowGate,
    current_request_context,
    request_context,
    sleep_within_deadline,
    start_interactive_context,
)
from tests.helpers import FAKE_BASE_URL, FAKE_TOKEN

#: A suppression short enough that a test can simply wait it out. The
#: limiter converts Hostaway's absolute timestamp into a delay, so this is
#: expressed the way the server expresses it.
_BRIEF_SUPPRESSION = 0.01


def make_limiter(budget: int = 100) -> AccountRateLimiter:
    """Build an isolated limiter with room to spare.

    Shared process-wide state is deliberately not used: a test that paced
    itself against another test's IP gate would be unreproducible.

    Args:
        budget: Admissions permitted per window on both general counters.

    Returns:
        A limiter with no startup hold.
    """
    return AccountRateLimiter(
        "client-id",
        account_budget=budget,
        ip_gate=SlidingWindowGate(
            "ip", GateScope.IP, budget=budget, startup_hold=False
        ),
        provider_suppression=ProviderSuppression(),
        startup_hold=False,
    )


def unlimited_client(http: httpx.AsyncClient) -> HostawayApiClient:
    """Build a client with no limiter attached.

    Args:
        http: The transport to use.

    Returns:
        An unpaced client.
    """
    return HostawayApiClient(_token_manager(), http, base_url=FAKE_BASE_URL)


def limited_client(
    http: httpx.AsyncClient, limiter: AccountRateLimiter
) -> HostawayApiClient:
    """Build a client that paces itself.

    Args:
        http: The transport to use.
        limiter: The limiter to pace against.

    Returns:
        A paced client.
    """
    return HostawayApiClient(
        _token_manager(), http, base_url=FAKE_BASE_URL, limiter=limiter
    )


def _token_manager() -> Mock:
    """Return a token manager stub that never makes a request.

    Returns:
        A mock handing out a fixed token.
    """
    manager = Mock(spec=HostawayTokenManager)
    manager.get_token = AsyncMock(return_value=FAKE_TOKEN)
    manager.invalidate = Mock()
    return manager


def _refused(**extra: str) -> httpx.Response:
    """Build a 429 carrying a Hostaway retry timestamp.

    The timestamp is absolute, so it has to be stamped when the response is
    served rather than when the test is written: a stale one reads as
    corrupt and earns the ten-second default suppression instead.

    Args:
        **extra: Additional rate-limit headers to include.

    Returns:
        A refused response the limiter can learn from.
    """
    headers = {
        _retry.RETRY_AFTER_HEADER: str(time.time() + _BRIEF_SUPPRESSION),
        **extra,
    }
    return httpx.Response(429, headers=headers)


def _refuse_then_succeed(refusals: int, **extra: str) -> Any:
    """Return a respx side effect that refuses, then succeeds.

    Args:
        refusals: How many 429s to serve before the 200.
        **extra: Additional rate-limit headers on each 429.

    Returns:
        A callable respx can use as a side effect.
    """
    served = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        """Serve the next scripted response.

        Args:
            _request: The outgoing request, unused.

        Returns:
            A 429 while refusals remain, then a 200.
        """
        nonlocal served
        served += 1
        return _refused(**extra) if served <= refusals else _ok()

    return respond


def _token_response() -> httpx.Response:
    """Build a successful token response.

    Returns:
        A 200 carrying a usable token.
    """
    return httpx.Response(
        200,
        json={
            "status": "success",
            "result": {
                "access_token": "token-value",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        },
    )


def _ok() -> httpx.Response:
    """Build an empty successful list response.

    Returns:
        A 200 response.
    """
    return httpx.Response(200, json={"status": "success", "result": []})


class TestEveryAttemptIsPaced:
    """T016: the chokepoint sits inside the retry loop, not around it."""

    async def test_each_retry_attempt_is_admitted_separately(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """SC-010: the server counts four requests, so we must pace four."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = _refuse_then_succeed(3)
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        assert limiter.snapshot().admitted_total == 4
        limiter.close()

    async def test_a_brand_new_client_method_is_paced_for_free(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """SC-009: pacing is a property of the chokepoint, not the method."""
        respx.get(f"{FAKE_BASE_URL}/v1/somethingNobodyHasWrittenYet").mock(
            return_value=_ok()
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        async def brand_new_endpoint() -> httpx.Response:
            """Call an endpoint with no limiter code of its own.

            Returns:
                The response.
            """
            return await client._request("GET", "/v1/somethingNobodyHasWrittenYet")

        with request_context(start_interactive_context()):
            await brand_new_endpoint()

        assert limiter.snapshot().admitted_total == 1
        limiter.close()

    async def test_a_connection_error_retry_is_admitted_again(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A dropped connection may still have reached the server.

        Args:
            mock_httpx_client: The transport fixture.
        """
        served = 0

        def respond(request: httpx.Request) -> httpx.Response:
            """Fail the connection once, then succeed.

            Args:
                request: The outgoing request.

            Returns:
                The successful response, once the failure is spent.

            Raises:
                ConnectError: On the first attempt.
            """
            nonlocal served
            served += 1
            if served == 1:
                raise httpx.ConnectError("boom", request=request)
            return _ok()

        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(side_effect=respond)
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client._request("GET", "/v1/listings")

        assert limiter.snapshot().admitted_total == 2
        limiter.close()

    async def test_a_server_error_retry_is_admitted_again(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A 5xx is a request the server counted all the same.

        Args:
            mock_httpx_client: The transport fixture.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=[httpx.Response(503), _ok()]
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client._request("GET", "/v1/listings")

        assert limiter.snapshot().admitted_total == 2
        limiter.close()

    async def test_the_403_refresh_re_entry_is_admitted_again(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """The recursive retry after a token refresh is a second request.

        Args:
            mock_httpx_client: The transport fixture.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=[httpx.Response(403), _ok()]
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with request_context(start_interactive_context()):
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        assert limiter.snapshot().admitted_total == 2
        limiter.close()

    async def test_one_operation_keeps_one_deadline_without_a_context(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A synthesised deadline must not be renewed for every attempt.

        A caller that establishes no context gets a conservative one, but
        it has to be *one*: re-synthesising per acquisition would let a
        two-second operation run indefinitely, two seconds at a time.

        Args:
            mock_httpx_client: The transport fixture.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(1)
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)
        deadlines: list[float] = []
        admit = limiter.acquire

        async def spy(method: str, path: str) -> None:
            """Record the ambient deadline, then admit normally.

            Args:
                method: HTTP method.
                path: Request path.
            """
            deadlines.append(current_request_context().deadline)
            await admit(method, path)

        with (
            patch.object(limiter, "acquire", spy),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client._request("GET", "/v1/listings")

        assert len(deadlines) == 2
        assert deadlines[0] == deadlines[1]
        limiter.close()

    async def test_an_unlimited_client_still_works(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Every existing caller constructs the client without a limiter."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
        client = unlimited_client(mock_httpx_client)

        assert (await client._request("GET", "/v1/listings")).status_code == 200


class TestRetrySleepsRespectTheDeadline:
    """T017: a backoff must not outlive the operation it belongs to."""

    async def test_a_sheddable_cycle_sheds_rather_than_oversleeping(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """FR-020: the next acquisition could not have succeeded anyway."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(99)
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)
        slept: list[float] = []

        async def record(delay: float) -> None:
            """Record a sleep instead of performing it.

            Args:
                delay: Seconds the caller asked to sleep.
            """
            slept.append(delay)

        # A cycle with almost no time left: the first backoff is a second,
        # which it plainly cannot afford.
        expiring = RequestContext(
            priority=RequestPriority.SCHEDULED,
            policy=SCHEDULED_POLICY,
            deadline=time.monotonic() + 0.001,
        )
        with (
            request_context(expiring),
            patch("asyncio.sleep", record),
            pytest.raises(HostawayRateLimitShedError),
        ):
            await client._request("GET", "/v1/listings")

        assert slept == []
        limiter.close()

    async def test_an_unsheddable_cycle_times_out_instead(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """FR-021: the failure mode follows the policy, not the call site."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(99)
        )
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)
        interactive = start_interactive_context()
        expiring = RequestContext(
            priority=interactive.priority,
            policy=interactive.policy,
            deadline=time.monotonic() + 0.001,
        )

        with (
            request_context(expiring),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(HostawayRateLimitWaitTimeout),
        ):
            await client._request("GET", "/v1/listings")

        limiter.close()

    async def test_an_unlimited_client_keeps_its_old_backoff(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A caller that never opted into pacing cannot start shedding."""
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = [httpx.Response(429), _ok()]
        client = unlimited_client(mock_httpx_client)

        with patch("asyncio.sleep", new_callable=AsyncMock) as slept:
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        slept.assert_called()


class TestHostawayRetryHeaders:
    """T018: Hostaway sends a timestamp where HTTP sends a duration."""

    def test_a_timestamp_becomes_a_delay(self) -> None:
        """Reading the timestamp as seconds would sleep for decades."""
        response = httpx.Response(
            429, headers={_retry.RETRY_AFTER_HEADER: str(time.time() + 5.0)}
        )
        delay = _retry._parse_retry_after(response)

        assert delay is not None
        assert 4.0 < delay <= 5.0

    def test_a_past_timestamp_falls_back_to_our_own_backoff(self) -> None:
        """Retrying instantly on a stale header would hammer the server."""
        response = httpx.Response(
            429, headers={_retry.RETRY_AFTER_HEADER: str(time.time() - 60.0)}
        )

        assert _retry._parse_retry_after(response) is None

    @pytest.mark.parametrize("value", ["not-a-number", "nan", "inf", ""])
    def test_an_unusable_timestamp_is_ignored(self, value: str) -> None:
        """A corrupt header must not wedge or crash the retry path.

        Args:
            value: The header value under test.
        """
        response = httpx.Response(429, headers={_retry.RETRY_AFTER_HEADER: value})

        assert _retry._parse_retry_after(response) is None

    def test_the_standard_retry_after_header_is_not_read(self) -> None:
        """Hostaway does not send it, and reading it would mean seconds."""
        response = httpx.Response(429, headers={"Retry-After": "120"})

        assert _retry._parse_retry_after(response) is None

    def test_counter_headers_are_read(self) -> None:
        """The limiter needs the server's own view to converge on it."""
        response = httpx.Response(
            429,
            headers={
                _retry.LIMIT_HEADER: "200",
                _retry.REMAINING_HEADER: "0",
                _retry.APPLIED_HEADER: "account",
            },
        )
        headers = _retry.parse_rate_limit_headers(response)

        assert headers.limit == 200
        assert headers.remaining == 0
        assert headers.applied == "account"
        assert headers.retry_at is None

    @pytest.mark.parametrize(
        "value", ["1.9", "0.9", "-1", "-0.5", "nan", "inf", "1e3", "", " ", "abc"]
    )
    def test_a_counter_header_must_be_a_whole_number(self, value: str) -> None:
        """Truncating a malformed ceiling would quietly starve the budget.

        Args:
            value: The header value under test.
        """
        response = httpx.Response(429, headers={_retry.LIMIT_HEADER: value})

        assert _retry.parse_rate_limit_headers(response).limit is None

    def test_a_padded_counter_header_is_still_read(self) -> None:
        """Surrounding whitespace is a transport artefact, not corruption."""
        response = httpx.Response(429, headers={_retry.REMAINING_HEADER: " 12 "})

        assert _retry.parse_rate_limit_headers(response).remaining == 12

    def test_missing_counter_headers_are_absent_not_zero(self) -> None:
        """Zero remaining and unknown remaining are very different facts."""
        headers = _retry.parse_rate_limit_headers(httpx.Response(429))

        assert headers.limit is None
        assert headers.remaining is None
        assert headers.applied is None

    def test_the_retry_policy_itself_is_unchanged(self) -> None:
        """FR-013: pacing must not quietly re-tune the retry behaviour."""
        assert MAX_RETRIES == 3
        assert INITIAL_BACKOFF == 1.0
        assert BACKOFF_MULTIPLIER == 2.0
        assert MAX_BACKOFF == 30.0

    def test_the_backoff_curve_is_unchanged(self) -> None:
        """The jitter band is still a quarter either side, floored at 0.1."""
        for base in (1.0, 2.0, 4.0, 8.0, 60.0):
            expected = min(base, MAX_BACKOFF)
            for _ in range(50):
                delay = _retry._jittered_delay(base)
                assert delay >= 0.1
                assert expected * 0.75 - 1e-9 <= delay <= expected * 1.25 + 1e-9

    async def test_an_observed_limit_never_raises_the_budget(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """FR-015: the server's ceiling is not an invitation to spend more."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(
                99,
                **{
                    _retry.APPLIED_HEADER: "account",
                    _retry.LIMIT_HEADER: "200",
                    _retry.REMAINING_HEADER: "199",
                },
            )
        )
        limiter = make_limiter(budget=50)
        client = limited_client(mock_httpx_client, limiter)

        # A single refused attempt: a "remaining: 0" reconciliation fills
        # the window by design, so retrying here would simply wait out the
        # window and prove nothing about the budget.
        with (
            request_context(start_interactive_context()),
            pytest.raises(HostawayRateLimitError),
        ):
            await client._request("GET", "/v1/listings", retry_ambiguous=False)

        gates = limiter.snapshot().gates
        assert gates["account"].effective_budget == 50
        # Only the counter the server named was touched.
        assert gates["ip"].effective_budget == 50
        assert limiter.snapshot().rate_limited_by_counter["account"] >= 1
        limiter.close()

    async def test_an_observed_remaining_lowers_only_that_counter(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """FR-014: converge downward on the counter the server charged."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(
                99,
                **{
                    _retry.APPLIED_HEADER: "account",
                    _retry.LIMIT_HEADER: "20",
                    _retry.REMAINING_HEADER: "0",
                },
            )
        )
        limiter = make_limiter(budget=50)
        client = limited_client(mock_httpx_client, limiter)

        # A single refused attempt: a "remaining: 0" reconciliation fills
        # the window by design, so retrying here would simply wait out the
        # window and prove nothing about the budget.
        with (
            request_context(start_interactive_context()),
            pytest.raises(HostawayRateLimitError),
        ):
            await client._request("GET", "/v1/listings", retry_ambiguous=False)

        gates = limiter.snapshot().gates
        assert gates["account"].effective_budget == 20
        assert gates["ip"].effective_budget == 50
        limiter.close()


class TestTokenEndpointIsPaced:
    """T019: the second chokepoint, and the assumption behind it."""

    async def test_a_token_request_is_admitted(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """OQ-001: assumed to be charged, because guessing low is cheaper."""
        respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "access_token": "token-value",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                },
            )
        )
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )

        priorities: list[RequestPriority] = []
        admit = limiter.acquire

        async def spy(method: str, path: str) -> None:
            """Record the priority the token request was admitted at.

            Args:
                method: HTTP method.
                path: Request path.
            """
            priorities.append(current_request_context().priority)
            await admit(method, path)

        with (
            request_context(start_interactive_context()),
            patch.object(limiter, "acquire", spy),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager.get_token()

        assert limiter.snapshot().admitted_total == 1
        # T019: the token request belongs to the operation that needed it,
        # so it must not quietly queue behind scheduled work.
        assert priorities == [RequestPriority.INTERACTIVE]
        limiter.close()

    async def test_a_token_refresh_and_its_data_request_are_both_paced(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Two requests leave the process, so two admissions are charged.

        The token is fetched before the data request acquires, so these are
        sequential rather than nested; nesting is covered separately.
        """
        respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "access_token": "token-value",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                },
            )
        )
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
        limiter = make_limiter(budget=2)
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )
        client = HostawayApiClient(
            manager, mock_httpx_client, base_url=FAKE_BASE_URL, limiter=limiter
        )

        with (
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await client._request("GET", "/v1/listings")

        assert response.status_code == 200
        # One for the token, one for the data request.
        assert limiter.snapshot().admitted_total == 2
        limiter.close()

    async def test_a_refused_token_request_feeds_the_limiter(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A 429 here is budget news like any other."""
        respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            side_effect=_refuse_then_succeed(99, **{_retry.APPLIED_HEADER: "account"})
        )
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )

        with (
            request_context(start_interactive_context()),
            pytest.raises(HostawayRateLimitError),
        ):
            await manager.get_token()

        assert limiter.snapshot().rate_limited_total == 1
        limiter.close()

    async def test_a_nested_acquisition_cannot_deadlock(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Admission is a rate reservation, not a lock that is held.

        Args:
            mock_httpx_client: The transport fixture, unused here.
        """
        limiter = make_limiter(budget=2)

        with request_context(start_interactive_context()):
            await limiter.acquire("POST", "/v1/accessTokens")
            await limiter.acquire("GET", "/v1/listings")

        assert limiter.snapshot().admitted_total == 2
        limiter.close()

    async def test_waiting_for_the_refresh_lock_respects_the_deadline(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """Queueing behind another refresh is still queueing.

        Args:
            mock_httpx_client: The transport fixture.
        """
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )
        await manager._lock.acquire()
        nearly_spent = RequestContext.start(
            RequestPriority.SCHEDULED,
            SCHEDULED_POLICY,
            time.monotonic() - (SCHEDULED_POLICY.duration - 0.05),
        )

        try:
            with (
                request_context(nearly_spent),
                pytest.raises(HostawayRateLimitShedError),
            ):
                await manager.get_token()
        finally:
            manager._lock.release()

        limiter.close()

    async def test_an_already_spent_deadline_never_waits_for_the_lock(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """There is nothing left to spend, so there is nothing to wait for.

        Args:
            mock_httpx_client: The transport fixture.
        """
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )
        spent = RequestContext.start(
            RequestPriority.SCHEDULED,
            SCHEDULED_POLICY,
            time.monotonic() - 10.0,
        )

        with request_context(spent), pytest.raises(HostawayRateLimitShedError):
            await manager.get_token()

        assert not manager._lock.locked()
        limiter.close()

    async def test_a_token_survives_a_deadline_lost_to_its_readiness_wait(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """A token is paid for in budget the moment it is issued.

        Discarding one because this caller ran out of time would buy
        another next cycle and run out of time again, forever.

        Args:
            mock_httpx_client: The transport fixture.
        """
        route = respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            return_value=_token_response()
        )
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )
        nearly_spent = RequestContext.start(
            RequestPriority.SCHEDULED,
            SCHEDULED_POLICY,
            time.monotonic() - (SCHEDULED_POLICY.duration - 0.5),
        )

        with request_context(nearly_spent), pytest.raises(HostawayRateLimitShedError):
            await manager.get_token()

        assert manager._cached_token is not None

        with (
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            assert await manager.get_token() == "token-value"

        assert route.call_count == 1
        limiter.close()

    def test_the_oq_001_assumption_is_written_down(self) -> None:
        """An undocumented guess that is not labelled becomes folklore."""
        source = Path("custom_components/hostaway/api/auth.py").read_text()

        assert source.count("OQ-001") >= 2
        assert "not** documented Hostaway behaviour" in source


class TestChokepointObservability:
    """T020: tell an operator which side of the limit refused them."""

    async def test_server_pushback_is_distinguishable_from_our_own_pacing(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """FR-028: "they refused us" and "we refused us" are not the same.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        route = respx.get(f"{FAKE_BASE_URL}/v1/listings")
        route.side_effect = _refuse_then_succeed(1, **{_retry.APPLIED_HEADER: "ip"})
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            caplog.at_level(logging.WARNING),
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client._request("GET", "/v1/listings")

        pushback = [r for r in caplog.records if "server pushback" in r.message]
        assert len(pushback) == 1
        assert "ip" in pushback[0].getMessage()
        shed = [r for r in caplog.records if "Skipped a scheduled" in r.getMessage()]
        assert shed == []
        limiter.close()

    async def test_an_unlimited_client_does_not_claim_to_pace_itself(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """There is no pacing to contrast the refusal with.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
            side_effect=_refuse_then_succeed(1)
        )
        client = unlimited_client(mock_httpx_client)

        with (
            caplog.at_level(
                logging.WARNING, logger="custom_components.hostaway.api.client"
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client._request("GET", "/v1/listings")

        refusals = [r for r in caplog.records if "HTTP 429" in r.getMessage()]
        assert len(refusals) == 1
        assert "own pacing" not in refusals[0].getMessage()

    async def test_a_below_budget_request_is_quiet(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """FR-027: pacing is only interesting when it does something.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            caplog.at_level(logging.INFO, logger="custom_components.hostaway"),
            request_context(start_interactive_context()),
        ):
            await client._request("GET", "/v1/listings")

        ours = [r for r in caplog.records if r.name.startswith("custom_components")]
        assert ours == []
        limiter.close()

    async def test_admission_is_visible_at_debug(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An operator chasing latency needs to see the queueing.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        with (
            caplog.at_level(
                logging.DEBUG, logger="custom_components.hostaway.api.client"
            ),
            request_context(start_interactive_context()),
        ):
            await client._request("GET", "/v1/listings")

        admitted = [r for r in caplog.records if "Admitted" in r.getMessage()]
        assert len(admitted) == 1
        # FR-028: priority, how long the caller waited, and what is left.
        message = admitted[0].getMessage()
        assert "interactive" in message
        assert "after 0." in message
        assert "account 99/100" in message
        assert "ip 99/100" in message
        limiter.close()

    async def test_a_token_admission_is_visible_at_debug(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Both chokepoints are paced, so both have to be observable.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            return_value=_token_response()
        )
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )

        with (
            caplog.at_level(
                logging.DEBUG, logger="custom_components.hostaway.api.auth"
            ),
            request_context(start_interactive_context()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager.get_token()

        admitted = [r for r in caplog.records if "Admitted" in r.getMessage()]
        assert len(admitted) == 1
        message = admitted[0].getMessage()
        assert "/v1/accessTokens" in message
        assert "interactive" in message
        assert "account 99/100" in message
        limiter.close()

    async def test_a_refused_token_request_says_who_refused_it(
        self,
        mock_httpx_client: httpx.AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An operator must be able to tell pushback from our own shedding.

        Args:
            mock_httpx_client: The transport fixture.
            caplog: Captured log records.
        """
        respx.post("https://api.hostaway.com/v1/accessTokens").mock(
            side_effect=_refuse_then_succeed(99, **{_retry.APPLIED_HEADER: "account"})
        )
        limiter = make_limiter()
        manager = HostawayTokenManager(
            "client-id", "secret", mock_httpx_client, limiter=limiter
        )

        with (
            caplog.at_level(
                logging.WARNING, logger="custom_components.hostaway.api.auth"
            ),
            request_context(start_interactive_context()),
            pytest.raises(HostawayRateLimitError),
        ):
            await manager.get_token()

        refusals = [r for r in caplog.records if "HTTP 429" in r.getMessage()]
        assert len(refusals) == 1
        assert "account" in refusals[0].getMessage()
        limiter.close()


class TestTheSharedTransportIsUntouched:
    """T021: the httpx client belongs to Home Assistant, not to us."""

    async def test_the_injected_client_is_used_as_given(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """FR-003: wrapping a shared client would affect every integration."""
        respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
        before: dict[str, Any] = {
            "transport": mock_httpx_client._transport,
            "event_hooks": dict(mock_httpx_client.event_hooks),
            "timeout": mock_httpx_client.timeout,
            "headers": dict(mock_httpx_client.headers),
            "class": type(mock_httpx_client),
        }
        limiter = make_limiter()
        client = limited_client(mock_httpx_client, limiter)

        assert client._http is mock_httpx_client

        with request_context(start_interactive_context()):
            await client._request("GET", "/v1/listings")

        assert client._http is mock_httpx_client
        assert mock_httpx_client._transport is before["transport"]
        assert dict(mock_httpx_client.event_hooks) == before["event_hooks"]
        assert mock_httpx_client.timeout == before["timeout"]
        assert dict(mock_httpx_client.headers) == before["headers"]
        assert type(mock_httpx_client) is before["class"]
        limiter.close()

    async def test_the_limiter_holds_no_reference_to_the_transport(
        self, mock_httpx_client: httpx.AsyncClient
    ) -> None:
        """The limiter decides when to send, never how."""
        limiter = make_limiter()
        limited_client(mock_httpx_client, limiter)

        assert not any(
            isinstance(value, httpx.AsyncClient) for value in vars(limiter).values()
        )
        limiter.close()


async def test_a_shed_request_never_reaches_the_transport(
    mock_httpx_client: httpx.AsyncClient,
) -> None:
    """The whole point is that the request does not leave the process."""
    route = respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
    limiter = make_limiter(budget=1)
    client = limited_client(mock_httpx_client, limiter)

    # Spend the only slot, then queue a cycle with no time to wait.
    with request_context(start_interactive_context()):
        await client._request("GET", "/v1/listings")

    expiring = RequestContext(
        priority=RequestPriority.SCHEDULED,
        policy=SCHEDULED_POLICY,
        deadline=time.monotonic() - 1.0,
    )
    with request_context(expiring), pytest.raises(HostawayRateLimitShedError):
        await client._request("GET", "/v1/listings")

    assert len(route.calls) == 1
    limiter.close()


async def test_a_cancelled_caller_leaves_no_task_behind(
    mock_httpx_client: httpx.AsyncClient,
) -> None:
    """Giving up mid-queue must not strand the limiter's bookkeeping."""
    respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(return_value=_ok())
    limiter = make_limiter(budget=1)
    client = limited_client(mock_httpx_client, limiter)

    with request_context(start_interactive_context()):
        await client._request("GET", "/v1/listings")
        queued = asyncio.create_task(client._request("GET", "/v1/listings"))
        await asyncio.sleep(0)

        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued

    assert limiter.snapshot().admitted_total == 1
    limiter.close()


class TestADelayedEventLoopCannotOversleep:
    """A deadline that is only checked up front is not enforced."""

    async def test_a_sleep_that_overruns_is_cut_short(self) -> None:
        """Resuming after the deadline is as bad as never checking it."""
        ctx = RequestContext.start(
            RequestPriority.SCHEDULED,
            SCHEDULED_POLICY,
            time.monotonic() - (SCHEDULED_POLICY.duration - 0.1),
        )

        async def oversleep(_delay: float) -> None:
            """Sleep far longer than the caller asked for.

            Args:
                _delay: The requested delay, deliberately ignored.
            """
            await real_sleep(0.5)

        real_sleep = asyncio.sleep

        with (
            patch("asyncio.sleep", oversleep),
            pytest.raises(HostawayRateLimitShedError),
        ):
            await sleep_within_deadline(0.01, ctx)
