# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validation helpers and schemas for Hostaway services."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import math
from typing import Any

import probatio

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
        probatio.Invalid: If the value is not a valid positive integer.
    """
    if isinstance(value, bool):
        raise probatio.Invalid("boolean is not a valid integer")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise probatio.Invalid("expected a finite number")
        if value != int(value):
            raise probatio.Invalid("float with fractional part is not valid")
        value = int(value)
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as exc:
            raise probatio.Invalid("expected an integer") from exc
    if value <= 0:
        raise probatio.Invalid("must be a positive integer")
    result: int = value
    return result


def _strict_positive_int(value: Any) -> int:
    """Validate a positive integer without coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise probatio.Invalid("must be a positive integer")
    return value


def _non_empty_string(value: Any) -> str:
    """Validate a non-empty string, stripping whitespace.

    Args:
        value: The value to validate.

    Returns:
        The stripped string.

    Raises:
        probatio.Invalid: If the value is not a non-empty string.
    """
    if not isinstance(value, str):
        raise probatio.Invalid("expected a string")
    stripped = value.strip()
    if not stripped:
        raise probatio.Invalid("must be a non-empty string")
    return stripped


def _strict_string(value: Any) -> str:
    """Validate that a value is a string without coercion.

    Args:
        value: The value to validate.

    Returns:
        The original string value.

    Raises:
        probatio.Invalid: If the value is not a string.
    """
    if not isinstance(value, str):
        raise probatio.Invalid("expected a string")
    return value


def _positive_int_list(value: Any) -> list[int]:
    """Validate a list of positive integers without container coercion.

    Args:
        value: The value to validate.

    Returns:
        The validated list of positive integers.

    Raises:
        probatio.Invalid: If the value is not a list of positive integers.
    """
    if not isinstance(value, list):
        raise probatio.Invalid("expected a list")
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


SERVICE_SET_DOOR_CODE_SCHEMA = probatio.Schema(
    {
        probatio.Required("reservation_id"): _positive_int,
        probatio.Required("door_code"): _non_empty_string,
        probatio.Optional("door_code_vendor"): probatio.Maybe(_strict_string),
        probatio.Optional("door_code_instruction"): probatio.Maybe(
            _strict_string,
        ),
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_RESERVATIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required("listing_id"): _positive_int,
        probatio.Optional("force_refresh"): probatio.Boolean(),
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_FIND_RESERVATION_SCHEMA = probatio.Schema(
    {
        probatio.Required("guest_name"): _non_empty_string,
        probatio.Required("check_in"): _non_empty_string,
        probatio.Required("check_out"): _non_empty_string,
        probatio.Optional("listing_id"): _positive_int,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_CREATE_TASK_SCHEMA = probatio.Schema(
    {
        probatio.Required("title"): _non_empty_string,
        probatio.Optional("description"): _strict_string,
        probatio.Optional("listing_id"): _positive_int,
        probatio.Optional("listing_name"): _non_empty_string,
        probatio.Optional("reservation_id"): _positive_int,
        probatio.Optional("status"): probatio.In(_TASK_STATUS_VALUES),
        probatio.Optional("priority"): _positive_int,
        probatio.Optional("assignee_user_id"): _positive_int,
        probatio.Optional("can_be_picked_by_group_id"): _positive_int,
        probatio.Optional("supervisor_user_id"): _positive_int,
        probatio.Optional("categories_map"): _positive_int_list,
        probatio.Optional("can_start_from"): _non_empty_string,
        probatio.Optional("should_end_by"): _non_empty_string,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_UPDATE_TASK_SCHEMA = probatio.Schema(
    {
        probatio.Required("task_id"): _positive_int,
        probatio.Optional("title"): _non_empty_string,
        probatio.Optional("description"): _strict_string,
        probatio.Optional("listing_id"): _positive_int,
        probatio.Optional("listing_name"): _non_empty_string,
        probatio.Optional("reservation_id"): _positive_int,
        probatio.Optional("status"): probatio.In(_TASK_STATUS_VALUES),
        probatio.Optional("priority"): _positive_int,
        probatio.Optional("assignee_user_id"): _positive_int,
        probatio.Optional("can_be_picked_by_group_id"): _positive_int,
        probatio.Optional("supervisor_user_id"): _positive_int,
        probatio.Optional("categories_map"): _positive_int_list,
        probatio.Optional("can_start_from"): _non_empty_string,
        probatio.Optional("should_end_by"): _non_empty_string,
        probatio.Optional("resolution_note"): _strict_string,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_DELETE_TASK_SCHEMA = probatio.Schema(
    {
        probatio.Required("task_id"): _positive_int,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_TASKS_SCHEMA = probatio.Schema(
    {
        probatio.Optional("listing_id"): _positive_int,
        probatio.Optional("listing_name"): _non_empty_string,
        probatio.Optional("reservation_id"): _positive_int,
        probatio.Optional("status"): probatio.In(_TASK_STATUS_VALUES),
        probatio.Optional("can_start_from_start"): _non_empty_string,
        probatio.Optional("can_start_from_end"): _non_empty_string,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_USERS_SCHEMA = probatio.Schema(
    {
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_GROUPS_SCHEMA = probatio.Schema(
    {
        probatio.Optional("config_entry_id"): _strict_string,
    }
)


SERVICE_GET_CUSTOM_FIELDS_SCHEMA = probatio.Schema(
    {
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_GET_CUSTOM_FIELD_VALUES_SCHEMA = probatio.Schema(
    {
        probatio.Required("target_type"): probatio.In(("listing", "reservation")),
        probatio.Required("target_id"): _strict_positive_int,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)

SERVICE_SET_CUSTOM_FIELD_SCHEMA = probatio.Schema(
    {
        probatio.Required("target_type"): probatio.In(("listing", "reservation")),
        probatio.Required("target_id"): _strict_positive_int,
        probatio.Optional("customFieldId"): _strict_positive_int,
        probatio.Optional("varName"): _non_empty_string,
        probatio.Required("value"): object,
        probatio.Optional("config_entry_id"): _strict_string,
    }
)
