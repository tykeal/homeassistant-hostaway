# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway integration for Home Assistant.

Provides OAuth 2.0 authenticated access to the Hostaway API
for property management automation. Manages the API client
lifecycle and token persistence across HA restarts.
"""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.hostaway.api.auth import HostawayTokenManager
from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.custom_fields import (
    CustomFieldWriteGenerationRegistry,
    CustomFieldWriteLockRegistry,
    CustomFieldWriteSafetyGates,
    ListingCustomFieldEvidenceState,
    ReservationCustomFieldEvidenceState,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
)
from custom_components.hostaway.api.models import AccessToken
from custom_components.hostaway.api.rate_limit import (
    request_context,
    start_interactive_context,
)
from custom_components.hostaway.const import (
    CONF_CACHED_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID,
    CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED,
    CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED,
    CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID,
    CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED,
    DOMAIN,
    PLATFORMS,
)
from custom_components.hostaway.coordinator import (
    HostawayCustomFieldsCoordinator,
    HostawayListingsCoordinator,
    HostawayReservationsCoordinator,
)
from custom_components.hostaway.rate_limit_registry import (
    register_entry,
    release_entry,
    schedule_release,
)

_LOGGER = logging.getLogger(__name__)


def _entry_custom_field_write_account_id(entry: ConfigEntry) -> int | None:
    """Return the explicitly configured custom-field write account id."""
    value = entry.options.get(
        CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID,
        entry.data.get(CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID),
    )
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _custom_field_write_safety(
    entry: ConfigEntry,
) -> tuple[
    CustomFieldWriteSafetyGates,
    ListingCustomFieldEvidenceState | None,
    ReservationCustomFieldEvidenceState | None,
    int | None,
]:
    """Build explicit account-bound production custom-field write gates."""
    account_id = _entry_custom_field_write_account_id(entry)
    options = {**entry.data, **entry.options}
    has_account = account_id is not None
    listing_enabled = (
        has_account and options.get(CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED) is True
    )
    reservation_enabled = (
        has_account
        and options.get(CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED) is True
        and options.get(CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED) is True
        and options.get(CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID)
        == account_id
    )
    gates = CustomFieldWriteSafetyGates()
    listing_evidence = None
    reservation_evidence = None
    if listing_enabled:
        object.__setattr__(gates, "listing_partial_put_verified", True)
        object.__setattr__(gates, "listing_payload_strategy", "partial")
        listing_evidence = ListingCustomFieldEvidenceState(
            account_id=account_id,
            config_entry_id=entry.entry_id,
            partial_put_verified=True,
            payload_strategy="partial",
        )
    if reservation_enabled:
        object.__setattr__(gates, "reservation_no_clobber_verified", True)
        object.__setattr__(gates, "reservation_payload_strategy", "partial")
        reservation_evidence = ReservationCustomFieldEvidenceState(
            account_id=account_id,
            config_entry_id=entry.entry_id,
            custom_field_values_round_trip_verified=True,
            payload_strategy="partial",
        )
    return gates, listing_evidence, reservation_evidence, account_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Hostaway from a config entry.

    Home Assistant never unloads an entry that failed to load, so a setup
    that raises would otherwise leave its claim on the account budget
    forever — and a failed entry asking for a low budget would throttle
    the entry that did load. A failure therefore starts the same grace
    period an unload does.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to set up.

    Returns:
        True if setup succeeded.
    """
    try:
        loaded = await _async_setup_entry(hass, entry)
    except Exception:
        schedule_release(hass, entry)
        raise
    if not loaded:
        schedule_release(hass, entry)
    return loaded


async def _async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Hostaway from a config entry.

    Creates the HTTP client, token manager, API client, and
    coordinators. Seeds persisted token if available and validates
    connectivity.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to set up.

    Returns:
        True if setup succeeded.

    Raises:
        ConfigEntryNotReady: If the API is unreachable.
    """
    http_client = get_async_client(hass)
    # One limiter per account, created before anything can send: the
    # server's counters are per account and per IP, not per config entry.
    limiter = register_entry(hass, entry)

    token_manager = HostawayTokenManager(
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        http_client=http_client,
        limiter=limiter,
    )

    # Restore persisted token if available
    cached = entry.data.get(CONF_CACHED_TOKEN)
    if cached is not None:
        try:
            token = AccessToken.from_dict(cached)
            token_manager.seed_token(token)
        except (KeyError, ValueError, TypeError) as exc:
            _LOGGER.warning("Failed to restore cached token: %s", exc)

    api_client = HostawayApiClient(
        token_manager=token_manager,
        http_client=http_client,
        limiter=limiter,
    )

    try:
        with request_context(start_interactive_context()):
            await api_client.test_connection()
    except HostawayAuthError as exc:
        raise ConfigEntryAuthFailed(
            f"Invalid Hostaway credentials: {exc}",
        ) from exc
    except (HostawayConnectionError, HostawayRateLimitError) as exc:
        raise ConfigEntryNotReady(
            f"Unable to connect to Hostaway: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise ConfigEntryNotReady(
            f"Hostaway API error during setup: {exc}",
        ) from exc

    listings_coordinator = HostawayListingsCoordinator(hass, entry, api_client)
    reservations_coordinator = HostawayReservationsCoordinator(hass, entry, api_client)
    custom_fields_coordinator = HostawayCustomFieldsCoordinator(hass, entry, api_client)

    # Perform initial data fetch
    await listings_coordinator.async_config_entry_first_refresh()
    await reservations_coordinator.async_config_entry_first_refresh()

    def _custom_fields_listener() -> None:
        """Keep the definitions coordinator interval scheduled."""

    custom_fields_update_unsub = custom_fields_coordinator.async_add_listener(
        _custom_fields_listener,
    )

    def _initial_custom_fields_refresh(_now: object) -> None:
        """Start the first definitions refresh without blocking setup."""
        hass.loop.call_soon_threadsafe(
            lambda: hass.async_create_task(
                custom_fields_coordinator.async_refresh_retaining_stale()
            )
        )

    custom_fields_initial_refresh_unsub = async_call_later(
        hass,
        1,
        _initial_custom_fields_refresh,
    )
    (
        write_safety,
        listing_evidence,
        reservation_evidence,
        account_id,
    ) = _custom_field_write_safety(entry)

    hass.data.setdefault(DOMAIN, {})
    entry_data = hass.data[DOMAIN].setdefault(entry.entry_id, {})
    entry_data.update(
        {
            "token_manager": token_manager,
            "api_client": api_client,
            "listings_coordinator": listings_coordinator,
            "reservations_coordinator": reservations_coordinator,
            "custom_fields_coordinator": custom_fields_coordinator,
            "custom_fields_update_unsub": custom_fields_update_unsub,
            "custom_fields_initial_refresh_unsub": custom_fields_initial_refresh_unsub,
            "config_entry_id": entry.entry_id,
            "account_id": account_id,
            "custom_field_write_safety": write_safety,
            "listing_custom_field_evidence": listing_evidence,
            "reservation_custom_field_evidence": reservation_evidence,
            "custom_field_write_locks": CustomFieldWriteLockRegistry(),
            "custom_field_write_generations": CustomFieldWriteGenerationRegistry(),
            "limiter": limiter,
        }
    )

    # Register services (idempotent, safe for multi-entry)
    from custom_components.hostaway.services import async_setup_services

    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a Hostaway config entry.

    Removes runtime data and unloads platforms.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to unload.

    Returns:
        True if unload succeeded.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        schedule_release(hass, entry)

    if unload_ok and DOMAIN in hass.data:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            custom_fields_update_unsub = data.get("custom_fields_update_unsub")
            if callable(custom_fields_update_unsub):
                custom_fields_update_unsub()
            custom_fields_initial_refresh_unsub = data.get(
                "custom_fields_initial_refresh_unsub"
            )
            if callable(custom_fields_initial_refresh_unsub):
                custom_fields_initial_refresh_unsub()
            await data["listings_coordinator"].async_shutdown()
            await data["reservations_coordinator"].async_shutdown()
            await data["custom_fields_coordinator"].async_shutdown()

        # Remove services when no entries remain
        if not hass.data.get(DOMAIN):
            from custom_components.hostaway.services import async_unregister_services

            async_unregister_services(hass)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop an entry's claim on its account limiter.

    Removal releases the budget at once. An unload only starts a grace
    window, because it may be the first half of a reload and giving the
    budget back in between would hand the next ten seconds more
    admissions than the account is entitled to.

    Args:
        hass: Home Assistant instance.
        entry: The entry being removed.
    """
    release_entry(hass, entry)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after option changes."""
    await hass.config_entries.async_reload(entry.entry_id)
