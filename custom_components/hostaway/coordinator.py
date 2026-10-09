# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Hostaway listings and reservations."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages
# aislop-ignore-file complexity/file-too-large -- one shed policy, three coordinators

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import HassJob, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.hostaway.api.custom_fields import (
    LISTING_OBJECT_TYPE,
    RESERVATION_OBJECT_TYPE,
    CustomFieldWriteGenerationRegistry,
    HostawayCustomFieldDefinition,
    fetch_custom_field_definitions,
    lookup_definition_by_id,
    resolve_var_name,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
    HostawayRateLimitShedError,
)
from custom_components.hostaway.api.models import (
    HostawayListing,
    HostawayReservation,
)
from custom_components.hostaway.api.rate_limit.context import (
    request_context,
    start_first_refresh_context,
    start_scheduled_context,
)
from custom_components.hostaway.const import (
    CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.hostaway.api.client import HostawayApiClient

_LOGGER = logging.getLogger(__name__)

_SHED_LOG_COOLDOWN_SECONDS = 300.0

_DEFINITION_RETRY_DELAYS = (30.0, 60.0, 120.0, 300.0, 600.0)


class HostawayShedAwareCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Coordinator base that drops a refresh rather than failing it.

    Hostaway charges every call against a small rolling budget, so a
    routine refresh that cannot get capacity in time is abandoned instead
    of queued. Abandoning it is a healthy outcome: the previously fetched
    data stays published, entities stay available, and the next scheduled
    cycle tries again. Subclasses implement ``_async_fetch_data`` and the
    base class owns the context, the shed signal, and the logging.
    """

    _shed_label = "coordinator"

    api_client: HostawayApiClient

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the shed bookkeeping and the wrapped coordinator.

        Args:
            *args: Positional arguments for ``DataUpdateCoordinator``.
            **kwargs: Keyword arguments for ``DataUpdateCoordinator``.
        """
        self._first_refresh_complete = False
        self._shed_count = 0
        self._shed_since_summary = 0
        self._last_shed_log: float | None = None
        super().__init__(*args, **kwargs)

    @property
    def shed_count(self) -> int:
        """Return how many refreshes this coordinator has dropped.

        Returns:
            The lifetime shed count.
        """
        return self._shed_count

    async def _async_fetch_data(self) -> DataT:
        """Fetch this coordinator's data.

        Returns:
            The freshly fetched data.

        Raises:
            NotImplementedError: Always; subclasses must override this.
        """
        raise NotImplementedError

    async def _async_update_data(self) -> DataT:
        """Run one refresh cycle under a rate-limit context.

        Returns:
            The freshly fetched data, or the previously published data
            when the cycle was shed.
        """
        ctx = (
            start_scheduled_context()
            if self._first_refresh_complete
            else start_first_refresh_context()
        )
        try:
            with request_context(ctx):
                data = await self._async_fetch_data()
        except HostawayRateLimitShedError as exc:
            self._note_shed(exc)
            return cast(DataT, self.data)
        self._first_refresh_complete = True
        return data

    def _note_shed(self, exc: HostawayRateLimitShedError) -> None:
        """Record and report one shed refresh cycle.

        The log names the coordinator by a fixed label rather than by
        ``self.name``, which embeds the configured client id.

        Args:
            exc: The shed signal that ended the cycle.
        """
        limiter = getattr(self.api_client, "limiter", None)
        if limiter is not None:
            limiter.note_shed()
        self._shed_count += 1
        self._shed_since_summary += 1
        now = time.monotonic()
        last = self._last_shed_log
        due = last is None or now - last >= _SHED_LOG_COOLDOWN_SECONDS
        message = (
            "Hostaway %s refresh dropped after waiting %.1fs for rate limit "
            "capacity; the previously fetched data is still published "
            "(%d dropped since the last report, %d in total)"
        )
        args = (
            self._shed_label,
            exc.waited,
            self._shed_since_summary,
            self._shed_count,
        )
        if due:
            _LOGGER.warning(message, *args)
            self._last_shed_log = now
            self._shed_since_summary = 0
        else:
            _LOGGER.debug(message, *args)
        self._on_shed()

    def _on_shed(self) -> None:
        """React to a shed cycle beyond logging. Does nothing by default."""


class HostawayCustomFieldsCoordinator(
    HostawayShedAwareCoordinator[list[HostawayCustomFieldDefinition]],
):
    """Coordinator that fetches Hostaway custom-field definitions."""

    _shed_label = "custom fields"

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the custom-field definitions coordinator."""
        self.api_client = api_client
        self.last_refresh_succeeded = False
        self.last_refresh_error: Exception | None = None
        self._definitions_loaded = False
        self._definition_retries = 0
        self._definition_retry_unsub: Callable[[], None] | None = None
        self._definitions_shutdown = False
        interval_minutes = entry.options.get(
            CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
            DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_custom_fields_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )
        self.data = []

    async def _async_fetch_data(self) -> list[HostawayCustomFieldDefinition]:
        """Fetch and cache custom-field definitions.

        Returns:
            The definitions Hostaway reports for this account.

        Raises:
            ConfigEntryAuthFailed: On authentication failure.
            UpdateFailed: On any other fetch failure.
        """
        try:
            definitions = await fetch_custom_field_definitions(self.api_client._request)
        except HostawayRateLimitShedError:
            raise
        except HostawayAuthError as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise UpdateFailed(
                f"Failed to fetch custom field definitions: {exc}",
            ) from exc
        except Exception as exc:
            self.last_refresh_succeeded = False
            self.last_refresh_error = exc
            raise UpdateFailed(
                f"Unexpected error fetching custom field definitions: {exc}",
            ) from exc
        self.last_refresh_succeeded = True
        self.last_refresh_error = None
        self._definitions_loaded = True
        self._cancel_definition_retry()
        return definitions

    @property
    def definitions_loaded(self) -> bool:
        """Return whether a fetch has ever delivered definitions.

        The initialiser seeds ``data`` with an empty list so callers never
        meet ``None``, which means an empty list on its own cannot tell a
        loaded account with no custom fields apart from one that has not
        answered yet. This flag can.

        Returns:
            ``True`` once a fetch has completed successfully.
        """
        return self._definitions_loaded

    async def async_refresh_retaining_stale(self) -> None:
        """Refresh definitions without clearing stale cached data on failure.

        ``DataUpdateCoordinator`` already leaves ``data`` alone when a
        refresh raises, so there is nothing to put back; snapshotting it
        here would instead let a slow failing refresh roll back a faster
        successful one.
        """
        await self.async_refresh()
        if not self._definitions_loaded:
            self._schedule_definition_retry()

    def _on_shed(self) -> None:
        """Keep trying when definitions have never loaded."""
        if not self._definitions_loaded:
            self._schedule_definition_retry()

    def _schedule_definition_retry(self) -> None:
        """Arm one bounded retry towards a first successful load.

        Setup deliberately does not wait for definitions, so a first
        attempt that loses to the rate limit would otherwise leave the
        integration without them until the long scan interval elapses.
        The retries are bounded: a persistent failure is an operator
        problem, not something to keep hammering.
        """
        if self._definitions_shutdown or self._definition_retry_unsub is not None:
            return
        if self._definition_retries >= len(_DEFINITION_RETRY_DELAYS):
            return
        delay = _DEFINITION_RETRY_DELAYS[self._definition_retries]
        self._definition_retries += 1
        _LOGGER.debug(
            "Hostaway custom field definitions are not loaded yet; "
            "retrying in %.0fs (attempt %d of %d)",
            delay,
            self._definition_retries,
            len(_DEFINITION_RETRY_DELAYS),
        )

        @callback
        def _retry(_now: object) -> None:
            """Run the retry on the event loop.

            Args:
                _now: The fire time, which this callback ignores.
            """
            self._definition_retry_unsub = None
            # A retry may wait out a whole first-refresh deadline, so it
            # belongs to the entry: unloading has to be able to cancel it
            # rather than wait for it.
            self.config_entry.async_create_background_task(
                self.hass,
                self.async_refresh_retaining_stale(),
                "hostaway custom field definitions retry",
            )

        self._definition_retry_unsub = async_call_later(
            self.hass,
            delay,
            HassJob(
                _retry,
                "hostaway custom field definitions retry",
                cancel_on_shutdown=True,
            ),
        )

    def _cancel_definition_retry(self) -> None:
        """Cancel a pending definitions retry, if one is armed."""
        if self._definition_retry_unsub is not None:
            self._definition_retry_unsub()
            self._definition_retry_unsub = None

    async def async_shutdown(self) -> None:
        """Cancel pending retries and shut the coordinator down."""
        self._definitions_shutdown = True
        self._cancel_definition_retry()
        await super().async_shutdown()

    @property
    def by_id(self) -> dict[int, HostawayCustomFieldDefinition]:
        """Return cached definitions keyed by id."""
        return {
            definition.custom_field_id: definition for definition in self.data or []
        }

    def get_definitions_for_object_type(
        self, object_type: str
    ) -> list[HostawayCustomFieldDefinition]:
        """Return cached definitions for one Hostaway object type."""
        return [
            definition
            for definition in self.data or []
            if definition.object_type == object_type
        ]

    def get_definition(
        self, custom_field_id: int, object_type: str
    ) -> HostawayCustomFieldDefinition | None:
        """Return a cached definition by id and object type."""
        for definition in self.get_definitions_for_object_type(object_type):
            if definition.custom_field_id == custom_field_id:
                return definition
        return None

    def require_definition(
        self, custom_field_id: int, object_type: str
    ) -> HostawayCustomFieldDefinition:
        """Return a cached definition or raise a definition error."""
        return lookup_definition_by_id(self.data or [], custom_field_id, object_type)

    def resolve_var_name(
        self, var_name: str, object_type: str
    ) -> HostawayCustomFieldDefinition:
        """Resolve a varName against cached definitions for one object type."""
        return resolve_var_name(self.data or [], var_name, object_type)


class HostawayListingsCoordinator(
    HostawayShedAwareCoordinator[dict[int, HostawayListing]],
):
    """Coordinator that fetches Hostaway listings periodically.

    Wraps the API client's ``get_all_listings()`` call inside a
    ``DataUpdateCoordinator`` so sensor entities receive automatic
    updates. Only listings in ``CONF_SELECTED_LISTINGS`` are kept.

    Attributes:
        api_client: The Hostaway API client instance.
        config_entry: The integration config entry.
    """

    _shed_label = "listings"

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the listings coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Hostaway API client for fetching listings.
        """
        self._hass = hass
        self.api_client = api_client
        interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_listings_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_fetch_data(
        self,
    ) -> dict[int, HostawayListing]:
        """Fetch listings filtered by selected IDs.

        Returns:
            Dictionary mapping listing ID to HostawayListing.

        Raises:
            UpdateFailed: On any Hostaway API error.
            ConfigEntryAuthFailed: On authentication failure.
        """
        selected = set(self.config_entry.data.get(CONF_SELECTED_LISTINGS, []))
        generations = _write_generations(self._hass, self.config_entry.entry_id)
        captured = _capture_generations(
            generations,
            {(LISTING_OBJECT_TYPE, listing_id) for listing_id in selected},
        )
        try:
            listings = await self.api_client.get_all_listings()
        except HostawayAuthError as exc:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise UpdateFailed(
                f"Failed to fetch listings: {exc}",
            ) from exc
        result = {listing.id: listing for listing in listings if listing.id in selected}
        current = self.data or {}
        for listing_id in selected:
            key = (LISTING_OBJECT_TYPE, listing_id)
            if (
                _generation_changed(generations, key, captured)
                and listing_id in current
            ):
                result[listing_id] = current[listing_id]
        return result


class HostawayReservationsCoordinator(
    HostawayShedAwareCoordinator[dict[int, list[HostawayReservation]]],
):
    """Coordinator that fetches Hostaway reservations periodically.

    Fetches reservations per selected listing and sorts each
    listing's reservations by check-in date.

    Attributes:
        api_client: The Hostaway API client instance.
        config_entry: The integration config entry.
    """

    _shed_label = "reservations"

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: HostawayApiClient,
    ) -> None:
        """Initialize the reservations coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Hostaway API client for fetching reservations.
        """
        self._hass = hass
        self.api_client = api_client
        interval_minutes = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_reservations_{entry.unique_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_fetch_data(
        self,
    ) -> dict[int, list[HostawayReservation]]:
        """Fetch reservations for each selected listing sequentially.

        Sequential fetching respects Hostaway API rate limits
        (FR-005) as each call may involve pagination.

        Returns:
            Dictionary mapping listing ID to sorted reservation list.

        Raises:
            UpdateFailed: On any Hostaway API error.
            ConfigEntryAuthFailed: On authentication failure.
        """
        selected = self.config_entry.data.get(CONF_SELECTED_LISTINGS, [])
        current_at_start = self.data or {}
        current_by_id = {
            reservation.id: reservation
            for reservations in current_at_start.values()
            for reservation in reservations
        }
        generations = _write_generations(self._hass, self.config_entry.entry_id)
        captured = _capture_generations(
            generations,
            {
                (RESERVATION_OBJECT_TYPE, reservation_id)
                for reservation_id in current_by_id
            },
        )
        result: dict[int, list[HostawayReservation]] = {}
        try:
            for listing_id in selected:
                reservations = await self.api_client.get_all_reservations(listing_id)
                result[listing_id] = sorted(reservations, key=lambda r: r.check_in)
        except HostawayAuthError as exc:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise UpdateFailed(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        latest = self.data or {}
        latest_by_id = {
            reservation.id: reservation
            for reservations_for_listing in latest.values()
            for reservation in reservations_for_listing
        }
        return {
            listing_id: _preserve_changed_reservations(
                listing_id,
                reservations,
                latest_by_id,
                generations,
                captured,
            )
            for listing_id, reservations in result.items()
        }


def _write_generations(
    hass: HomeAssistant,
    entry_id: str,
) -> CustomFieldWriteGenerationRegistry | None:
    """Return the write-generation registry for one config entry."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return None
    registry = entry_data.get("custom_field_write_generations")
    if isinstance(registry, CustomFieldWriteGenerationRegistry):
        return registry
    return None


def _capture_generations(
    registry: CustomFieldWriteGenerationRegistry | None,
    keys: set[tuple[str, int]],
) -> dict[tuple[str, int], int]:
    """Return generation values captured before a coordinator fetch."""
    if registry is None:
        return {}
    return {key: registry.current(*key) for key in keys}


def _generation_changed(
    registry: CustomFieldWriteGenerationRegistry | None,
    key: tuple[str, int],
    captured: dict[tuple[str, int], int],
) -> bool:
    """Return whether a target changed during an in-flight refresh."""
    if registry is None:
        return False
    return registry.current(*key) != captured.get(key, 0)


def _preserve_changed_reservations(
    listing_id: int,
    fetched: list[HostawayReservation],
    current_by_id: dict[int, HostawayReservation],
    generations: CustomFieldWriteGenerationRegistry | None,
    captured: dict[tuple[str, int], int],
) -> list[HostawayReservation]:
    """Preserve reservations patched by writes during an in-flight refresh."""
    result: list[HostawayReservation] = []
    seen: set[int] = set()
    for reservation in fetched:
        key = (RESERVATION_OBJECT_TYPE, reservation.id)
        if _generation_changed(generations, key, captured):
            result.append(current_by_id.get(reservation.id, reservation))
        else:
            result.append(reservation)
        seen.add(reservation.id)
    for reservation_id, reservation in current_by_id.items():
        key = (RESERVATION_OBJECT_TYPE, reservation_id)
        if (
            reservation.listing_id == listing_id
            and reservation_id not in seen
            and _generation_changed(generations, key, captured)
        ):
            result.append(reservation)
    return sorted(result, key=lambda reservation: reservation.check_in)
