# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config flow for the Hostaway integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayAuthError,
    HostawayConnectionError,
)
from custom_components.hostaway.api.models import HostawayListing
from custom_components.hostaway.const import (
    CONF_CACHED_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)


async def _validate_credentials(
    hass: HomeAssistant,
    client_id: str,
    client_secret: str,
) -> None:
    """Validate Hostaway credentials via test_connection.

    Args:
        hass: Home Assistant instance.
        client_id: Hostaway API client ID.
        client_secret: Hostaway API client secret.

    Raises:
        HostawayAuthError: If credentials are invalid.
        HostawayConnectionError: If the API is unreachable.
    """
    http_client = get_async_client(hass)
    token_manager = HostawayTokenManager(
        client_id=client_id,
        client_secret=client_secret,
        http_client=http_client,
    )
    api_client = HostawayApiClient(
        token_manager=token_manager,
        http_client=http_client,
    )
    await api_client.test_connection()


async def _fetch_listings(
    hass: HomeAssistant,
    client_id: str,
    client_secret: str,
) -> list[HostawayListing]:
    """Fetch all listings from the Hostaway API.

    Args:
        hass: Home Assistant instance.
        client_id: Hostaway API client ID.
        client_secret: Hostaway API client secret.

    Returns:
        List of HostawayListing objects.
    """
    http_client = get_async_client(hass)
    token_manager = HostawayTokenManager(
        client_id=client_id,
        client_secret=client_secret,
        http_client=http_client,
    )
    api_client = HostawayApiClient(
        token_manager=token_manager,
        http_client=http_client,
    )
    return await api_client.get_all_listings()


class HostawayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Hostaway integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        super().__init__()
        self._client_id: str = ""
        self._client_secret: str = ""
        self._listings: list[HostawayListing] = []
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> HostawayOptionsFlow:
        """Return the options flow handler.

        Args:
            config_entry: The config entry.

        Returns:
            The options flow handler instance.
        """
        return HostawayOptionsFlow(config_entry)

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Handle reauth initiation.

        Args:
            entry_data: Existing config entry data.

        Returns:
            Config flow result showing the reauth confirm form.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"],
        )
        self._client_id = entry_data.get(CONF_CLIENT_ID, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reauth confirmation step.

        Args:
            user_input: User-provided form data, or None for initial
                form display.

        Returns:
            Config flow result (form, error, or abort on success).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id = self._client_id
            client_secret = user_input[CONF_CLIENT_SECRET]

            try:
                await _validate_credentials(self.hass, client_id, client_secret)
            except HostawayAuthError:
                errors["base"] = "invalid_auth"
            except HostawayConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                if self._reauth_entry is not None:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data={
                            **self._reauth_entry.data,
                            CONF_CLIENT_SECRET: client_secret,
                            CONF_CACHED_TOKEN: None,
                        },
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id,
                    )
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial user setup step.

        Args:
            user_input: User-provided form data, or None for initial
                form display.

        Returns:
            Config flow result (form, error, or next step).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID]
            self._client_secret = user_input[CONF_CLIENT_SECRET]

            await self.async_set_unique_id(self._client_id)
            self._abort_if_unique_id_configured()

            try:
                await _validate_credentials(
                    self.hass,
                    self._client_id,
                    self._client_secret,
                )
            except HostawayAuthError:
                errors["base"] = "invalid_auth"
            except HostawayConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                return await self.async_step_listings()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_listings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the listing selection step.

        Args:
            user_input: User-provided form data, or None for initial
                form display.

        Returns:
            Config flow result (form or entry creation).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_LISTINGS, [])
            if not selected:
                errors["base"] = "no_listings_selected"
            else:
                return self.async_create_entry(
                    title=f"Hostaway ({self._client_id[:8]}...)",
                    data={
                        CONF_CLIENT_ID: self._client_id,
                        CONF_CLIENT_SECRET: self._client_secret,
                        CONF_SELECTED_LISTINGS: [int(lid) for lid in selected],
                    },
                )

        # Fetch listings if not already cached
        if not self._listings:
            try:
                self._listings = await _fetch_listings(
                    self.hass,
                    self._client_id,
                    self._client_secret,
                )
            except HostawayAuthError:
                _LOGGER.warning("Auth failed fetching listings")
                return self.async_abort(reason="invalid_auth")
            except HostawayConnectionError:
                _LOGGER.warning("Connection failed fetching listings")
                return self.async_abort(reason="cannot_connect")
            except Exception:
                _LOGGER.exception("Failed to fetch listings")
                return self.async_abort(reason="unknown")

        # Filter to active listings only
        active_listings = [lst for lst in self._listings if lst.status == "active"]

        if not active_listings:
            return self.async_abort(reason="no_active_listings")

        options: list[SelectOptionDict] = []
        for listing in active_listings:
            label = f"{listing.name} (ID: {listing.id})"
            options.append(
                SelectOptionDict(value=str(listing.id), label=label),
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SELECTED_LISTINGS): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="listings",
            data_schema=schema,
            errors=errors,
        )


class HostawayOptionsFlow(OptionsFlow):
    """Handle options flow for the Hostaway integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow.

        Args:
            config_entry: The config entry being configured.
        """
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the polling intervals step.

        Args:
            user_input: User-provided form data, or None for initial
                display.

        Returns:
            Config flow result (form or entry creation).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            scan = user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            res_scan = user_input.get(
                CONF_RESERVATION_SCAN_INTERVAL,
                DEFAULT_RESERVATION_SCAN_INTERVAL,
            )

            if scan < MIN_SCAN_INTERVAL or res_scan < MIN_SCAN_INTERVAL:
                errors["base"] = "invalid_scan_interval"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SCAN_INTERVAL: scan,
                        CONF_RESERVATION_SCAN_INTERVAL: res_scan,
                    },
                )

        current_scan = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        current_res_scan = self._config_entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_scan,
                ): vol.Coerce(int),
                vol.Required(
                    CONF_RESERVATION_SCAN_INTERVAL,
                    default=current_res_scan,
                ): vol.Coerce(int),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
