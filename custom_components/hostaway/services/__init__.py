# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service registration for the Hostaway integration."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, NamedTuple

import voluptuous as vol
from homeassistant.core import (
    EntityServiceResponse,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)

from custom_components.hostaway.const import DOMAIN

from .lookup_handlers import async_handle_get_groups, async_handle_get_users
from .reservation_handlers import (
    async_handle_find_reservation,
    async_handle_get_reservations,
    async_handle_set_door_code,
)
from .schemas import (
    SERVICE_CREATE_TASK_SCHEMA,
    SERVICE_DELETE_TASK_SCHEMA,
    SERVICE_FIND_RESERVATION_SCHEMA,
    SERVICE_GET_GROUPS_SCHEMA,
    SERVICE_GET_RESERVATIONS_SCHEMA,
    SERVICE_GET_TASKS_SCHEMA,
    SERVICE_GET_USERS_SCHEMA,
    SERVICE_SET_DOOR_CODE_SCHEMA,
    SERVICE_UPDATE_TASK_SCHEMA,
)
from .task_handlers import (
    async_handle_create_task,
    async_handle_delete_task,
    async_handle_get_tasks,
    async_handle_update_task,
)

type ServiceResult = ServiceResponse | EntityServiceResponse | None
type ServiceHandler = Callable[
    [HomeAssistant, ServiceCall], Coroutine[Any, Any, ServiceResult]
]
type RegisteredHandler = Callable[[ServiceCall], Coroutine[Any, Any, ServiceResult]]


class ServiceDefinition(NamedTuple):
    """Definition for a single Hostaway service."""

    name: str
    handler: ServiceHandler
    schema: vol.Schema
    supports_response: SupportsResponse | None


def _bind_handler(
    hass: HomeAssistant,
    handler: ServiceHandler,
) -> RegisteredHandler:
    """Bind Home Assistant to a service handler."""

    async def _bound(call: ServiceCall) -> ServiceResult:
        """Call a registered service handler with bound Home Assistant."""
        return await handler(hass, call)

    return _bound


SERVICE_DEFINITIONS: tuple[ServiceDefinition, ...] = (
    ServiceDefinition(
        "set_door_code",
        async_handle_set_door_code,
        SERVICE_SET_DOOR_CODE_SCHEMA,
        None,
    ),
    ServiceDefinition(
        "get_reservations",
        async_handle_get_reservations,
        SERVICE_GET_RESERVATIONS_SCHEMA,
        SupportsResponse.OPTIONAL,
    ),
    ServiceDefinition(
        "find_reservation",
        async_handle_find_reservation,
        SERVICE_FIND_RESERVATION_SCHEMA,
        SupportsResponse.ONLY,
    ),
    ServiceDefinition(
        "create_task",
        async_handle_create_task,
        SERVICE_CREATE_TASK_SCHEMA,
        SupportsResponse.ONLY,
    ),
    ServiceDefinition(
        "update_task",
        async_handle_update_task,
        SERVICE_UPDATE_TASK_SCHEMA,
        SupportsResponse.ONLY,
    ),
    ServiceDefinition(
        "delete_task",
        async_handle_delete_task,
        SERVICE_DELETE_TASK_SCHEMA,
        None,
    ),
    ServiceDefinition(
        "get_tasks",
        async_handle_get_tasks,
        SERVICE_GET_TASKS_SCHEMA,
        SupportsResponse.ONLY,
    ),
    ServiceDefinition(
        "get_users",
        async_handle_get_users,
        SERVICE_GET_USERS_SCHEMA,
        SupportsResponse.ONLY,
    ),
    ServiceDefinition(
        "get_groups",
        async_handle_get_groups,
        SERVICE_GET_GROUPS_SCHEMA,
        SupportsResponse.ONLY,
    ),
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Hostaway services via a table-driven loop."""
    for definition in SERVICE_DEFINITIONS:
        if hass.services.has_service(DOMAIN, definition.name):
            continue
        kwargs: dict[str, Any] = {"schema": definition.schema}
        if definition.supports_response is not None:
            kwargs["supports_response"] = definition.supports_response
        hass.services.async_register(
            DOMAIN,
            definition.name,
            _bind_handler(hass, definition.handler),
            **kwargs,
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove all registered Hostaway services."""
    for definition in SERVICE_DEFINITIONS:
        if hass.services.has_service(DOMAIN, definition.name):
            hass.services.async_remove(DOMAIN, definition.name)
