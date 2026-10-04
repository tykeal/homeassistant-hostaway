# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway custom field definitions, values, and safe write helpers."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages
# aislop-ignore-file complexity/file-too-large -- cohesive foundation module

from __future__ import annotations

import json
import logging
from collections import Counter
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
ListingPayloadStrategy = Literal["partial", "full_object"]
ReservationPayloadStrategy = Literal["partial"]
PayloadStrategy = Literal["partial", "full_object"]
LISTING_WRITABLE_RESTORE_FIELDS = frozenset(
    {
        "name",
        "internalListingName",
        "externalListingName",
        "description",
        "houseRules",
        "keyPickup",
        "specialInstruction",
        "street",
        "address",
        "city",
        "state",
        "country",
        "zipcode",
        "price",
        "personCapacity",
        "bedroomsNumber",
        "bedsNumber",
        "bathroomsNumber",
        "minNights",
        "maxNights",
        "checkInTimeStart",
        "checkInTimeEnd",
        "checkOutTime",
        "currencyCode",
        "timeZoneName",
        "latitude",
        "longitude",
        "customFieldValues",
    }
)
TASK_WRITABLE_RESTORE_FIELDS = frozenset(
    {
        "listingMapId",
        "reservationId",
        "assigneeUserId",
        "canBePickedByGroupId",
        "supervisorUserId",
        "title",
        "description",
        "status",
        "priority",
        "canStartFrom",
        "shouldEndBy",
        "categoriesMap",
        "resolutionNote",
        "dueDate",
        "customFieldValues",
    }
)
SERVER_MANAGED_VOLATILE_FIELDS = frozenset(
    {
        "updatedAt",
        "updatedOn",
        "lastUpdatedAt",
        "lastUpdatedOn",
        "modifiedAt",
        "modifiedOn",
    }
)


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
    if not isinstance(value, int) or isinstance(value, bool) or value not in (0, 1):
        msg = f"{key} must be 0 or 1"
        raise ValueError(msg)
    return value == 1


def _possible_values(value: Any) -> list[str]:
    """Normalize Hostaway possibleValues into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return [item for item in decoded if isinstance(item, str)]
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
    listing_payload_strategy: ListingPayloadStrategy | None
    reservation_no_clobber_verified: bool
    reservation_payload_strategy: ReservationPayloadStrategy | None

    def __init__(self) -> None:
        """Initialize both safety gates to their source-controlled defaults."""
        object.__setattr__(self, "listing_partial_put_verified", False)
        object.__setattr__(self, "listing_payload_strategy", None)
        object.__setattr__(self, "reservation_no_clobber_verified", False)
        object.__setattr__(self, "reservation_payload_strategy", None)


@dataclass(frozen=True)
class ReservationCustomFieldEvidenceState:
    """Account-bound reservation custom-field write evidence state."""

    account_id: int | None = None
    config_entry_id: str | None = None
    custom_field_values_round_trip_verified: bool = False
    authoritative_contract_verified: bool = False
    payload_strategy: ReservationPayloadStrategy | None = None

    @property
    def enables_reservation_writes(self) -> bool:
        """Return whether this account has sufficient reservation evidence."""
        has_evidence = (
            self.custom_field_values_round_trip_verified
            or self.authoritative_contract_verified
        )
        return (
            self.account_id is not None
            and self.config_entry_id is not None
            and has_evidence
            and self.payload_strategy == "partial"
        )


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


class CustomFieldDefinitionError(ValueError):
    """Raised when a custom-field definition cannot be resolved safely."""


def _normalize_object_type(object_type: str) -> str:
    """Validate and return a supported Hostaway custom-field object type."""
    if object_type not in SUPPORTED_OBJECT_TYPES:
        msg = "object_type must be listing or reservation"
        raise CustomFieldDefinitionError(msg)
    return object_type


def _definition_matches(
    definition: HostawayCustomFieldDefinition,
    object_type: str,
) -> bool:
    """Return whether a definition belongs to an object type."""
    return definition.object_type == object_type


def lookup_definition_by_id(
    definitions: Iterable[HostawayCustomFieldDefinition],
    custom_field_id: int,
    object_type: str,
) -> HostawayCustomFieldDefinition:
    """Return a custom-field definition by id scoped to one object type."""
    object_type = _normalize_object_type(object_type)
    custom_field_id = validate_identifier(custom_field_id, "customFieldId")
    for definition in definitions:
        if (
            definition.custom_field_id == custom_field_id
            and definition.object_type == object_type
        ):
            return definition
    msg = f"unknown {object_type} customFieldId {custom_field_id}"
    raise CustomFieldDefinitionError(msg)


def resolve_var_name(
    definitions: Iterable[HostawayCustomFieldDefinition],
    var_name: str,
    object_type: str,
) -> HostawayCustomFieldDefinition:
    """Resolve one varName to a definition scoped to one object type."""
    object_type = _normalize_object_type(object_type)
    if not isinstance(var_name, str) or not var_name:
        msg = "varName must be a non-empty string"
        raise CustomFieldDefinitionError(msg)
    matches = [
        definition
        for definition in definitions
        if _definition_matches(definition, object_type)
        and definition.var_name == var_name
    ]
    if not matches:
        msg = f"unknown {object_type} custom-field varName {var_name!r}"
        raise CustomFieldDefinitionError(msg)
    if len(matches) > 1:
        msg = f"ambiguous {object_type} custom-field varName {var_name!r}"
        raise CustomFieldDefinitionError(msg)
    return matches[0]


def var_name_slug_counts(
    definitions: Iterable[HostawayCustomFieldDefinition],
    slugifier: Callable[[str], str],
) -> Counter[str]:
    """Return slug occurrence counts for a definition sequence."""
    return Counter(slugifier(definition.var_name) for definition in definitions)


def var_name_counts(
    definitions: Iterable[HostawayCustomFieldDefinition],
) -> Counter[str]:
    """Return varName occurrence counts for a definition sequence."""
    return Counter(definition.var_name for definition in definitions)


def validate_custom_field_value(
    definition: HostawayCustomFieldDefinition,
    value: Any,
) -> Any:
    """Validate and normalize a custom-field value for local known types."""
    if value is None:
        return None
    if definition.field_type in {"text", "textarea"}:
        if not isinstance(value, str):
            msg = f"{definition.var_name} requires a string value"
            raise CustomFieldDefinitionError(msg)
        return value
    if definition.field_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            msg = f"{definition.var_name} requires a non-boolean number value"
            raise CustomFieldDefinitionError(msg)
        return value
    if definition.field_type == "dropdown":
        if not isinstance(value, str):
            msg = f"{definition.var_name} requires a dropdown string value"
            raise CustomFieldDefinitionError(msg)
        normalized = value.strip()
        if normalized not in definition.possible_values:
            msg = f"{definition.var_name} must be one of possibleValues"
            raise CustomFieldDefinitionError(msg)
        return normalized
    return value


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
            updated = dict(raw)
            updated["value"] = value
            merged.append(updated)
            replaced = True
        else:
            merged.append(raw)
    if not replaced:
        merged.append({"customFieldId": custom_field_id, "value": value})
    return {"customFieldValues": merged}


def _restore_allowlist(target_type: str) -> frozenset[str]:
    """Return writable restore fields for a target type."""
    if target_type == "listing":
        return LISTING_WRITABLE_RESTORE_FIELDS
    if target_type == "task":
        return TASK_WRITABLE_RESTORE_FIELDS
    msg = "full-object restore payloads are disabled for reservations"
    raise CustomFieldMergeError(msg)


def _normalize_restore_value(value: Any) -> Any:
    """Return an allowlisted restore value normalized for JSON payloads."""
    if isinstance(value, Mapping):
        return {str(key): _normalize_restore_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_restore_value(item) for item in value]
    return value


def build_allowlisted_restore_payload(
    snapshot: Mapping[str, Any],
    target_type: str,
    *,
    require_concurrent_edit_detection: bool = False,
    concurrent_edit_detection_available: bool = False,
) -> dict[str, Any]:
    """Reconstruct an allowlisted restore payload from a complete snapshot.

    The payload is rebuilt field-by-field from a target-specific writable
    allowlist. Passing a raw deep copy of a GET response is therefore never a
    valid restore strategy.
    """
    if require_concurrent_edit_detection and not concurrent_edit_detection_available:
        msg = "full-object listing writes require concurrent edit detection"
        raise CustomFieldMergeError(msg)
    allowlist = _restore_allowlist(target_type)
    payload = {
        key: _normalize_restore_value(value)
        for key, value in snapshot.items()
        if key in allowlist
    }
    ensure_writable_collection(HostawayCustomFieldCollection.from_object(payload))
    if payload == dict(snapshot):
        msg = "raw GET response deepcopy is not a valid restore payload"
        raise CustomFieldMergeError(msg)
    return payload


def build_full_object_custom_field_payload(
    current_object: Mapping[str, Any],
    custom_field_id: int,
    value: Any,
    *,
    target_type: str = "listing",
    concurrent_edit_detection_available: bool = False,
) -> dict[str, Any]:
    """Build an allowlisted full-object payload with merged custom fields."""
    if target_type != "listing":
        msg = "full-object custom-field writes are listing-only"
        raise CustomFieldMergeError(msg)
    payload = build_allowlisted_restore_payload(
        current_object,
        target_type,
        require_concurrent_edit_detection=True,
        concurrent_edit_detection_available=concurrent_edit_detection_available,
    )
    merged = build_custom_field_values_payload(current_object, custom_field_id, value)
    payload["customFieldValues"] = merged["customFieldValues"]
    return payload


def select_listing_payload_strategy(
    gates: CustomFieldWriteSafetyGates,
) -> ListingPayloadStrategy:
    """Return the enabled listing strategy or fail closed."""
    if gates.listing_payload_strategy == "partial":
        if not gates.listing_partial_put_verified:
            msg = "listing partial strategy lacks partial-PUT verification"
            raise CustomFieldMergeError(msg)
        return "partial"
    if gates.listing_payload_strategy == "full_object":
        if gates.listing_partial_put_verified:
            msg = "full-object strategy must not masquerade as partial evidence"
            raise CustomFieldMergeError(msg)
        msg = "full-object listing strategy is disabled without version checks"
        raise CustomFieldMergeError(msg)
    msg = "listing custom-field writes are disabled until evidence is recorded"
    raise CustomFieldMergeError(msg)


def select_reservation_payload_strategy(
    gates: CustomFieldWriteSafetyGates,
    evidence: ReservationCustomFieldEvidenceState | None = None,
    *,
    account_id: int | None = None,
    config_entry_id: str | None = None,
) -> ReservationPayloadStrategy:
    """Return the enabled reservation strategy or fail closed."""
    if gates.reservation_payload_strategy != "partial":
        msg = "reservation custom-field writes are disabled until evidence is recorded"
        raise CustomFieldMergeError(msg)
    if not gates.reservation_no_clobber_verified:
        msg = "reservation no-clobber evidence has not been verified"
        raise CustomFieldMergeError(msg)
    if evidence is None or not evidence.enables_reservation_writes:
        msg = "reservation evidence is not bound to this Hostaway account"
        raise CustomFieldMergeError(msg)
    if evidence.account_id != account_id or evidence.config_entry_id != config_entry_id:
        msg = "reservation evidence does not match this Hostaway account"
        raise CustomFieldMergeError(msg)
    return "partial"


def canonicalize_complete_snapshot(value: Any) -> Any:
    """Return a canonical object snapshot for verification comparisons."""
    return _canonicalize_complete_snapshot(value, top_level=True)


def _canonicalize_complete_snapshot(value: Any, *, top_level: bool) -> Any:
    """Return a canonical snapshot value with scoped volatile exclusions."""
    if isinstance(value, Mapping):
        canonical: dict[str, Any] = {}
        for key in sorted(value):
            if top_level and key in SERVER_MANAGED_VOLATILE_FIELDS:
                continue
            item = value[key]
            if top_level and key == "customFieldValues" and isinstance(item, list):
                canonical[key] = _canonical_custom_field_values(item)
            else:
                canonical[key] = _canonicalize_complete_snapshot(item, top_level=False)
        return canonical
    if isinstance(value, list):
        return [
            _canonicalize_complete_snapshot(item, top_level=False) for item in value
        ]
    return value


def _canonical_custom_field_values(values: list[Any]) -> list[Any]:
    """Return custom-field values in stable comparison order."""
    canonical = [
        _canonicalize_complete_snapshot(item, top_level=False) for item in values
    ]
    return sorted(canonical, key=lambda item: json.dumps(item, sort_keys=True))


def canonical_snapshot_differences(before: Any, after: Any) -> list[str]:
    """Return paths that differ between two canonicalized snapshots."""
    return _snapshot_differences(
        canonicalize_complete_snapshot(before),
        canonicalize_complete_snapshot(after),
        "$",
    )


def _snapshot_differences(before: Any, after: Any, path: str) -> list[str]:
    """Return recursive snapshot difference paths."""
    if type(before) is not type(after):
        return [path]
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: list[str] = []
        keys = set(before) | set(after)
        for key in sorted(keys):
            if key not in before or key not in after:
                paths.append(f"{path}.{key}")
                continue
            paths.extend(
                _snapshot_differences(before[key], after[key], f"{path}.{key}")
            )
        return paths
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [path]
        paths = []
        for index, (before_item, after_item) in enumerate(
            zip(before, after, strict=True)
        ):
            paths.extend(
                _snapshot_differences(before_item, after_item, f"{path}[{index}]")
            )
        return paths
    if before != after:
        return [path]
    return []


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
