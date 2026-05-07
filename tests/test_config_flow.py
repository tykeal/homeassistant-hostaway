# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway config flow and options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

VALID_INPUT = {
    CONF_CLIENT_ID: "test-client-id",
    CONF_CLIENT_SECRET: "test-client-secret",
}


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hostaway (test-cli...)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
            CONF_SELECTED_LISTINGS: [12345],
        },
        unique_id="test-client-id",
        options={
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_RESERVATION_SCAN_INTERVAL: DEFAULT_RESERVATION_SCAN_INTERVAL,
        },
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
