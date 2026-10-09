# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the per-account limiter registry (T022-T026, T042).

Hostaway counts requests per account and per IP, so the limiter has to
outlive any single config entry and be shared by all of them. These tests
pin down the three things that are easy to get wrong: where the registry
lives, what a reload is allowed to do to the budget, and which operations
count as interactive.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.const import (
    DEFAULT_RATE_LIMIT_BUDGET,
    RATE_LIMIT_WINDOW_SECONDS,
)
from custom_components.hostaway.api.rate_limit import (
    AccountRateLimiter,
    RequestPriority,
    current_request_context,
)
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RATE_LIMIT_BUDGET,
    CONF_SELECTED_LISTINGS,
    DATA_RATE_LIMITERS,
    DOMAIN,
)
from custom_components.hostaway.rate_limit_registry import (
    entry_budget,
    get_registry,
    release_entry,
    validation_limiter,
)
from tests.helpers import FAKE_BASE_URL, FAKE_TOKEN_URL, make_token_response

_ACCOUNT = "test-client-id"


def _make_entry(**overrides: Any) -> MockConfigEntry:
    """Create a config entry for the shared test account.

    Args:
        **overrides: Fields to override, including extra ``data``.

    Returns:
        A config entry ready to add to Home Assistant.
    """
    data: dict[str, Any] = {
        CONF_CLIENT_ID: _ACCOUNT,
        CONF_CLIENT_SECRET: "test-client-secret",
        CONF_SELECTED_LISTINGS: [12345],
    }
    data.update(cast(dict[str, Any], overrides.pop("data", {})))
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-cli...)",
        data=data,
        **overrides,
    )


def _no_traffic() -> Any:
    """Patch every API call setup makes, so no request is attempted.

    Returns:
        A context manager stacking the three patches.
    """
    return patch.multiple(
        "custom_components.hostaway.HostawayApiClient",
        test_connection=AsyncMock(return_value=True),
        get_all_listings=AsyncMock(return_value=[]),
        get_all_reservations=AsyncMock(return_value=[]),
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set an entry up with every outbound call stubbed out.

    Args:
        hass: Home Assistant instance.
        entry: The entry to set up.
    """
    entry.add_to_hass(hass)
    with _no_traffic():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


class TestWhereTheRegistryLives:
    """T022: not under the key that decides whether services go away."""

    async def test_the_registry_is_not_inside_the_domain_data(
        self, hass: HomeAssistant
    ) -> None:
        """Unload reads ``hass.data[DOMAIN]`` to find the last entry.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)

        assert DATA_RATE_LIMITERS in hass.data
        assert DATA_RATE_LIMITERS not in hass.data[DOMAIN]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        # The trap: anything parked here keeps the dict truthy forever.
        assert not hass.data.get(DOMAIN)
        assert not hass.services.has_service(DOMAIN, "set_door_code")

    async def test_the_limiter_survives_an_unload(self, hass: HomeAssistant) -> None:
        """An unload may be the first half of a reload.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)
        limiter = get_registry(hass).limiters[_ACCOUNT]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert get_registry(hass).limiters[_ACCOUNT] is limiter


class TestOneLimiterPerAccount:
    """T023: two entries, one account, one set of counters."""

    async def test_two_entries_share_one_limiter(self, hass: HomeAssistant) -> None:
        """The server does not care how many entries we configured.

        Args:
            hass: Home Assistant instance.
        """
        first = _make_entry(unique_id="one")
        second = _make_entry(unique_id="two", data={CONF_SELECTED_LISTINGS: [67890]})
        await _setup(hass, first)
        await _setup(hass, second)

        registry = get_registry(hass)

        assert list(registry.limiters) == [_ACCOUNT]
        assert (
            hass.data[DOMAIN][first.entry_id]["limiter"]
            is (hass.data[DOMAIN][second.entry_id]["limiter"])
        )

    async def test_the_lower_budget_wins(self, hass: HomeAssistant) -> None:
        """One counter cannot honour two different ceilings.

        Args:
            hass: Home Assistant instance.
        """
        generous = _make_entry(unique_id="one", data={CONF_RATE_LIMIT_BUDGET: 150})
        cautious = _make_entry(unique_id="two", data={CONF_RATE_LIMIT_BUDGET: 40})
        await _setup(hass, generous)
        await _setup(hass, cautious)

        gates = get_registry(hass).limiters[_ACCOUNT].snapshot().gates

        assert gates["account"].effective_budget == 40
        assert gates["ip"].effective_budget == 40

    async def test_a_second_account_is_refused(self, hass: HomeAssistant) -> None:
        """Aliasing two accounts onto one gate lets either silence the other.

        Args:
            hass: Home Assistant instance.
        """
        await _setup(hass, _make_entry(unique_id="one"))
        intruder = _make_entry(
            unique_id="other", data={CONF_CLIENT_ID: "a-different-account"}
        )
        await _setup(hass, intruder)

        # Non-retryable: no amount of waiting makes a second account work.
        assert intruder.state is ConfigEntryState.SETUP_ERROR
        assert list(get_registry(hass).limiters) == [_ACCOUNT]

    def test_a_second_account_raises_a_config_entry_error(
        self, hass: HomeAssistant
    ) -> None:
        """The error type is the retry decision.

        Args:
            hass: Home Assistant instance.
        """
        from custom_components.hostaway.rate_limit_registry import register_entry

        first = _make_entry(unique_id="one")
        first.add_to_hass(hass)
        register_entry(hass, first)
        second = _make_entry(unique_id="two", data={CONF_CLIENT_ID: "other"})
        second.add_to_hass(hass)

        with pytest.raises(ConfigEntryError):
            register_entry(hass, second)

    async def test_the_config_flow_turns_a_second_account_away(
        self, hass: HomeAssistant
    ) -> None:
        """Failing at setup is a worse way to say no than not starting.

        Args:
            hass: Home Assistant instance.
        """
        existing = _make_entry()
        existing.add_to_hass(hass)

        with patch(
            "custom_components.hostaway.config_flow._validate_credentials",
            new_callable=AsyncMock,
        ) as validate:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_USER},
                data={
                    CONF_CLIENT_ID: "a-different-account",
                    CONF_CLIENT_SECRET: "secret",
                },
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "single_instance_allowed"
        # Turned away before spending a request on it.
        validate.assert_not_awaited()

    async def test_the_config_flow_still_allows_the_same_account(
        self, hass: HomeAssistant
    ) -> None:
        """One account, more than one entry, is still supported.

        Args:
            hass: Home Assistant instance.
        """
        existing = _make_entry(unique_id="already-here")
        existing.add_to_hass(hass)

        with (
            patch(
                "custom_components.hostaway.config_flow._validate_credentials",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.hostaway.config_flow._fetch_listings",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_USER},
                data={
                    CONF_CLIENT_ID: _ACCOUNT,
                    CONF_CLIENT_SECRET: "secret",
                },
            )

        assert result["type"] is not FlowResultType.ABORT or result["reason"] != (
            "single_instance_allowed"
        )


class TestReloadDoesNotRefundBudget:
    """T024/SC-012: the window does not care that we restarted."""

    async def test_a_reload_does_not_raise_the_budget(
        self, hass: HomeAssistant
    ) -> None:
        """The gap between unload and setup is not free capacity.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: 30})
        await _setup(hass, entry)
        limiter = get_registry(hass).limiters[_ACCOUNT]
        await limiter.acquire("GET", "/v1/listings")
        admitted_before = limiter.snapshot().admitted_total
        admitted_in_window_before = (
            limiter.snapshot().gates["account"].admitted_in_window
        )

        with _no_traffic():
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        after = get_registry(hass).limiters[_ACCOUNT]
        gates = after.snapshot().gates

        assert after is limiter
        assert after.snapshot().admitted_total >= admitted_before
        # The window itself survived: the reload did not hand back the
        # admissions already spent in it.
        assert gates["account"].admitted_in_window >= admitted_in_window_before
        assert gates["ip"].admitted_in_window >= admitted_in_window_before
        assert gates["account"].effective_budget == 30
        assert gates["ip"].effective_budget == 30

    async def test_the_unload_half_does_not_raise_a_shared_minimum(
        self, hass: HomeAssistant
    ) -> None:
        """The entry being reloaded is the one holding the budget down.

        Args:
            hass: Home Assistant instance.
        """
        cautious = _make_entry(unique_id="low", data={CONF_RATE_LIMIT_BUDGET: 20})
        generous = _make_entry(unique_id="high", data={CONF_RATE_LIMIT_BUDGET: 170})
        await _setup(hass, cautious)
        await _setup(hass, generous)
        limiter = get_registry(hass).limiters[_ACCOUNT]

        await hass.config_entries.async_unload(cautious.entry_id)
        await hass.async_block_till_done()

        assert limiter.snapshot().gates["account"].effective_budget == 20
        assert limiter.snapshot().gates["ip"].effective_budget == 20

    async def test_an_unload_keeps_the_budget_for_one_window(
        self, hass: HomeAssistant
    ) -> None:
        """An unload might be a reload, so its claim is not given back yet.

        Args:
            hass: Home Assistant instance.
        """
        cautious = _make_entry(unique_id="low", data={CONF_RATE_LIMIT_BUDGET: 20})
        generous = _make_entry(unique_id="high", data={CONF_RATE_LIMIT_BUDGET: 170})
        await _setup(hass, cautious)
        await _setup(hass, generous)
        limiter = get_registry(hass).limiters[_ACCOUNT]

        await hass.config_entries.async_unload(cautious.entry_id)
        await hass.async_block_till_done()

        assert cautious.entry_id in get_registry(hass).contributions
        assert limiter.snapshot().gates["account"].effective_budget == 20

    async def test_a_true_unload_releases_once_the_window_passes(
        self, hass: HomeAssistant
    ) -> None:
        """No setup came back for it, so it stops holding the others down.

        Args:
            hass: Home Assistant instance.
        """
        cautious = _make_entry(unique_id="low", data={CONF_RATE_LIMIT_BUDGET: 20})
        generous = _make_entry(unique_id="high", data={CONF_RATE_LIMIT_BUDGET: 170})
        await _setup(hass, cautious)
        await _setup(hass, generous)
        limiter = get_registry(hass).limiters[_ACCOUNT]

        await hass.config_entries.async_unload(cautious.entry_id)
        await hass.async_block_till_done()

        with patch(
            "custom_components.hostaway.rate_limit_registry._now",
            return_value=time.monotonic() + RATE_LIMIT_WINDOW_SECONDS + 1,
        ):
            registry = get_registry(hass)

        assert cautious.entry_id not in registry.contributions
        assert limiter.snapshot().gates["account"].effective_budget == 170
        assert limiter.snapshot().gates["ip"].effective_budget == 170

    async def test_a_reload_reclaims_its_pending_contribution(
        self, hass: HomeAssistant
    ) -> None:
        """Setting the entry up again cancels the pending release.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: 20})
        await _setup(hass, entry)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert get_registry(hass).pending_release

        await _setup(hass, entry)

        assert not get_registry(hass).pending_release
        limiter = get_registry(hass).limiters[_ACCOUNT]
        assert limiter.snapshot().gates["account"].effective_budget == 20

    async def test_removal_releases_the_limiter(self, hass: HomeAssistant) -> None:
        """Removal, unlike an unload, really is the end.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)

        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        assert get_registry(hass).limiters == {}
        assert get_registry(hass).contributions == {}

    async def test_removal_keeps_a_limiter_another_entry_still_uses(
        self, hass: HomeAssistant
    ) -> None:
        """The account is still configured, so its counters still matter.

        Args:
            hass: Home Assistant instance.
        """
        first = _make_entry(unique_id="one", data={CONF_RATE_LIMIT_BUDGET: 60})
        second = _make_entry(unique_id="two", data={CONF_RATE_LIMIT_BUDGET: 90})
        await _setup(hass, first)
        await _setup(hass, second)
        limiter = get_registry(hass).limiters[_ACCOUNT]

        await hass.config_entries.async_remove(first.entry_id)
        await hass.async_block_till_done()

        assert get_registry(hass).limiters[_ACCOUNT] is limiter
        # The departing entry's lower ceiling goes with it.
        assert limiter.snapshot().gates["account"].effective_budget == 90

    def test_releasing_an_unknown_entry_is_harmless(self, hass: HomeAssistant) -> None:
        """Removal can arrive for an entry that never finished setup.

        Args:
            hass: Home Assistant instance.
        """
        release_entry(hass, _make_entry(unique_id="never-set-up"))

        assert get_registry(hass).limiters == {}


class TestTheConfiguredBudget:
    """T022: a nonsense option must not become a nonsense ceiling."""

    @pytest.mark.parametrize(
        "value", [0, -5, 201, True, "150", 12.5, None], ids=lambda v: repr(v)
    )
    def test_an_unusable_budget_falls_back_to_the_default(self, value: object) -> None:
        """``configure()`` would reject it, and setup would fail with it.

        Args:
            value: The configured value under test.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: value})

        assert entry_budget(entry) == DEFAULT_RATE_LIMIT_BUDGET

    def test_an_option_overrides_the_entry_data(self) -> None:
        """Options are where the options flow writes."""
        entry = _make_entry(
            data={CONF_RATE_LIMIT_BUDGET: 100}, options={CONF_RATE_LIMIT_BUDGET: 50}
        )

        assert entry_budget(entry) == 50


class TestInteractivePriorityIsStructural:
    """T025: four places, and no per-service or per-method code."""

    async def test_a_brand_new_service_is_interactive_for_free(
        self, hass: HomeAssistant
    ) -> None:
        """Priority comes from the binder, not from the handler.

        Args:
            hass: Home Assistant instance.
        """
        from custom_components.hostaway.services import _bind_handler

        seen: list[RequestPriority] = []

        async def brand_new_handler(
            _hass: HomeAssistant, _call: object
        ) -> dict[str, Any]:
            """Record the ambient priority of a service nobody adapted.

            Args:
                _hass: Home Assistant instance, unused.
                _call: The service call, unused.

            Returns:
                An empty result.
            """
            seen.append(current_request_context().priority)
            return {}

        bound = _bind_handler(hass, cast(Any, brand_new_handler))
        await bound(cast(Any, object()))

        assert seen == [RequestPriority.INTERACTIVE]

    async def test_the_setup_probe_is_interactive(self, hass: HomeAssistant) -> None:
        """Somebody is watching the integration start.

        Args:
            hass: Home Assistant instance.
        """
        seen: list[RequestPriority] = []

        async def record() -> bool:
            """Record the ambient priority of the setup probe.

            Returns:
                True, as a successful probe does.
            """
            seen.append(current_request_context().priority)
            return True

        entry = _make_entry()
        entry.add_to_hass(hass)
        with (
            _no_traffic(),
            patch(
                "custom_components.hostaway.HostawayApiClient.test_connection",
                side_effect=record,
            ),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert seen == [RequestPriority.INTERACTIVE]


class TestConfigFlowValidationIsPaced:
    """T042: a credential check is traffic like any other."""

    async def test_validation_reuses_a_configured_account_limiter(
        self, hass: HomeAssistant
    ) -> None:
        """The existing window already knows what has been sent.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)
        shared = get_registry(hass).limiters[_ACCOUNT]

        with validation_limiter(hass, _ACCOUNT) as limiter:
            assert limiter is shared

        # Reusing it must not close it out from under the config entry.
        await shared.acquire("GET", "/v1/listings")

    async def test_a_new_account_gets_a_transient_limiter(
        self, hass: HomeAssistant
    ) -> None:
        """There is no entry and no option to take a budget from.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: 25})
        await _setup(hass, entry)
        configured = get_registry(hass).limiters[_ACCOUNT]

        with validation_limiter(hass, "some-other-account") as transient:
            assert transient is not configured
            assert "some-other-account" not in get_registry(hass).limiters
            # Excluded from the minima, never given a private IP gate.
            assert configured.snapshot().gates["account"].effective_budget == 25

        assert configured.snapshot().gates["ip"].effective_budget == 25

    async def test_a_transient_limiter_shares_the_ip_gate(
        self, hass: HomeAssistant
    ) -> None:
        """A config-flow request is still a request from this IP.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)
        configured = get_registry(hass).limiters[_ACCOUNT]
        await configured.acquire("GET", "/v1/listings")
        charged = configured.snapshot().gates["ip"].admitted_in_window

        with validation_limiter(hass, "some-other-account") as transient:
            # The combined history, not merely the budget, is shared.
            assert transient.snapshot().gates["ip"].admitted_in_window == charged
            await transient.acquire("POST", "/v1/accessTokens")

        assert configured.snapshot().gates["ip"].admitted_in_window == charged + 1

    async def test_both_helpers_pace_both_of_their_clients(
        self, hass: HomeAssistant
    ) -> None:
        """A token request and a data request both leave the process.

        Args:
            hass: Home Assistant instance.
        """
        from custom_components.hostaway import config_flow

        limiter = AccountRateLimiter(_ACCOUNT)
        get_registry(hass).limiters[_ACCOUNT] = limiter
        given: list[object] = []
        priorities: list[RequestPriority] = []

        def record_token_manager(**kwargs: Any) -> Mock:
            """Capture the limiter handed to the token manager.

            Args:
                **kwargs: The constructor arguments.

            Returns:
                A stub token manager.
            """
            given.append(kwargs.get("limiter"))
            return Mock()

        def record_client(**kwargs: Any) -> Mock:
            """Capture the limiter handed to the API client.

            Args:
                **kwargs: The constructor arguments.

            Returns:
                A stub API client that records the ambient priority.
            """
            given.append(kwargs.get("limiter"))

            async def probe(*_args: Any, **_kwargs: Any) -> Any:
                """Record the priority the probe would have used.

                Returns:
                    An empty result.
                """
                priorities.append(current_request_context().priority)
                return []

            client = Mock()
            client.test_connection = probe
            client.get_all_listings = probe
            return client

        with (
            patch.object(config_flow, "HostawayTokenManager", record_token_manager),
            patch.object(config_flow, "HostawayApiClient", record_client),
        ):
            await config_flow._validate_credentials(hass, _ACCOUNT, "secret")
            await config_flow._fetch_listings(hass, _ACCOUNT, "secret")

        # Two helpers, each building a token manager and a client.
        assert given == [limiter] * 4
        assert priorities == [RequestPriority.INTERACTIVE] * 2
        limiter.close()

    async def test_validation_waits_behind_a_saturated_ip_gate(
        self, hass: HomeAssistant
    ) -> None:
        """Another account's traffic already spent the IP's allowance.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: 2})
        await _setup(hass, entry)
        configured = get_registry(hass).limiters[_ACCOUNT]
        ip_gate = configured.snapshot().gates["ip"]
        for _ in range(ip_gate.effective_budget - ip_gate.admitted_in_window):
            await configured.acquire("GET", "/v1/listings")

        with validation_limiter(hass, "some-other-account") as transient:
            probe = asyncio.create_task(transient.acquire("POST", "/v1/accessTokens"))
            for _ in range(10):
                await asyncio.sleep(0)

            assert not probe.done()
            probe.cancel()
            with suppress(asyncio.CancelledError):
                await probe

    async def test_validation_paces_its_real_http_calls(
        self, hass: HomeAssistant
    ) -> None:
        """Both the token request and the data request buy budget first.

        Args:
            hass: Home Assistant instance.
        """
        from custom_components.hostaway import config_flow

        limiter = AccountRateLimiter(_ACCOUNT)
        get_registry(hass).limiters[_ACCOUNT] = limiter
        paced: list[tuple[str, str]] = []
        original = AccountRateLimiter.acquire

        async def spy(self: AccountRateLimiter, method: str, path: str) -> None:
            """Record every admission the helper asks for.

            Args:
                self: The limiter being acquired against.
                method: HTTP method of the pending request.
                path: Request path.
            """
            paced.append((method, path))
            await original(self, method, path)

        with (
            respx.mock,
            patch.object(AccountRateLimiter, "acquire", spy),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            respx.post(FAKE_TOKEN_URL).mock(
                return_value=httpx.Response(200, json=make_token_response())
            )
            respx.get(f"{FAKE_BASE_URL}/v1/listings").mock(
                return_value=httpx.Response(
                    200, json={"status": "success", "result": []}
                )
            )

            await config_flow._validate_credentials(hass, _ACCOUNT, "secret")

        assert ("POST", "/v1/accessTokens") in paced
        assert ("GET", "/v1/listings") in paced
        limiter.close()
