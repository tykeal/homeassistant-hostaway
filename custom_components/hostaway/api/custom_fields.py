# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway custom field definitions, values, and safe write helpers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeGuard

from custom_components.hostaway.api import responses as _responses
from custom_components.hostaway.api.exceptions import HostawayResponseError

_LOGGER = logging.getLogger(__name__)

CUSTOM_FIELD_PAGE_LIMIT = 500
LISTING_OBJECT_TYPE = "listing"
RESERVATION_OBJECT_TYPE = "reservation"
SUPPORTED_OBJECT_TYPES = frozenset({LISTING_OBJECT_TYPE, RESERVATION_OBJECT_TYPE})
CollectionState = Literal["present", "missing", "null", "invalid"]


class RequestProtocol(Protocol):
    """Narrow protocol for authenticated Hostaway API requests."""

    def __call__(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Awaitable[Any]:
        """Make an authenticated request."""


def is_valid_identifier(value: object) -> TypeGuard[int]:
    """Return whether value is a positive non-bool integer identifier."""
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def validate_identifier(value: object, field_name: str) -> int:
    """Return a positive integer identifier or raise ValueError."""
    if not is_valid_identifier(value):
        msg = f"{field_name} must be a positive integer"
        raise ValueError(msg)
    return value


def _string_field(data: Mapping[str, Any], key: str) -> str:
    """Return a required non-empty string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} must be a non-empty string"
        raise ValueError(msg)
    return value


def _optional_int(data: Mapping[str, Any], key: str) -> int | None:
    """Return an optional non-bool integer field."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer"
        raise ValueError(msg)
    return value


def _required_flag(data: Mapping[str, Any], key: str) -> bool:
    """Return a required 0/1 API flag as a boolean."""
    value = data.get(key)
    if isinstance(value, bool) or value not in (0, 1):
        msg = f"{key} must be 0 or 1"
        raise ValueError(msg)
    return int(value) == 1


def _possible_values(value: Any) -> list[str]:
    """Normalize Hostaway possibleValues into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


@dataclass(frozen=True)
class HostawayCustomFieldDefinition:
    """Account-level Hostaway custom field definition."""

    custom_field_id: int
    account_id: int | None
    name: str
    var_name: str
    field_type: str
    object_type: str
    possible_values: list[str]
    is_public: bool
    sort_order: int | None = None

    @classmethod
    def from_api_dict(
        cls, data: Mapping[str, Any]
    ) -> HostawayCustomFieldDefinition | None:
        """Parse one Hostaway custom-field definition."""
        try:
            object_type = _string_field(data, "objectType")
            if object_type not in SUPPORTED_OBJECT_TYPES:
                return None
            raw_id = data.get("id")
            custom_field_id = validate_identifier(raw_id, "id")
            account_id = _optional_int(data, "accountId")
            field_type = _string_field(data, "type")
            return cls(
                custom_field_id=custom_field_id,
                account_id=account_id,
                name=_string_field(data, "name"),
                var_name=_string_field(data, "varName"),
                field_type=field_type,
                object_type=object_type,
                possible_values=_possible_values(data.get("possibleValues")),
                is_public=_required_flag(data, "isPublic"),
                sort_order=_optional_int(data, "sortOrder"),
            )
        except ValueError as exc:
            _LOGGER.warning("Skipping malformed Hostaway custom field: %s", exc)
            return None

    def as_service_dict(self) -> dict[str, Any]:
        """Return the public service response shape for this definition."""
        return {
            "customFieldId": self.custom_field_id,
            "varName": self.var_name,
            "name": self.name,
            "type": self.field_type,
            "objectType": self.object_type,
            "possibleValues": list(self.possible_values),
            "isPublic": self.is_public,
            "sortOrder": self.sort_order,
        }


@dataclass(frozen=True)
class HostawayCustomFieldValue:
    """Presentation-safe Hostaway custom field value."""

    custom_field_id: int
    value: Any
    raw: Any

    @classmethod
    def from_api_entry(cls, data: Any) -> HostawayCustomFieldValue | None:
        """Parse one custom-field value entry for presentation."""
        try:
            if not isinstance(data, Mapping):
                msg = "entry must be an object"
                raise ValueError(msg)
            custom_field_id = validate_identifier(
                data.get("customFieldId"), "customFieldId"
            )
            if "value" not in data:
                msg = "value is required"
                raise ValueError(msg)
            return cls(custom_field_id=custom_field_id, value=data["value"], raw=data)
        except ValueError as exc:
            raw_id = data.get("customFieldId") if isinstance(data, Mapping) else None
            _LOGGER.warning(
                "Skipping malformed Hostaway custom field value "
                "for customFieldId %r: %s",
                raw_id,
                exc,
            )
            return None


@dataclass(frozen=True)
class HostawayCustomFieldCollection:
    """Parsed custom-field collection with raw-preservation state."""

    state: CollectionState
    values: dict[int, HostawayCustomFieldValue] = field(default_factory=dict)
    raw_entries: list[Any] = field(default_factory=list)
    malformed_entries: list[Any] = field(default_factory=list)
    invalid_raw: Any | None = None

    @classmethod
    def from_object(
        cls, data: Mapping[str, Any], key: str = "customFieldValues"
    ) -> HostawayCustomFieldCollection:
        """Parse an object's customFieldValues collection."""
        if key not in data:
            return cls(state="missing")
        raw = data.get(key)
        if raw is None:
            return cls(state="null")
        if not isinstance(raw, list):
            return cls(state="invalid", invalid_raw=raw)
        values: dict[int, HostawayCustomFieldValue] = {}
        malformed: list[Any] = []
        for entry in raw:
            parsed = HostawayCustomFieldValue.from_api_entry(entry)
            if parsed is None:
                malformed.append(entry)
                continue
            values[parsed.custom_field_id] = parsed
        return cls(
            state="present",
            values=values,
            raw_entries=list(raw),
            malformed_entries=malformed,
        )

    def raw_entries_for_id(self, custom_field_id: int) -> list[Any]:
        """Return all raw entries carrying a matching customFieldId."""
        return [
            entry
            for entry in self.raw_entries
            if isinstance(entry, Mapping)
            and entry.get("customFieldId") == custom_field_id
        ]

    def has_malformed_for_id(self, custom_field_id: int) -> bool:
        """Return whether a malformed raw entry carries the addressed id."""
        return any(
            isinstance(entry, Mapping) and entry.get("customFieldId") == custom_field_id
            for entry in self.malformed_entries
        )


@dataclass(frozen=True, init=False)
class CustomFieldWriteSafetyGates:
    """Executable default-off safety gates for custom-field writes."""

    listing_partial_put_verified: bool
    reservation_no_clobber_verified: bool

    def __init__(self) -> None:
        """Initialize both safety gates to their source-controlled defaults."""
        object.__setattr__(self, "listing_partial_put_verified", False)
        object.__setattr__(self, "reservation_no_clobber_verified", False)


@dataclass
class CustomFieldWriteLockRegistry:
    """Per-entry write lock registry placeholder."""

    locks: dict[tuple[str, int], Any] = field(default_factory=dict)


@dataclass
class CustomFieldWriteGenerationRegistry:
    """Per-entry custom-field write generation counters."""

    generations: dict[tuple[str, int], int] = field(default_factory=dict)

    def current(self, target_type: str, target_id: int) -> int:
        """Return the current generation for a target."""
        return self.generations.get((target_type, target_id), 0)

    def advance(self, target_type: str, target_id: int) -> int:
        """Advance and return the generation for a target."""
        key = (target_type, target_id)
        self.generations[key] = self.generations.get(key, 0) + 1
        return self.generations[key]


class CustomFieldMergeError(ValueError):
    """Raised when a custom-field write payload cannot be safely built."""


async def fetch_custom_field_definitions(
    request: RequestProtocol,
    *,
    object_type: str | None = None,
) -> list[HostawayCustomFieldDefinition]:
    """Fetch all custom-field definitions through offset pagination."""
    definitions: list[HostawayCustomFieldDefinition] = []
    offset = 0
    while True:
        params: dict[str, Any] = {"limit": CUSTOM_FIELD_PAGE_LIMIT, "offset": offset}
        if object_type is not None:
            params["objectType"] = object_type
        response = await request("GET", "/v1/customFields", params=params)
        data = _responses.parse_response(response)
        items = _responses.extract_results(
            data, error_prefix="Get custom fields failed"
        )
        for item in items:
            parsed = HostawayCustomFieldDefinition.from_api_dict(item)
            if parsed is not None:
                definitions.append(parsed)
        if _is_last_definition_page(data, len(items)):
            return definitions
        offset += CUSTOM_FIELD_PAGE_LIMIT


def _is_last_definition_page(data: Mapping[str, Any], item_count: int) -> bool:
    """Return whether a custom-field definitions page is terminal."""
    total_pages = data.get("totalPages")
    page = data.get("page")
    if isinstance(total_pages, int) and isinstance(page, int):
        return page >= total_pages
    limit = data.get("limit", CUSTOM_FIELD_PAGE_LIMIT)
    if not isinstance(limit, int) or limit <= 0:
        limit = CUSTOM_FIELD_PAGE_LIMIT
    return item_count < limit


async def read_listing_with_custom_fields(
    request: RequestProtocol, listing_id: int
) -> dict[str, Any]:
    """Read one listing with includeResources=1 and an object result."""
    validate_identifier(listing_id, "listing_id")
    return await _read_single_object(request, f"/v1/listings/{listing_id}")


async def read_reservation_with_custom_fields(
    request: RequestProtocol, reservation_id: int
) -> dict[str, Any]:
    """Read one reservation with includeResources=1 and an object result."""
    validate_identifier(reservation_id, "reservation_id")
    return await _read_single_object(request, f"/v1/reservations/{reservation_id}")


async def _read_single_object(request: RequestProtocol, path: str) -> dict[str, Any]:
    """Read one object endpoint and validate a mapping result."""
    response = await request("GET", path, params={"includeResources": 1})
    result = _responses.ensure_success(
        _responses.parse_response(response), "Get failed"
    )
    if not isinstance(result, dict):
        raise HostawayResponseError("Response missing 'result' object")
    return result


def ensure_writable_collection(
    collection: HostawayCustomFieldCollection,
) -> None:
    """Raise when a collection is unsafe for read-modify-write merge."""
    if collection.state != "present":
        msg = (
            "customFieldValues must be a present list before writing; "
            f"got {collection.state}"
        )
        raise CustomFieldMergeError(msg)


def preflight_addressed_entry(
    collection: HostawayCustomFieldCollection, custom_field_id: int
) -> None:
    """Fail closed for duplicates or malformed addressed raw entries."""
    validate_identifier(custom_field_id, "customFieldId")
    if collection.has_malformed_for_id(custom_field_id):
        msg = "addressed customFieldId has a malformed raw entry"
        raise CustomFieldMergeError(msg)
    addressed = collection.raw_entries_for_id(custom_field_id)
    if len(addressed) > 1:
        msg = "addressed customFieldId has duplicate raw entries"
        raise CustomFieldMergeError(msg)


def build_custom_field_values_payload(
    current_object: Mapping[str, Any], custom_field_id: int, value: Any
) -> dict[str, Any]:
    """Build a no-clobber custom-field write payload.

    The returned body intentionally contains exactly one top-level key:
    ``customFieldValues``.
    """
    custom_field_id = validate_identifier(custom_field_id, "customFieldId")
    collection = HostawayCustomFieldCollection.from_object(current_object)
    ensure_writable_collection(collection)
    preflight_addressed_entry(collection, custom_field_id)

    merged: list[Any] = []
    replaced = False
    for raw in collection.raw_entries:
        if isinstance(raw, Mapping) and raw.get("customFieldId") == custom_field_id:
            merged.append({"customFieldId": custom_field_id, "value": value})
            replaced = True
        else:
            merged.append(raw)
    if not replaced:
        merged.append({"customFieldId": custom_field_id, "value": value})
    return {"customFieldValues": merged}


def definition_index_by_id(
    definitions: Iterable[HostawayCustomFieldDefinition], object_type: str
) -> dict[int, HostawayCustomFieldDefinition]:
    """Return definitions keyed by id for one object type."""
    return {
        definition.custom_field_id: definition
        for definition in definitions
        if definition.object_type == object_type
    }


def definitions_for_object_type(
    definitions: Iterable[HostawayCustomFieldDefinition], object_type: str
) -> list[HostawayCustomFieldDefinition]:
    """Return definitions for one object type."""
    return [
        definition
        for definition in definitions
        if definition.object_type == object_type
    ]


def request_results_adapter(
    request_results: Callable[..., Awaitable[list[dict[str, Any]]]],
) -> RequestProtocol:
    """Adapt a list-result helper into the request protocol for tests."""

    async def _request(
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Adapt one GET request into an httpx response object."""
        if method != "GET" or json is not None:
            msg = "adapter only supports GET list requests"
            raise HostawayResponseError(msg)
        items = await request_results(path, params=params)
        import httpx

        return httpx.Response(200, json={"status": "success", "result": items})

    return _request
