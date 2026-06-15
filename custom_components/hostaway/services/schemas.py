# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validation helpers and schemas for Hostaway services."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol

from custom_components.hostaway.api.exceptions import HostawayResponseError

_TASK_STATUS_VALUES = (
    "pending",
    "confirmed",
    "inProgress",
    "completed",
    "cancelled",
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

SERVICE_GET_USERS_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_GROUPS_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): _strict_string,
    }
)
