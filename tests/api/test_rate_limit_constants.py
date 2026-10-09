# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the documented Hostaway rate-limit constants."""

from __future__ import annotations

import inspect

from custom_components.hostaway.api import const


class TestRateLimitConstants:
    """Values the limiter depends on, pinned against accidental drift."""

    def test_window_is_ten_seconds(self) -> None:
        """Hostaway documents a 10-second general window."""
        assert const.RATE_LIMIT_WINDOW_SECONDS == 10.0

    def test_ceiling_is_documented_limit(self) -> None:
        """The documented general limit is 200 requests per window."""
        assert const.RATE_LIMIT_CEILING == 200

    def test_default_budget_leaves_safety_margin(self) -> None:
        """The default budget is deliberately below the documented limit."""
        assert const.DEFAULT_RATE_LIMIT_BUDGET == 180
        assert const.DEFAULT_RATE_LIMIT_BUDGET < const.RATE_LIMIT_CEILING

    def test_default_suppression_is_one_window(self) -> None:
        """A headerless 429 suppresses for exactly one documented window."""
        assert const.DEFAULT_SUPPRESSION_SECONDS == 10.0
        assert const.DEFAULT_SUPPRESSION_SECONDS == const.RATE_LIMIT_WINDOW_SECONDS

    def test_max_suppression_bounds_corrupt_headers(self) -> None:
        """A corrupt Retry-After cannot wedge the integration forever."""
        assert const.MAX_SUPPRESSION_SECONDS == 3600.0
        assert const.MAX_SUPPRESSION_SECONDS > const.MAX_BACKOFF

    def test_budget_is_a_valid_gate_budget(self) -> None:
        """The default must satisfy the gate invariant 1 <= budget <= ceiling."""
        assert 1 <= const.DEFAULT_RATE_LIMIT_BUDGET <= const.RATE_LIMIT_CEILING


class TestSafetyMarginIsDocumented:
    """The 180 default is an owner decision, not a Hostaway figure."""

    def test_module_records_that_the_margin_is_ours(self) -> None:
        """A reader must not mistake 180 for a documented Hostaway value."""
        source = inspect.getsource(const)
        assert "not a Hostaway-documented value" in source


class TestWindowLengthIsNotConfigurable:
    """FR-034: the window tracks the documented API, so it is fixed.

    Guarding only ``api.const`` would be hollow, since an operator-facing
    option is declared in the integration's own ``const`` and surfaced by
    the config flow. All three are checked.
    """

    def test_api_const_exposes_no_window_setter(self) -> None:
        """Only the budget is tunable; the window length never is."""
        names = [n for n in dir(const) if not n.startswith("_")]
        assert "RATE_LIMIT_WINDOW_SECONDS" in names
        assert not [
            n for n in names if "WINDOW" in n and n != "RATE_LIMIT_WINDOW_SECONDS"
        ]
        assert not any(callable(getattr(const, n)) for n in names)

    def test_no_integration_option_constant_declares_a_window(self) -> None:
        """An option would live here, so no CONF_ name may mention one."""
        from custom_components.hostaway import const as integration_const

        offenders = [
            name
            for name in dir(integration_const)
            if name.startswith(("CONF_", "DEFAULT_")) and "WINDOW" in name
        ]
        assert not offenders

    def test_config_flow_exposes_no_window_field(self) -> None:
        """The operator-facing schema must offer no window control."""
        import inspect

        from custom_components.hostaway import config_flow

        assert "window" not in inspect.getsource(config_flow).lower()

    def test_translations_offer_no_window_control(self) -> None:
        """A user-visible label would betray an option we must not have."""
        import json
        from pathlib import Path

        import custom_components.hostaway as integration

        strings = Path(integration.__file__).with_name("strings.json")
        assert strings.is_file(), "the integration must ship strings.json"
        assert "window" not in json.dumps(json.loads(strings.read_text())).lower()


class TestStaleConstantsRemoved:
    """Hostaway corrected its published limits on 2026-08-20."""

    def test_superseded_limits_are_gone(self) -> None:
        """The 15/IP and 20/account figures predate the correction."""
        assert not hasattr(const, "RATE_LIMIT_PER_IP")
        assert not hasattr(const, "RATE_LIMIT_PER_ACCOUNT")
