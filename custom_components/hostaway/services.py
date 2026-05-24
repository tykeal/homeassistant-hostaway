# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for the Hostaway integration."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayReservationLockedError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import HostawayListing, HostawayReservation
from custom_components.hostaway.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_LOCKED_LOG_COOLDOWN_SECONDS = 3600
_LOCKED_RESERVATION_LOG_STATE: dict[int, float] = {}

_TASK_STATUS_VALUES = (
    "pending",
    "confirmed",
    "inProgress",
    "completed",
    "cancelled",
)


def _prune_locked_state(now: float) -> None:
    """Drop log-state entries older than twice the cooldown.

    Keeps the in-process state bounded for long-lived HA instances
    that may see many distinct reservation IDs over time. Pruning
    is opportunistic — invoked when a new WARNING is about to be
    emitted — and uses a 2x cooldown threshold so entries are
    retained at least long enough to suppress repeats but cannot
    grow without bound.

    Args:
        now: Current ``time.monotonic()`` value.
    """
    stale_threshold = 2 * _LOCKED_LOG_COOLDOWN_SECONDS
    stale = [
        rid
        for rid, ts in _LOCKED_RESERVATION_LOG_STATE.items()
        if (now - ts) >= stale_threshold
    ]
    for rid in stale:
        del _LOCKED_RESERVATION_LOG_STATE[rid]


def _log_locked_reservation(
    reservation_id: int,
    exc: HostawayReservationLockedError,
) -> None:
    """Log a locked-reservation event with a per-reservation cooldown.

    The first failure for a given ``reservation_id`` emits a WARNING
    that includes the exception message (which already carries the
    HTTP method, path, status, and a redacted body snippet from the
    client). Subsequent failures for the same reservation within
    ``_LOCKED_LOG_COOLDOWN_SECONDS`` are demoted to DEBUG so the HA
    log is not flooded by a repeating automation (e.g., the
    ~2-minute door-code refresh loop).

    State is module-level and best-effort: a HA restart resets it,
    which is acceptable. Uses :func:`time.monotonic` so wall-clock
    changes cannot break the cooldown. The state dict is pruned
    of entries older than twice the cooldown on each WARNING
    emission so it stays bounded on long-lived instances.

    Args:
        reservation_id: Hostaway reservation ID that was rejected.
        exc: The raised locked-reservation exception. Its string
            form is appended to the WARNING for diagnostic context.
    """
    now = time.monotonic()
    last = _LOCKED_RESERVATION_LOG_STATE.get(reservation_id)
    if last is None or (now - last) >= _LOCKED_LOG_COOLDOWN_SECONDS:
        _prune_locked_state(now)
        _LOCKED_RESERVATION_LOG_STATE[reservation_id] = now
        _LOGGER.warning(
            "Skipping doorCode update for reservation %s: Hostaway "
            "refused the request as non-writable (likely "
            "channel-managed or in conflict). %s",
            reservation_id,
            exc,
        )
        return
    _LOGGER.debug(
        "Locked-reservation update suppressed (rate-limited) for reservation %s",
        reservation_id,
    )


def _positive_int(value: Any) -> int:
    """Validate and coerce a value to a positive integer.

    Rejects booleans and floats with fractional parts to
    prevent silent coercion of unintended values.

    Args:
        value: The value to validate.

    Returns:
        The validated positive integer.

    Raises:
        vol.Invalid: If the value is not a valid positive integer.
    """
    if isinstance(value, bool):
        raise vol.Invalid("boolean is not a valid integer")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise vol.Invalid("expected a finite number")
        if value != int(value):
            raise vol.Invalid("float with fractional part is not valid")
        value = int(value)
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as exc:
            raise vol.Invalid("expected an integer") from exc
    if value <= 0:
        raise vol.Invalid("must be a positive integer")
    result: int = value
    return result


def _non_empty_string(value: Any) -> str:
    """Validate a non-empty string, stripping whitespace.

    Args:
        value: The value to validate.

    Returns:
        The stripped string.

    Raises:
        vol.Invalid: If the value is not a non-empty string.
    """
    if not isinstance(value, str):
        raise vol.Invalid("expected a string")
    stripped = value.strip()
    if not stripped:
        raise vol.Invalid("must be a non-empty string")
    return stripped


def _strict_string(value: Any) -> str:
    """Validate that a value is a string without coercion.

    Args:
        value: The value to validate.

    Returns:
        The original string value.

    Raises:
        vol.Invalid: If the value is not a string.
    """
    if not isinstance(value, str):
        raise vol.Invalid("expected a string")
    return value


def _positive_int_list(value: Any) -> list[int]:
    """Validate a list of positive integers without container coercion.

    Args:
        value: The value to validate.

    Returns:
        The validated list of positive integers.

    Raises:
        vol.Invalid: If the value is not a list of positive integers.
    """
    if not isinstance(value, list):
        raise vol.Invalid("expected a list")
    return [_positive_int(item) for item in value]


def _is_user_correctable_task_error(exc: HostawayResponseError) -> bool:
    """Return whether a task API error likely reflects invalid user input.

    Args:
        exc: The response error raised by the API client.

    Returns:
        True when the message looks like a validation or field error that
        the caller can correct.
    """
    message = str(exc).lower()
    if "not found" in message:
        return False
    return any(
        marker in message
        for marker in (
            "validation",
            "invalid",
            "required",
            "missing field",
            "field error",
        )
    )


SERVICE_SET_DOOR_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("reservation_id"): _positive_int,
        vol.Required("door_code"): _non_empty_string,
        vol.Optional("door_code_vendor"): vol.Maybe(_strict_string),
        vol.Optional("door_code_instruction"): vol.Maybe(
            _strict_string,
        ),
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_RESERVATIONS_SCHEMA = vol.Schema(
    {
        vol.Required("listing_id"): _positive_int,
        vol.Optional("force_refresh"): vol.Boolean(),
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_FIND_RESERVATION_SCHEMA = vol.Schema(
    {
        vol.Required("guest_name"): _non_empty_string,
        vol.Required("check_in"): _non_empty_string,
        vol.Required("check_out"): _non_empty_string,
        vol.Optional("listing_id"): _positive_int,
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_CREATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("title"): _non_empty_string,
        vol.Optional("description"): _strict_string,
        vol.Optional("listing_id"): _positive_int,
        vol.Optional("listing_name"): _non_empty_string,
        vol.Optional("reservation_id"): _positive_int,
        vol.Optional("status"): vol.In(_TASK_STATUS_VALUES),
        vol.Optional("priority"): _positive_int,
        vol.Optional("assignee_user_id"): _positive_int,
        vol.Optional("can_be_picked_by_group_id"): _positive_int,
        vol.Optional("supervisor_user_id"): _positive_int,
        vol.Optional("categories_map"): _positive_int_list,
        vol.Optional("can_start_from"): _non_empty_string,
        vol.Optional("should_end_by"): _non_empty_string,
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_UPDATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("task_id"): _positive_int,
        vol.Optional("title"): _non_empty_string,
        vol.Optional("description"): _strict_string,
        vol.Optional("listing_id"): _positive_int,
        vol.Optional("listing_name"): _non_empty_string,
        vol.Optional("reservation_id"): _positive_int,
        vol.Optional("status"): vol.In(_TASK_STATUS_VALUES),
        vol.Optional("priority"): _positive_int,
        vol.Optional("assignee_user_id"): _positive_int,
        vol.Optional("can_be_picked_by_group_id"): _positive_int,
        vol.Optional("supervisor_user_id"): _positive_int,
        vol.Optional("categories_map"): _positive_int_list,
        vol.Optional("can_start_from"): _non_empty_string,
        vol.Optional("should_end_by"): _non_empty_string,
        vol.Optional("resolution_note"): _strict_string,
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_DELETE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("task_id"): _positive_int,
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_TASKS_SCHEMA = vol.Schema(
    {
        vol.Optional("listing_id"): _positive_int,
        vol.Optional("listing_name"): _non_empty_string,
        vol.Optional("reservation_id"): _positive_int,
        vol.Optional("status"): vol.In(_TASK_STATUS_VALUES),
        vol.Optional("can_start_from_start"): _non_empty_string,
        vol.Optional("can_start_from_end"): _non_empty_string,
        vol.Optional("config_entry_id"): _strict_string,
    }
)


def _resolve_entry_data(
    hass: HomeAssistant,
    call_data: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the correct config entry data for a service call.

    When multiple entries exist, the caller must provide
    ``config_entry_id`` to disambiguate.

    Args:
        hass: Home Assistant instance.
        call_data: Service call data dictionary.

    Returns:
        The runtime data dict for the resolved config entry.

    Raises:
        ServiceValidationError: If the entry cannot be resolved.
    """
    entries: dict[str, Any] = hass.data.get(DOMAIN, {})
    config_entry_id = call_data.get("config_entry_id")

    if config_entry_id:
        if config_entry_id not in entries:
            raise ServiceValidationError(
                f"Config entry {config_entry_id} not found",
            )
        result: dict[str, Any] = entries[config_entry_id]
        return result

    if len(entries) == 0:
        raise ServiceValidationError(
            "No Hostaway config entries are loaded",
        )

    if len(entries) == 1:
        first: dict[str, Any] = next(iter(entries.values()))
        return first

    raise ServiceValidationError(
        "config_entry_id required when multiple entries exist",
    )


def _get_listing_name_index(listings_coordinator: Any) -> dict[str, int]:
    """Return a cached internal-name index for listing lookups.

    Args:
        listings_coordinator: The listings coordinator for the entry.

    Returns:
        Mapping of ``internal_name`` to Hostaway listing ID.

    Raises:
        ServiceValidationError: If listings data is unavailable.
    """
    listings: dict[int, HostawayListing] | None = listings_coordinator.data
    if listings is None:
        raise ServiceValidationError(
            "Listings data not available for name resolution",
        )

    cache_key = id(listings)
    cached_key = getattr(
        listings_coordinator,
        "_hostaway_listing_name_index_key",
        None,
    )
    cached_index = getattr(
        listings_coordinator,
        "_hostaway_listing_name_index",
        None,
    )
    if cache_key != cached_key or not isinstance(cached_index, dict):
        cached_index = {
            listing.internal_name: listing.id
            for listing in listings.values()
            if listing.internal_name is not None
        }
        listings_coordinator._hostaway_listing_name_index_key = cache_key
        listings_coordinator._hostaway_listing_name_index = cached_index

    result: dict[str, int] = cached_index
    return result


def _resolve_listing_id(
    call_data: dict[str, Any],
    entry_data: dict[str, Any],
) -> int | None:
    """Resolve a listing ID from call data.

    If ``listing_id`` is provided directly, it takes precedence.
    If ``listing_name`` is provided, resolves it via a cached
    ``internal_name`` index built from the listings coordinator.

    Args:
        call_data: Service call data dictionary.
        entry_data: Runtime data for the resolved config entry.

    Returns:
        The resolved listing ID, or None if neither field present.

    Raises:
        ServiceValidationError: If listing_name is not found in
            the coordinator cache or listings data is unavailable.
    """
    if "listing_id" in call_data:
        result: int = call_data["listing_id"]
        return result

    if "listing_name" not in call_data:
        return None

    listing_name: str = call_data["listing_name"]
    listings_coordinator = entry_data["listings_coordinator"]
    listing_name_index = _get_listing_name_index(listings_coordinator)

    if listing_name in listing_name_index:
        return listing_name_index[listing_name]

    raise ServiceValidationError(
        f"Listing '{listing_name}' not found",
    )


async def async_handle_create_task(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.create_task service call.

    Builds a camelCase payload from the service call data,
    resolves listing if specified by name, and sends a POST
    request to create the task.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The created task data from the API.

    Raises:
        ServiceValidationError: On invalid input or missing listing.
        HomeAssistantError: On API failure.
    """
    payload: dict[str, Any] = {"title": call.data["title"]}

    if call.data.get("description") is not None:
        payload["description"] = call.data["description"]
    if call.data.get("reservation_id") is not None:
        payload["reservationId"] = call.data["reservation_id"]
    if call.data.get("status") is not None:
        payload["status"] = call.data["status"]
    if call.data.get("priority") is not None:
        payload["priority"] = call.data["priority"]
    if call.data.get("assignee_user_id") is not None:
        payload["assigneeUserId"] = call.data["assignee_user_id"]
    if call.data.get("can_be_picked_by_group_id") is not None:
        payload["canBePickedByGroupId"] = call.data["can_be_picked_by_group_id"]
    if call.data.get("supervisor_user_id") is not None:
        payload["supervisorUserId"] = call.data["supervisor_user_id"]
    if call.data.get("categories_map") is not None:
        payload["categoriesMap"] = call.data["categories_map"]
    if call.data.get("can_start_from") is not None:
        payload["canStartFrom"] = call.data["can_start_from"]
    if call.data.get("should_end_by") is not None:
        payload["shouldEndBy"] = call.data["should_end_by"]

    entry_data = _resolve_entry_data(hass, call.data)

    listing_id = _resolve_listing_id(call.data, entry_data)
    if listing_id is not None:
        payload["listingMapId"] = listing_id

    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        return await api_client.create_task(payload)
    except HostawayResponseError as exc:
        if _is_user_correctable_task_error(exc):
            raise ServiceValidationError(
                f"Invalid task data: {exc}",
            ) from exc
        raise HomeAssistantError(
            f"Failed to create task: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to create task: {exc}",
        ) from exc


async def async_handle_update_task(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.update_task service call.

    Builds a camelCase payload from optional fields, resolves
    listing if specified by name, and sends a PUT request to
    update the task.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The updated task data from the API.

    Raises:
        ServiceValidationError: On invalid input or missing resource.
        HomeAssistantError: On API failure.
    """
    task_id: int = call.data["task_id"]
    payload: dict[str, Any] = {}

    if call.data.get("title") is not None:
        payload["title"] = call.data["title"]
    if call.data.get("description") is not None:
        payload["description"] = call.data["description"]
    if call.data.get("reservation_id") is not None:
        payload["reservationId"] = call.data["reservation_id"]
    if call.data.get("status") is not None:
        payload["status"] = call.data["status"]
    if call.data.get("priority") is not None:
        payload["priority"] = call.data["priority"]
    if call.data.get("assignee_user_id") is not None:
        payload["assigneeUserId"] = call.data["assignee_user_id"]
    if call.data.get("can_be_picked_by_group_id") is not None:
        payload["canBePickedByGroupId"] = call.data["can_be_picked_by_group_id"]
    if call.data.get("supervisor_user_id") is not None:
        payload["supervisorUserId"] = call.data["supervisor_user_id"]
    if call.data.get("categories_map") is not None:
        payload["categoriesMap"] = call.data["categories_map"]
    if call.data.get("can_start_from") is not None:
        payload["canStartFrom"] = call.data["can_start_from"]
    if call.data.get("should_end_by") is not None:
        payload["shouldEndBy"] = call.data["should_end_by"]
    if call.data.get("resolution_note") is not None:
        payload["resolutionNote"] = call.data["resolution_note"]

    entry_data = _resolve_entry_data(hass, call.data)

    listing_id = _resolve_listing_id(call.data, entry_data)
    if listing_id is not None:
        payload["listingMapId"] = listing_id

    if not payload:
        raise ServiceValidationError(
            "At least one field to update must be provided",
        )

    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        return await api_client.update_task(task_id, payload)
    except HostawayResponseError as exc:
        if "not found" in str(exc).lower():
            raise ServiceValidationError(
                f"Task {task_id} not found",
            ) from exc
        raise HomeAssistantError(
            f"Failed to update task: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to update task: {exc}",
        ) from exc


async def async_handle_delete_task(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle hostaway.delete_task service call.

    Sends a DELETE request to remove the specified task.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Raises:
        ServiceValidationError: On invalid input or missing resource.
        HomeAssistantError: On API failure.
    """
    task_id: int = call.data["task_id"]

    entry_data = _resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        await api_client.delete_task(task_id)
    except HostawayResponseError as exc:
        if "not found" in str(exc).lower():
            raise ServiceValidationError(
                f"Task {task_id} not found",
            ) from exc
        raise HomeAssistantError(
            f"Failed to delete task: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to delete task: {exc}",
        ) from exc


async def async_handle_get_tasks(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.get_tasks service call.

    Builds camelCase query params from optional filters,
    resolves listing if specified by name, and sends a GET
    request to retrieve tasks.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Dict containing the tasks list: {"tasks": [...]}.

    Raises:
        ServiceValidationError: On invalid input or missing listing.
        HomeAssistantError: On API failure.
    """
    params: dict[str, Any] = {}
    entry_data = _resolve_entry_data(hass, call.data)

    listing_id = _resolve_listing_id(call.data, entry_data)
    if listing_id is not None:
        params["listingMapId"] = listing_id
    if call.data.get("reservation_id") is not None:
        params["reservationId"] = call.data["reservation_id"]
    if call.data.get("status") is not None:
        params["status"] = call.data["status"]
    if call.data.get("can_start_from_start") is not None:
        params["canStartFromStart"] = call.data["can_start_from_start"]
    if call.data.get("can_start_from_end") is not None:
        params["canStartFromEnd"] = call.data["can_start_from_end"]

    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        tasks = await api_client.get_tasks(params or None)
    except HostawayResponseError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve tasks: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to retrieve tasks: {exc}",
        ) from exc

    return {"tasks": tasks}


async def async_handle_set_door_code(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle hostaway.set_door_code service call.

    Validates inputs, builds a camelCase payload, and sends
    a PUT request to update the reservation's door code.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Raises:
        ServiceValidationError: On invalid input or missing resource.
        HomeAssistantError: On API failure.
    """
    reservation_id: int = call.data["reservation_id"]
    door_code: str = call.data["door_code"]

    payload: dict[str, Any] = {"doorCode": door_code}
    if call.data.get("door_code_vendor") is not None:
        payload["doorCodeVendor"] = call.data["door_code_vendor"]
    if call.data.get("door_code_instruction") is not None:
        payload["doorCodeInstruction"] = call.data["door_code_instruction"]

    entry_data = _resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        await api_client.update_reservation(reservation_id, payload)
    except HostawayReservationLockedError as exc:
        _log_locked_reservation(reservation_id, exc)
        return
    except HostawayResponseError as exc:
        if "not found" in str(exc).lower():
            raise ServiceValidationError(
                f"Reservation {reservation_id} not found",
            ) from exc
        raise HomeAssistantError(
            f"Failed to update reservation: {exc}",
        ) from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(
            f"Failed to update reservation: {exc}",
        ) from exc


async def async_handle_get_reservations(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any] | None:
    """Handle hostaway.get_reservations service call.

    Returns cached reservations when available. Falls back to
    the API when the listing is not in cache or when
    force_refresh is True. Fires a backwards-compat event and
    returns the data for SupportsResponse.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Event data dict if return_response is True, else None.

    Raises:
        ServiceValidationError: On invalid input.
        HomeAssistantError: On API failure.
    """
    listing_id: int = call.data["listing_id"]
    force_refresh: bool = call.data.get("force_refresh", False)

    entry_data = _resolve_entry_data(hass, call.data)
    listings_coordinator = entry_data["listings_coordinator"]
    reservations_coordinator = entry_data["reservations_coordinator"]

    reservations = None

    # Use cached data unless force_refresh is requested
    if not force_refresh and reservations_coordinator.data is not None:
        cached = reservations_coordinator.data.get(listing_id)
        if cached is not None:
            reservations = cached
        # listing_id not tracked locally; fall through to API

    if reservations is None:
        api_client: HostawayApiClient = entry_data["api_client"]
        try:
            reservations = await api_client.get_all_reservations(
                listing_id,
            )
        except HostawayResponseError as exc:
            if "not found" in str(exc).lower():
                _LOGGER.debug(
                    "Listing %d not found, returning empty list",
                    listing_id,
                )
                reservations = []
            else:
                raise HomeAssistantError(
                    f"Failed to fetch reservations: {exc}",
                ) from exc
        except HostawayApiError as exc:
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc

    listing_name = "Unknown"
    if listings_coordinator.data:
        listing = listings_coordinator.data.get(listing_id)
        if listing:
            listing_name = listing.name

    event_data: dict[str, Any] = {
        "listing_id": listing_id,
        "listing_name": listing_name,
        "reservations": [
            {
                "id": r.id,
                "guest_name": r.guest_name,
                "check_in": r.check_in,
                "check_out": r.check_out,
                "status": r.status,
                "door_code": r.door_code,
            }
            for r in reservations
        ],
    }
    hass.bus.async_fire(
        "hostaway_reservations_retrieved",
        event_data,
        context=call.context,
    )

    if call.return_response:
        return event_data
    return None


async def async_handle_find_reservation(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle hostaway.find_reservation service call.

    Searches for a reservation by guest name and dates, first in
    the coordinator cache and optionally via the API.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        Dict with found flag and reservation data if matched.

    Raises:
        ServiceValidationError: On invalid input.
        HomeAssistantError: On API failure.
    """
    guest_name: str = call.data["guest_name"]
    check_in: str = call.data["check_in"]
    check_out: str = call.data["check_out"]
    listing_id: int | None = call.data.get("listing_id")

    entry_data = _resolve_entry_data(hass, call.data)
    reservations_coordinator = entry_data["reservations_coordinator"]

    def _match(r: HostawayReservation) -> bool:
        """Check if reservation matches search criteria."""
        return (
            guest_name.lower() in r.guest_name.lower()
            and r.check_in == check_in
            and r.check_out == check_out
        )

    # Search coordinator cache
    if reservations_coordinator.data:
        if listing_id is not None:
            cached = reservations_coordinator.data.get(listing_id, [])
            for r in cached:
                if _match(r):
                    return _reservation_result(r)
        else:
            for res_list in reservations_coordinator.data.values():
                for r in res_list:
                    if _match(r):
                        return _reservation_result(r)

    # Fall back to API if listing_id provided
    if listing_id is not None:
        api_client: HostawayApiClient = entry_data["api_client"]
        try:
            reservations = await api_client.get_all_reservations(
                listing_id,
            )
        except HostawayResponseError as exc:
            if "not found" in str(exc).lower():
                return {"found": False, "reservation": None}
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        except HostawayApiError as exc:
            raise HomeAssistantError(
                f"Failed to fetch reservations: {exc}",
            ) from exc
        for r in reservations:
            if _match(r):
                return _reservation_result(r)

    return {"found": False, "reservation": None}


def _reservation_result(r: HostawayReservation) -> dict[str, Any]:
    """Build a successful find_reservation result dict.

    Args:
        r: The matched reservation object.

    Returns:
        Dict with found=True and reservation details.
    """
    return {
        "found": True,
        "reservation": {
            "id": r.id,
            "listing_id": r.listing_id,
            "guest_name": r.guest_name,
            "check_in": r.check_in,
            "check_out": r.check_out,
            "status": r.status,
            "door_code": r.door_code,
            "confirmation_code": r.confirmation_code,
        },
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Hostaway domain-level services.

    Idempotent: skips registration for services that already
    exist. Safe to call on every config entry setup.

    Args:
        hass: Home Assistant instance.
    """

    async def _handle_set_door_code(call: ServiceCall) -> None:
        """Delegate to set_door_code handler."""
        await async_handle_set_door_code(hass, call)

    async def _handle_get_reservations(
        call: ServiceCall,
    ) -> dict[str, Any] | None:
        """Delegate to get_reservations handler."""
        return await async_handle_get_reservations(hass, call)

    if not hass.services.has_service(DOMAIN, "set_door_code"):
        hass.services.async_register(
            DOMAIN,
            "set_door_code",
            _handle_set_door_code,
            schema=SERVICE_SET_DOOR_CODE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, "get_reservations"):
        hass.services.async_register(
            DOMAIN,
            "get_reservations",
            _handle_get_reservations,
            schema=SERVICE_GET_RESERVATIONS_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _handle_find_reservation(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Delegate to find_reservation handler."""
        return await async_handle_find_reservation(hass, call)

    if not hass.services.has_service(DOMAIN, "find_reservation"):
        hass.services.async_register(
            DOMAIN,
            "find_reservation",
            _handle_find_reservation,
            schema=SERVICE_FIND_RESERVATION_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_create_task(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Delegate to create_task handler."""
        return await async_handle_create_task(hass, call)

    if not hass.services.has_service(DOMAIN, "create_task"):
        hass.services.async_register(
            DOMAIN,
            "create_task",
            _handle_create_task,
            schema=SERVICE_CREATE_TASK_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_update_task(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Delegate to update_task handler."""
        return await async_handle_update_task(hass, call)

    if not hass.services.has_service(DOMAIN, "update_task"):
        hass.services.async_register(
            DOMAIN,
            "update_task",
            _handle_update_task,
            schema=SERVICE_UPDATE_TASK_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _handle_delete_task(call: ServiceCall) -> None:
        """Delegate to delete_task handler."""
        await async_handle_delete_task(hass, call)

    if not hass.services.has_service(DOMAIN, "delete_task"):
        hass.services.async_register(
            DOMAIN,
            "delete_task",
            _handle_delete_task,
            schema=SERVICE_DELETE_TASK_SCHEMA,
        )

    async def _handle_get_tasks(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Delegate to get_tasks handler."""
        return await async_handle_get_tasks(hass, call)

    if not hass.services.has_service(DOMAIN, "get_tasks"):
        hass.services.async_register(
            DOMAIN,
            "get_tasks",
            _handle_get_tasks,
            schema=SERVICE_GET_TASKS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
