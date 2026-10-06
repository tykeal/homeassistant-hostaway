# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway integration setup and teardown."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
)
from custom_components.hostaway.const import (
    CONF_CACHED_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SELECTED_LISTINGS,
    DOMAIN,
)


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Hostaway integration.
    """
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
        **overrides,  # type: ignore[arg-type]
    )


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_runtime_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates token_manager, api_client in hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        data = hass.data[DOMAIN][entry.entry_id]
        assert "token_manager" in data
        assert "api_client" in data
        assert "listings_coordinator" in data
        assert "reservations_coordinator" in data

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_loads_persisted_token(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup seeds token manager from cached_token in entry data."""
        cached = {
            "access_token": "persisted-token",
            "token_type": "Bearer",
            "expires_in": 86400,
            "issued_at": datetime.now(UTC).isoformat(),
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Hostaway (test-cli...)",
            data={
                CONF_CLIENT_ID: "test-client-id",
                CONF_CLIENT_SECRET: "test-client-secret",
                CONF_SELECTED_LISTINGS: [12345],
                CONF_CACHED_TOKEN: cached,
            },
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        tm = data["token_manager"]
        # Token was seeded - get_token returns it without network call
        token = await tm.get_token()
        assert token == "persisted-token"

    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        side_effect=HostawayConnectionError("cannot connect"),
    )
    async def test_setup_failure_raises_not_ready(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup raises ConfigEntryNotReady on connection failure."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY

    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        side_effect=HostawayAuthError("bad creds"),
    )
    async def test_auth_error_raises_auth_failed(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup raises ConfigEntryAuthFailed on auth failure."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_ERROR


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_removes_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload removes entry data from hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.entry_id not in hass.data.get(DOMAIN, {})


class TestCustomFieldRuntimeData:
    """Tests for custom-field runtime data wiring."""

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_adds_custom_field_keys(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup nests custom-field runtime state in existing entry data."""
        from custom_components.hostaway.api.custom_fields import (
            CustomFieldWriteSafetyGates,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        data = hass.data[DOMAIN][entry.entry_id]
        assert "token_manager" in data
        assert "custom_fields_coordinator" in data
        assert callable(data["custom_fields_update_unsub"])
        assert callable(data["custom_fields_initial_refresh_unsub"])
        assert isinstance(
            data["custom_field_write_safety"],
            CustomFieldWriteSafetyGates,
        )
        assert data["custom_field_write_safety"].listing_partial_put_verified is False
        assert data["custom_field_write_safety"].listing_payload_strategy is None
        assert (
            data["custom_field_write_safety"].reservation_no_clobber_verified is False
        )
        assert data["custom_field_write_safety"].reservation_payload_strategy is None
        assert data["config_entry_id"] == entry.entry_id
        assert data["account_id"] is None
        assert data["listing_custom_field_evidence"] is None
        assert data["reservation_custom_field_evidence"] is None
        assert "custom_field_write_locks" in data
        assert "custom_field_write_generations" in data
        assert entry.update_listeners

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_enables_verified_write_entry(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Production write gates require explicit account-bound options."""
        from custom_components.hostaway.api.custom_fields import (
            ListingCustomFieldEvidenceState,
            ReservationCustomFieldEvidenceState,
        )

        entry = _make_entry(
            entry_id="verified-entry",
            data={
                CONF_CLIENT_ID: "test-client-id",
                CONF_CLIENT_SECRET: "test-client-secret",
                CONF_SELECTED_LISTINGS: [12345],
                "custom_field_write_account_id": 1,
                "listing_custom_field_writes_enabled": True,
                "reservation_custom_field_writes_enabled": True,
                "reservation_custom_field_residual_risk_accepted": True,
            },
        )
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        data = hass.data[DOMAIN][entry.entry_id]
        gates = data["custom_field_write_safety"]
        assert gates.listing_partial_put_verified is True
        assert gates.listing_payload_strategy == "partial"
        assert gates.reservation_no_clobber_verified is True
        assert gates.reservation_payload_strategy == "partial"
        assert data["account_id"] == 1
        assert isinstance(
            data["listing_custom_field_evidence"],
            ListingCustomFieldEvidenceState,
        )
        assert isinstance(
            data["reservation_custom_field_evidence"],
            ReservationCustomFieldEvidenceState,
        )

    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.get_all_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.hostaway.HostawayApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_rejects_unbound_write_entry(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Write toggles without an account id keep the entry disabled."""
        entry = _make_entry(
            entry_id="other-entry",
            data={
                CONF_CLIENT_ID: "test-client-id",
                CONF_CLIENT_SECRET: "test-client-secret",
                CONF_SELECTED_LISTINGS: [12345],
                "listing_custom_field_writes_enabled": True,
                "reservation_custom_field_writes_enabled": True,
                "reservation_custom_field_residual_risk_accepted": True,
            },
        )
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        data = hass.data[DOMAIN][entry.entry_id]
        assert data["custom_field_write_safety"].listing_payload_strategy is None
        assert data["custom_field_write_safety"].reservation_payload_strategy is None
        assert data["listing_custom_field_evidence"] is None
        assert data["reservation_custom_field_evidence"] is None
