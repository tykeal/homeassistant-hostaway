# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the config entry diagnostics payload (T036, SC-013).

Diagnostics are the one place an operator looks when the integration is
quiet and they want to know why. The payload therefore has to separate
the account counter from the IP counter, name the counter Hostaway
actually applied, and carry nothing that identifies the account: the
entry ``unique_id`` is the client id.
"""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.rate_limit import AccountRateLimiter
from custom_components.hostaway.const import (
    CONF_CACHED_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RATE_LIMIT_BUDGET,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
)
from custom_components.hostaway.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hostaway.rate_limit_registry import get_registry

_ACCOUNT = "test-client-id"
_SECRET = "test-client-secret"
_TOKEN = "hostaway-access-token-0a1b2c3d4e5f"


def _make_entry(**overrides: Any) -> MockConfigEntry:
    """Create a config entry for the shared test account.

    Args:
        **overrides: Fields to override, including extra ``data``.

    Returns:
        A config entry ready to add to Home Assistant.
    """
    data: dict[str, Any] = {
        CONF_CLIENT_ID: _ACCOUNT,
        CONF_CLIENT_SECRET: _SECRET,
        CONF_SELECTED_LISTINGS: [12345],
    }
    data.update(cast(dict[str, Any], overrides.pop("data", {})))
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-cli...)",
        data=data,
        unique_id=_ACCOUNT,
        **overrides,
    )


def _no_traffic() -> Any:
    """Patch every API call setup makes, so no request is attempted.

    Returns:
        A context manager stacking the patches.
    """
    return patch.multiple(
        "custom_components.hostaway.HostawayApiClient",
        test_connection=AsyncMock(return_value=True),
        get_all_listings=AsyncMock(return_value=[]),
        get_all_reservations=AsyncMock(return_value=[]),
        _request=AsyncMock(return_value={"status": "success", "result": []}),
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> AccountRateLimiter:
    """Set an entry up with every outbound call stubbed out.

    Args:
        hass: Home Assistant instance.
        entry: The entry to set up.

    Returns:
        The limiter pacing the entry's account.
    """
    entry.add_to_hass(hass)
    with _no_traffic():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return get_registry(hass).limiters[_ACCOUNT]


class TestTheGatesAreReportedSeparately:
    """SC-013: one gate being held is not the same as both being held."""

    async def test_both_gates_appear_with_their_own_state(
        self, hass: HomeAssistant
    ) -> None:
        """The payload carries an account object and an IP object.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)
        gates = payload["rate_limit"]["gates"]

        assert set(gates) >= {"account", "ip"}
        for gate in (gates["account"], gates["ip"]):
            assert set(gate) == {
                "budget",
                "effective_budget",
                "window_seconds",
                "admitted_in_window",
                "waiting_interactive",
                "waiting_scheduled",
                "suppressed",
                "suppressed_for_seconds",
            }

    async def test_a_diverged_pair_of_gates_is_visible(
        self, hass: HomeAssistant
    ) -> None:
        """Hostaway refusing on IP must not read as an account problem.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        limiter = await _setup(hass, entry)

        limiter.note_rate_limited("ip", None, "GET", "/v1/listings")

        payload = await async_get_config_entry_diagnostics(hass, entry)
        gates = payload["rate_limit"]["gates"]

        assert gates["ip"]["suppressed"] is True
        assert cast(float, gates["ip"]["suppressed_for_seconds"]) > 0
        # The account gate was never named by the server, so it is free.
        assert gates["account"]["suppressed"] is False
        assert gates["account"]["suppressed_for_seconds"] is None


class TestTheAppliedCounterIsNamed:
    """SC-013: 'rate limited' is useless without saying by which counter."""

    async def test_each_counter_is_counted_under_its_own_key(
        self, hass: HomeAssistant
    ) -> None:
        """Account, IP, endpoint, provider and unknown stay distinct.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        limiter = await _setup(hass, entry)

        limiter.note_rate_limited("account", None, "GET", "/v1/listings")
        limiter.note_rate_limited("provider", None, "GET", "/v1/listings")
        limiter.note_rate_limited("something-new", None, "GET", "/v1/listings")

        payload = await async_get_config_entry_diagnostics(hass, entry)
        rate_limit = payload["rate_limit"]

        assert rate_limit["rate_limited_total"] == 3
        assert rate_limit["rate_limited_by_counter"] == {
            "account": 1,
            "ip": 0,
            "endpoint": 0,
            "provider": 1,
            "unknown": 1,
        }

    async def test_a_counter_we_do_not_know_lands_in_unknown(
        self, hass: HomeAssistant
    ) -> None:
        """A name Hostaway invents later must not add a payload key.

        Consumers of the payload compare the five documented counters.
        A sixth key appearing because the server started naming a new
        counter would silently drop out of any comparison, so an
        unrecognised name is folded into ``unknown`` instead.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        limiter = await _setup(hass, entry)
        limiter._stats.rate_limited_by_counter["tenant"] = 2

        payload = await async_get_config_entry_diagnostics(hass, entry)

        assert payload["rate_limit"]["rate_limited_by_counter"] == {
            "account": 0,
            "ip": 0,
            "endpoint": 0,
            "provider": 0,
            "unknown": 2,
        }

    async def test_every_counter_key_is_present_from_the_start(
        self, hass: HomeAssistant
    ) -> None:
        """A zero is an answer; a missing key is a question.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)

        assert payload["rate_limit"]["rate_limited_by_counter"] == {
            "account": 0,
            "ip": 0,
            "endpoint": 0,
            "provider": 0,
            "unknown": 0,
        }


class TestTheShedCountsAreAttributed:
    """SC-013: which poll is being dropped matters to the operator."""

    async def test_the_counts_use_fixed_coordinator_labels(
        self, hass: HomeAssistant
    ) -> None:
        """The coordinators' own names embed the client id.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        entry_data["reservations_coordinator"]._shed_count = 7

        payload = await async_get_config_entry_diagnostics(hass, entry)

        assert payload["rate_limit"]["shed_by_coordinator"] == {
            "listings": 0,
            "reservations": 7,
            "custom_fields": 0,
        }


class TestTheBudgetSourceIsHonest:
    """An operator needs to know whether the figure is one they chose."""

    async def test_an_untouched_entry_reports_the_default(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing stored means nothing was chosen.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)

        assert payload["rate_limit"]["budget_source"] == "default"

    async def test_a_configured_entry_reports_an_option(
        self, hass: HomeAssistant
    ) -> None:
        """A stored budget is the operator's own decision.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(options={CONF_RATE_LIMIT_BUDGET: 60})
        await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)
        rate_limit = payload["rate_limit"]

        assert rate_limit["budget_source"] == "option"
        assert rate_limit["gates"]["account"]["budget"] == 60

    async def test_an_unusable_stored_budget_is_not_an_option(
        self, hass: HomeAssistant
    ) -> None:
        """A budget that is not honoured must not be reported as chosen.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(options={CONF_RATE_LIMIT_BUDGET: 9000})
        await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)

        assert payload["rate_limit"]["budget_source"] == "default"


class TestNothingSecretIsEmitted:
    """SC-013/Constitution X: diagnostics are attached to support tickets."""

    async def test_the_payload_carries_no_credential(self, hass: HomeAssistant) -> None:
        """The client id is both half the credential and the unique id.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_CACHED_TOKEN: _TOKEN})
        limiter = await _setup(hass, entry)
        limiter.note_rate_limited("account", None, "GET", "/v1/listings")

        payload = await async_get_config_entry_diagnostics(hass, entry)
        serialized = json.dumps(payload)

        assert _ACCOUNT not in serialized
        assert _SECRET not in serialized
        assert _TOKEN not in serialized
        assert cast(str, entry.unique_id) not in serialized

    async def test_the_limiter_label_is_not_the_account_key(
        self, hass: HomeAssistant
    ) -> None:
        """An operator still needs to tell two limiters apart.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        limiter = await _setup(hass, entry)

        payload = await async_get_config_entry_diagnostics(hass, entry)
        label = payload["rate_limit"]["limiter_label"]

        assert label == limiter.label
        assert label != limiter.account_key
        assert _ACCOUNT not in label


class TestAnEntryWithoutALimiter:
    """Diagnostics are often pulled from an entry that failed to load."""

    async def test_an_entry_that_never_loaded_keeps_the_payload_shape(
        self, hass: HomeAssistant
    ) -> None:
        """A download that raises tells the operator nothing at all.

        The gate objects are what the operator was sent to look at, so
        they are present even when nothing has been measured yet.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(options={CONF_RATE_LIMIT_BUDGET: 60})
        entry.add_to_hass(hass)

        payload = await async_get_config_entry_diagnostics(hass, entry)
        rate_limit = payload["rate_limit"]

        assert rate_limit["limiter_label"] is None
        assert set(rate_limit["gates"]) == {"account", "ip"}
        for gate in rate_limit["gates"].values():
            assert gate["budget"] == 60
            assert gate["admitted_in_window"] == 0
            assert gate["suppressed"] is False
        assert rate_limit["shed_by_coordinator"] == {
            "listings": 0,
            "reservations": 0,
            "custom_fields": 0,
        }
        assert json.dumps(payload)

    async def test_an_unloaded_entry_still_reports_its_live_limiter(
        self, hass: HomeAssistant
    ) -> None:
        """The account is still being paced after the entry goes away.

        The registry deliberately outlives an unload, so the pacing an
        operator is asking about is still observable - and is the only
        honest answer to give them.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        limiter = await _setup(hass, entry)
        limiter.note_rate_limited("account", None, "GET", "/v1/listings")

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        payload = await async_get_config_entry_diagnostics(hass, entry)
        rate_limit = payload["rate_limit"]

        assert rate_limit["limiter_label"] == limiter.label
        assert rate_limit["rate_limited_total"] == 1
        assert rate_limit["gates"]["account"]["suppressed"] is True
