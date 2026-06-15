# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Task-oriented Hostaway service handlers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hostaway.api.client import HostawayApiClient
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayResponseError,
)

from . import helpers, schemas


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

    entry_data = helpers._resolve_entry_data(hass, call.data)

    listing_id = helpers._resolve_listing_id(call.data, entry_data)
    if listing_id is not None:
        payload["listingMapId"] = listing_id

    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        return await api_client.create_task(payload)
    except HostawayResponseError as exc:
        if schemas._is_user_correctable_task_error(exc):
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

    entry_data = helpers._resolve_entry_data(hass, call.data)

    listing_id = helpers._resolve_listing_id(call.data, entry_data)
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

    entry_data = helpers._resolve_entry_data(hass, call.data)
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
    entry_data = helpers._resolve_entry_data(hass, call.data)

    listing_id = helpers._resolve_listing_id(call.data, entry_data)
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
