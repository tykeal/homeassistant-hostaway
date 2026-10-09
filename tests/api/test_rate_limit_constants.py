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
    """FR-034: the window tracks the documented API, so it is fixed."""

    def test_no_window_setter_or_option_exists(self) -> None:
        """Only the budget is tunable; the window length never is."""
        names = [n for n in dir(const) if not n.startswith("_")]
        assert "RATE_LIMIT_WINDOW_SECONDS" in names
        assert not [
            n for n in names if "WINDOW" in n and n != "RATE_LIMIT_WINDOW_SECONDS"
        ]
        assert not any(callable(getattr(const, n)) for n in names)
