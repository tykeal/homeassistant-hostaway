# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway config flow and options flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.const import (
    DEFAULT_RATE_LIMIT_BUDGET,
    RATE_LIMIT_CEILING,
)
from custom_components.hostaway.config_flow import _validated_budget
from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FILTER_CANCELLED,
    CONF_RATE_LIMIT_BUDGET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_FILTER_CANCELLED,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPTIONS_SECTION_ADVANCED,
)
from custom_components.hostaway.rate_limit_registry import entry_budget

VALID_INPUT = {
    CONF_CLIENT_ID: "test-client-id",
    CONF_CLIENT_SECRET: "test-client-secret",
}


def _advanced_section(schema: vol.Schema | None) -> section | None:
    """Return the advanced section of an options schema.

    Args:
        schema: The schema the options form is showing, if any.

    Returns:
        The section, or ``None`` when the form has no advanced section.
    """
    if schema is None:
        return None
    for key, value in schema.schema.items():
        if str(key) == OPTIONS_SECTION_ADVANCED and isinstance(value, section):
            return value
    return None


def _section_default(advanced: section, key: str) -> object:
    """Return the default a section offers for one field.

    Args:
        advanced: The section to inspect.
        key: The field name.

    Returns:
        The default value, or ``None`` when the field has none.
    """
    for marker in advanced.schema.schema:
        if str(marker) == key:
            return marker.default()
    return None


def _key_paths(data: dict, prefix: str = "") -> set[str]:
    """Return every dotted key path in a translation document.

    Args:
        data: The document to walk.
        prefix: The path accumulated so far.

    Returns:
        Every dotted key path the document contains.
    """
    paths: set[str] = set()
    for key, value in data.items():
        paths.add(f"{prefix}{key}")
        if isinstance(value, dict):
            paths |= _key_paths(value, f"{prefix}{key}.")
    return paths


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
    options: dict[str, Any] = {
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_RESERVATION_SCAN_INTERVAL: DEFAULT_RESERVATION_SCAN_INTERVAL,
    }
    options.update(cast(dict[str, Any], overrides.pop("options", {})))
    data: dict[str, Any] = {
        CONF_CLIENT_ID: "test-client-id",
        CONF_CLIENT_SECRET: "test-client-secret",
        CONF_SELECTED_LISTINGS: [12345],
    }
    data.update(cast(dict[str, Any], overrides.pop("data", {})))
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-cli...)",
        data=data,
        unique_id="test-client-id",
        options=options,
        **overrides,  # type: ignore[arg-type]
    )


class TestStepUser:
    """Tests for the user config flow step."""

    async def test_shows_form_when_no_input(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Step user shows form when user_input is None."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_valid_credentials_proceed_to_listings(
        self,
        mock_validate: AsyncMock,
        mock_fetch: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid credentials proceed to the listings step."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(id=101, name="Test Listing", status="active"),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "listings"
        mock_validate.assert_awaited_once()

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=Exception("Invalid client credentials"),
    )
    async def test_invalid_auth_shows_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid credentials show invalid_auth error."""
        from custom_components.hostaway.api.exceptions import HostawayAuthError

        mock_validate.side_effect = HostawayAuthError("bad creds")

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_connection_error_shows_cannot_connect(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Connection failure shows cannot_connect error."""
        from custom_components.hostaway.api.exceptions import (
            HostawayConnectionError,
        )

        mock_validate.side_effect = HostawayConnectionError("timeout")

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_duplicate_client_id_aborts(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Duplicate client_id aborts with already_configured."""
        existing = _make_entry()
        existing.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    )
    async def test_unknown_error_shows_unknown(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unexpected exception shows unknown error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


class TestStepListings:
    """Tests for the listings selection step."""

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    async def test_fetches_and_displays_listings(
        self,
        mock_fetch: AsyncMock,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Listings step fetches all listings and shows multi-select."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(id=101, name="Beach House", status="active"),
            HostawayListing(id=102, name="Mountain Cabin", status="active"),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "listings"

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    async def test_labels_use_internal_name(
        self,
        mock_fetch: AsyncMock,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Labels prefer internal_name over external name."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(
                id=101,
                name="Beach House",
                internal_name="beach-1",
                status="active",
            ),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["data_schema"] is not None
        schema = result["data_schema"].schema
        selector = schema[vol.Required(CONF_SELECTED_LISTINGS)]
        options = selector.config["options"]
        assert options[0]["label"] == "beach-1 (ID: 101)"

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    async def test_labels_fallback_to_name(
        self,
        mock_fetch: AsyncMock,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Labels fall back to name when internal_name is None."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(
                id=201,
                name="Mountain Cabin",
                status="active",
            ),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        assert result["data_schema"] is not None
        schema = result["data_schema"].schema
        selector = schema[vol.Required(CONF_SELECTED_LISTINGS)]
        options = selector.config["options"]
        assert options[0]["label"] == "Mountain Cabin (ID: 201)"

    @patch(
        "custom_components.hostaway.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    async def test_selection_creates_entry(
        self,
        mock_fetch: AsyncMock,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Selecting listings creates config entry."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(id=101, name="Beach House", status="active"),
            HostawayListing(id=102, name="Mountain Cabin", status="active"),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        # Now select listings
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_SELECTED_LISTINGS: ["101", "102"]},
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_CLIENT_ID] == "test-client-id"
        assert result["data"][CONF_CLIENT_SECRET] == "test-client-secret"
        assert result["data"][CONF_SELECTED_LISTINGS] == [101, 102]

    @patch(
        "custom_components.hostaway.config_flow._validate_credentials",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "custom_components.hostaway.config_flow._fetch_listings",
        new_callable=AsyncMock,
    )
    async def test_no_selection_shows_error(
        self,
        mock_fetch: AsyncMock,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Empty listing selection shows error."""
        from custom_components.hostaway.api.models import HostawayListing

        mock_fetch.return_value = [
            HostawayListing(id=101, name="Beach House", status="active"),
        ]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=VALID_INPUT,
        )

        # Submit with empty selection
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_SELECTED_LISTINGS: []},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "no_listings_selected"}


class TestOptionsFlow:
    """Tests for the options flow."""

    async def test_shows_current_intervals(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Options flow shows form with current scan intervals."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_valid_intervals_accepted(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Valid intervals update config entry options."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 10,
                CONF_RESERVATION_SCAN_INTERVAL: 5,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SCAN_INTERVAL] == 10
        assert result["data"][CONF_RESERVATION_SCAN_INTERVAL] == 5
        assert result["data"][CONF_FILTER_CANCELLED] is DEFAULT_FILTER_CANCELLED

    async def test_filter_cancelled_toggle(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Filter cancelled toggle persists in options."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
                CONF_FILTER_CANCELLED: False,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_FILTER_CANCELLED] is False

    async def test_below_minimum_shows_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Interval below minimum shows error."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 0,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_scan_interval"}


async def test_custom_field_definitions_options_flow(
    hass: HomeAssistant,
) -> None:
    """Options flow exposes the custom field definitions scan interval."""
    from custom_components.hostaway.const import (
        CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
        DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    )

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"] is not None
    schema = result["data_schema"].schema
    assert any(
        getattr(key, "schema", None) == CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
        for key in schema
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: 0,
        },
    )
    assert result["errors"] == {"base": "invalid_scan_interval"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: (
                DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
            ),
        },
    )
    assert result["data"][CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL] == 15


async def test_custom_field_write_options_flow(
    hass: HomeAssistant,
) -> None:
    """Options flow exposes explicit account-bound write enablement."""
    from custom_components.hostaway.const import (
        CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
        CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID,
        CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED,
        CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED,
        CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID,
        CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED,
        DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    )

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    data_schema = result["data_schema"]
    assert data_schema is not None
    schema = data_schema.schema
    assert any(
        getattr(key, "schema", None) == CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID
        for key in schema
    )
    assert any(
        getattr(key, "schema", None) == CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED
        for key in schema
    )
    assert any(
        getattr(key, "schema", None) == CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED
        for key in schema
    )
    assert any(
        getattr(key, "schema", None)
        == CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED
        for key in schema
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: (
                DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
            ),
            CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED: True,
        },
    )
    assert result["errors"] == {"base": "missing_write_account_id"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: (
                DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
            ),
            CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID: 1,
            CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED: True,
            CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED: True,
            CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED: False,
        },
    )
    assert result["errors"] == {"base": "reservation_risk_not_accepted"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: (
                DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
            ),
            CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID: 1,
            CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED: True,
            CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED: True,
            CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED: True,
        },
    )
    assert result["data"][CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID] == 1
    assert result["data"][CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED] is True
    assert result["data"][CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED] is True
    assert result["data"][CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED] is True
    assert result["data"][CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID] == 1


async def test_custom_field_write_options_rebinds_risk(
    hass: HomeAssistant,
) -> None:
    """Changing accounts requires clearing reservation risk acceptance."""
    from custom_components.hostaway.const import (
        CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
        CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID,
        CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED,
        CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID,
        CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED,
        DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    )

    entry = _make_entry(
        options={
            CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID: 1,
            CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED: True,
            CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID: 1,
        }
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_SCAN_INTERVAL: 5,
            CONF_RESERVATION_SCAN_INTERVAL: 2,
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: (
                DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL
            ),
            CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID: 2,
            CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED: False,
            CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED: True,
        },
    )

    assert result["errors"] == {"base": "reservation_risk_not_accepted"}


class TestRateLimitBudgetOption:
    """Tests for the one rate-limit lever an operator gets."""

    async def test_an_untouched_entry_runs_on_the_default_budget(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-017: no stored budget means the default, with no migration.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        assert CONF_RATE_LIMIT_BUDGET not in entry.options

        result = await hass.config_entries.options.async_init(entry.entry_id)
        advanced = _advanced_section(result["data_schema"])

        assert advanced is not None
        assert _section_default(advanced, CONF_RATE_LIMIT_BUDGET) == (
            DEFAULT_RATE_LIMIT_BUDGET
        )
        assert advanced.options["collapsed"] is True
        assert entry_budget(entry) == DEFAULT_RATE_LIMIT_BUDGET

    async def test_the_ceiling_is_accepted(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-017: the budget may be raised to Hostaway's own limit.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
                OPTIONS_SECTION_ADVANCED: {
                    CONF_RATE_LIMIT_BUDGET: RATE_LIMIT_CEILING,
                },
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_RATE_LIMIT_BUDGET] == RATE_LIMIT_CEILING

    @pytest.mark.parametrize(
        "budget",
        [RATE_LIMIT_CEILING + 1, 500, 0, -5, 1.5, 200.9],
    )
    async def test_an_out_of_range_budget_is_refused_not_clamped(
        self,
        hass: HomeAssistant,
        budget: object,
    ) -> None:
        """SC-017: anything but a whole 1-200 stores nothing at all.

        Args:
            hass: Home Assistant instance.
            budget: The rejected value.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
                OPTIONS_SECTION_ADVANCED: {CONF_RATE_LIMIT_BUDGET: budget},
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_rate_limit_budget"}
        assert CONF_RATE_LIMIT_BUDGET not in entry.options

    async def test_a_whole_number_typed_as_a_decimal_is_accepted(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A number box hands back floats; 150.0 is still 150.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
                OPTIONS_SECTION_ADVANCED: {CONF_RATE_LIMIT_BUDGET: 150.0},
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_RATE_LIMIT_BUDGET] == 150
        assert isinstance(result["data"][CONF_RATE_LIMIT_BUDGET], int)

    async def test_a_collapsed_section_keeps_the_stored_budget(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-017: never opening the section must not reset the budget.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(options={CONF_RATE_LIMIT_BUDGET: 90})
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_RATE_LIMIT_BUDGET] == 90

    async def test_a_data_backed_budget_survives_the_form(
        self,
        hass: HomeAssistant,
    ) -> None:
        """SC-017: a budget stored in entry data is not quietly reset.

        An entry may carry the budget in ``data`` rather than ``options``.
        The form must offer that figure as the default and keep it when
        the section is left collapsed, rather than falling back to 180
        and raising the operator's allowance behind their back.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry(data={CONF_RATE_LIMIT_BUDGET: 120})
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)

        advanced = _advanced_section(result["data_schema"])

        assert advanced is not None
        assert _section_default(advanced, CONF_RATE_LIMIT_BUDGET) == 120

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_SCAN_INTERVAL: 5,
                CONF_RESERVATION_SCAN_INTERVAL: 2,
            },
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_RATE_LIMIT_BUDGET] == 120
        assert entry_budget(entry) == 120

    async def test_the_setup_flow_never_asks_about_the_budget(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-031: the budget is an options lever, not a setup question.

        Args:
            hass: Home Assistant instance.
        """
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

        assert result["type"] is FlowResultType.FORM
        schema = result["data_schema"]
        assert schema is not None
        keys = {str(key) for key in schema.schema}
        assert CONF_RATE_LIMIT_BUDGET not in keys
        assert OPTIONS_SECTION_ADVANCED not in keys

    async def test_the_window_length_is_not_an_option(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-034: the ten-second window is Hostaway's, not an operator's.

        Args:
            hass: Home Assistant instance.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        advanced = _advanced_section(result["data_schema"])

        assert advanced is not None
        keys = {str(key) for key in advanced.schema.schema}
        assert keys == {CONF_RATE_LIMIT_BUDGET}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (180, 180),
            (1, 1),
            (RATE_LIMIT_CEILING, RATE_LIMIT_CEILING),
            (150.0, 150),
            (True, None),
            (False, None),
            (1.5, None),
            (0, None),
            (-1, None),
            (RATE_LIMIT_CEILING + 1, None),
            ("180", None),
            (None, None),
        ],
    )
    def test_the_budget_validator_takes_whole_numbers_only(
        self,
        value: object,
        expected: int | None,
    ) -> None:
        """A booking count is a whole number, and a bool is not one.

        Args:
            value: The value to validate.
            expected: The validated budget, or ``None``.
        """
        assert _validated_budget(value) == expected

    def test_both_translation_files_describe_the_same_form(self) -> None:
        """Constitution VII: strings and translations may not drift."""
        root = Path("custom_components/hostaway")
        strings = json.loads((root / "strings.json").read_text())
        english = json.loads((root / "translations" / "en.json").read_text())

        assert _key_paths(strings) == _key_paths(english)
        advanced = strings["options"]["step"]["init"]["sections"]["advanced"]
        assert CONF_RATE_LIMIT_BUDGET in advanced["data"]
        description = advanced["data_description"][CONF_RATE_LIMIT_BUDGET]
        assert "not a Hostaway value" in description
        assert "invalid_rate_limit_budget" in strings["options"]["error"]
