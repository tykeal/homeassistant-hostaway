# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for Hostaway custom field support."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages
# aislop-ignore-file complexity/file-too-large -- cohesive custom-field services

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.hostaway.api.const import INITIAL_BACKOFF, MAX_BACKOFF
from custom_components.hostaway.api.custom_fields import (
    LISTING_OBJECT_TYPE,
    RESERVATION_OBJECT_TYPE,
    SUPPORTED_OBJECT_TYPES,
    CustomFieldDefinitionError,
    CustomFieldMergeError,
    CustomFieldWriteSafetyGates,
    HostawayCustomFieldCollection,
    HostawayCustomFieldDefinition,
    ListingCustomFieldEvidenceState,
    ReservationCustomFieldEvidenceState,
    build_custom_field_values_payload,
    canonical_snapshot_differences,
    definitions_for_object_type,
    lookup_definition_by_id,
    resolve_var_name,
    select_listing_payload_strategy,
    select_reservation_payload_strategy,
    validate_custom_field_value,
    validate_identifier,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayConnectionError,
    HostawayMutationResultError,
    HostawayRateLimitError,
    HostawayRateLimitWaitTimeout,
)
from custom_components.hostaway.api.models import HostawayListing, HostawayReservation
from custom_components.hostaway.const import CONF_SELECTED_LISTINGS
from custom_components.hostaway.sensor.custom_fields import (
    ListingCustomFieldKeyAllocation,
)
from custom_components.hostaway.services.helpers import _resolve_entry_data

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ValueResponseContext:
    """Shared context for custom-field value response key allocation."""

    hass: HomeAssistant
    entry_data: dict[str, Any]
    target_type: str
    target_id: int
    definitions: list[HostawayCustomFieldDefinition]
    allocation: ListingCustomFieldKeyAllocation | None = None


@dataclass(frozen=True)
class _PostWriteVerification:
    """Inputs required for mandatory post-write read-back verification."""

    target_type: str
    target_id: int
    custom_field_id: int
    value: Any
    before_snapshot: dict[str, Any]
    payload: dict[str, Any]
    after_snapshot: dict[str, Any]
    ambiguous_result: bool = False


def _call_data(call: ServiceCall | dict[str, Any]) -> dict[str, Any]:
    """Return service-call data from Home Assistant or direct tests."""
    if isinstance(call, dict):
        return call
    data: dict[str, Any] = dict(call.data)
    return data


async def async_handle_get_custom_fields(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return cached Hostaway custom-field definitions."""
    entry_data = _resolve_entry_data(hass, _call_data(call))
    coordinator = entry_data.get("custom_fields_coordinator")
    definitions = [] if coordinator is None else (coordinator.data or [])
    return {
        "custom_fields": [
            definition.as_service_dict()
            for definition in definitions
            if definition.object_type in SUPPORTED_OBJECT_TYPES
        ]
    }


async def async_handle_get_custom_field_values(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return custom-field values for one listing or reservation target."""
    data = _call_data(call)
    target_type = _validate_target_type(data.get("target_type"))
    entry_data = _resolve_entry_data(hass, data)
    target_id = validate_identifier(data.get("target_id"), "target_id")
    definitions = _entry_definitions(entry_data)
    collection = await _read_target_collection(entry_data, target_type, target_id)
    return cast(
        ServiceResponse,
        {
            "custom_fields": _custom_field_values_response(
                hass,
                entry_data,
                target_type,
                target_id,
                collection,
                definitions,
            )
        },
    )


async def async_handle_set_custom_field(
    hass: HomeAssistant, call: ServiceCall | dict[str, Any]
) -> ServiceResponse:
    """Set one verified Hostaway custom-field value without clobbering others."""
    data = _call_data(call)
    target_type = _validate_target_type(data.get("target_type"))
    entry_data = _resolve_entry_data(hass, data)
    target_id = validate_identifier(data.get("target_id"), "target_id")
    coordinator = entry_data.get("custom_fields_coordinator")
    if coordinator is not None and not getattr(
        coordinator,
        "last_refresh_succeeded",
        True,
    ):
        raise ServiceValidationError(
            "custom field definitions refresh failed; writes are disabled "
            "until the next successful refresh"
        )
    definition = _validate_set_request(data, entry_data, target_type)
    if definition is None:
        raise ServiceValidationError("custom field definition is required")
    gates = entry_data.get("custom_field_write_safety")
    if not isinstance(gates, CustomFieldWriteSafetyGates):
        gates = CustomFieldWriteSafetyGates()
    try:
        if target_type == LISTING_OBJECT_TYPE:
            strategy = select_listing_payload_strategy(
                gates,
                _listing_evidence(entry_data),
                account_id=_entry_account_id(entry_data),
                config_entry_id=_entry_config_entry_id(entry_data),
            )
        else:
            strategy = select_reservation_payload_strategy(
                gates,
                _reservation_evidence(entry_data),
                account_id=_entry_account_id(entry_data),
                config_entry_id=_entry_config_entry_id(entry_data),
            )
    except CustomFieldMergeError as exc:
        target = "listing" if target_type == LISTING_OBJECT_TYPE else "reservation"
        raise ServiceValidationError(
            f"{target} custom-field writes are disabled until live safety "
            f"verification passes: {exc}"
        ) from exc
    lock = _write_lock(entry_data, target_type, target_id)
    async with lock:
        value = validate_custom_field_value(definition, data["value"])
        result = await _write_custom_field(
            entry_data,
            target_type,
            target_id,
            definition.custom_field_id,
            value,
            strategy,
        )
    _patch_local_state(
        entry_data,
        target_type,
        target_id,
        result,
    )
    _advance_generation(entry_data, target_type, target_id)
    addressed_by = "customFieldId" if "customFieldId" in data else "varName"
    return cast(
        ServiceResponse,
        {
            "target_type": target_type,
            "target_id": target_id,
            "customFieldId": definition.custom_field_id,
            "varName": definition.var_name,
            "addressed_by": addressed_by,
            "result": "success",
        },
    )


def _validate_target_type(value: object) -> str:
    """Return a supported target type or raise a service validation error."""
    if value not in {LISTING_OBJECT_TYPE, RESERVATION_OBJECT_TYPE}:
        raise ServiceValidationError("target_type must be listing or reservation")
    return str(value)


def _entry_definitions(
    entry_data: dict[str, Any],
) -> list[HostawayCustomFieldDefinition]:
    """Return cached custom-field definitions for a runtime entry."""
    coordinator = entry_data.get("custom_fields_coordinator")
    if coordinator is None:
        return []
    definitions: list[HostawayCustomFieldDefinition] = list(
        getattr(coordinator, "data", None) or []
    )
    return definitions


async def _read_target_collection(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> HostawayCustomFieldCollection:
    """Read one Hostaway target via the public client abstraction."""
    api_client = entry_data.get("api_client")
    if api_client is None:
        raise ServiceValidationError("Hostaway API client is not available")
    try:
        if target_type == LISTING_OBJECT_TYPE:
            listing = await api_client.get_listing(target_id)
            collection = cast(
                HostawayCustomFieldCollection | None,
                listing.custom_field_collection,
            )
        else:
            reservation = await api_client.get_reservation(target_id)
            collection = cast(
                HostawayCustomFieldCollection | None,
                reservation.custom_field_collection,
            )
    except HostawayApiError as exc:
        raise ServiceValidationError(
            f"Unable to read {target_type} {target_id}: {exc}"
        ) from exc
    if collection is None:
        return HostawayCustomFieldCollection.from_object({})
    return collection


async def _read_target_model(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> HostawayListing | HostawayReservation:
    """Read one target object for write merge or post-write verification."""
    api_client = entry_data.get("api_client")
    if api_client is None:
        raise ServiceValidationError("Hostaway API client is not available")
    try:
        if target_type == LISTING_OBJECT_TYPE:
            listing: HostawayListing = await api_client.get_listing(target_id)
            result: HostawayListing | HostawayReservation = listing
        else:
            result = await api_client.get_reservation(target_id)
    except HostawayApiError as exc:
        raise ServiceValidationError(
            f"Unable to read {target_type} {target_id}: {exc}"
        ) from exc
    return result


# aislop-ignore-next-line complexity/function-too-long -- cohesive write transaction
async def _write_custom_field(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    custom_field_id: int,
    value: Any,
    strategy: str,
) -> HostawayListing | HostawayReservation:
    """Run read-modify-write and mandatory read-back verification."""
    if strategy != "partial":
        raise ServiceValidationError("full-object writes are not enabled")
    for attempt in range(2):
        before_snapshot, _before = await _read_target_snapshot(
            entry_data,
            target_type,
            target_id,
        )
        try:
            payload = build_custom_field_values_payload(
                before_snapshot,
                custom_field_id,
                value,
            )
        except CustomFieldMergeError as exc:
            raise ServiceValidationError(str(exc)) from exc
        try:
            await _send_custom_field_update(
                entry_data,
                target_type,
                target_id,
                payload,
            )
            break
        except HostawayMutationResultError as exc:
            _LOGGER.warning(
                "Hostaway returned a successful but malformed mutation response "
                "for %s %s; continuing with mandatory read-back: %s",
                target_type,
                target_id,
                exc,
            )
            break
        except HostawayRateLimitWaitTimeout as exc:
            # Our own pacing gave up, so the request never left. Sleeping
            # and running the read/merge/write again would spend the next
            # window's budget re-asking a question nobody answered.
            raise ServiceValidationError(
                f"Unable to update {target_type} {target_id}: the "
                f"integration is pacing itself to stay within Hostaway's "
                f"rate limit and ran out of time waiting for capacity "
                f"({exc})"
            ) from exc
        except HostawayRateLimitError as exc:
            if attempt >= 1:
                raise ServiceValidationError(
                    f"Unable to update {target_type} {target_id}: {exc}"
                ) from exc
            await asyncio.sleep(_rate_limit_retry_delay(exc.retry_after))
            continue
        except HostawayConnectionError:
            try:
                after_snapshot, after = await _read_target_snapshot(
                    entry_data,
                    target_type,
                    target_id,
                )
            except ServiceValidationError as exc:
                raise ServiceValidationError(
                    f"Hostaway {target_type} {target_id} custom-field write "
                    "had an ambiguous transport/server result, and mandatory "
                    f"read-back failed: {exc}. Do not retry until the target "
                    "is manually verified or a fresh read succeeds."
                ) from exc
            _verify_post_write_readback(
                _PostWriteVerification(
                    target_type=target_type,
                    target_id=target_id,
                    custom_field_id=custom_field_id,
                    value=value,
                    before_snapshot=before_snapshot,
                    payload=payload,
                    after_snapshot=after_snapshot,
                    ambiguous_result=True,
                )
            )
            return after
        except (AttributeError, HostawayApiError) as exc:
            raise ServiceValidationError(
                f"Unable to update {target_type} {target_id}: {exc}"
            ) from exc
    else:  # pragma: no cover
        raise ServiceValidationError(
            f"Unable to update {target_type} {target_id}: retry loop exhausted"
        )
    try:
        after_snapshot, after = await _read_target_snapshot(
            entry_data,
            target_type,
            target_id,
        )
    except ServiceValidationError as exc:
        raise ServiceValidationError(
            f"Hostaway {target_type} {target_id} custom-field write response "
            f"was received, but mandatory read-back failed: {exc}. No success "
            "was reported; verify current Hostaway state before retrying."
        ) from exc
    _verify_post_write_readback(
        _PostWriteVerification(
            target_type=target_type,
            target_id=target_id,
            custom_field_id=custom_field_id,
            value=value,
            before_snapshot=before_snapshot,
            payload=payload,
            after_snapshot=after_snapshot,
        )
    )
    return after


async def _send_custom_field_update(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    payload: dict[str, Any],
) -> None:
    """Send one no-retry custom-field update mutation."""
    api_client = entry_data.get("api_client")
    if api_client is None:
        raise ServiceValidationError("Hostaway API client is not available")
    if target_type == LISTING_OBJECT_TYPE:
        await api_client.update_listing_custom_fields(target_id, payload)
    else:
        await api_client.update_reservation_custom_fields(target_id, payload)


def _rate_limit_retry_delay(retry_after: float | None) -> float:
    """Return a bounded service-level 429 retry delay."""
    if retry_after is not None:
        if retry_after > MAX_BACKOFF:
            raise ServiceValidationError(
                "Hostaway rate-limited the custom-field write with a "
                f"Retry-After delay of {retry_after:g} seconds. No retry was "
                "attempted; try again after the advertised delay."
            )
        return max(retry_after, 0.1)
    return min(INITIAL_BACKOFF, MAX_BACKOFF)


async def _read_target_snapshot(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> tuple[dict[str, Any], HostawayListing | HostawayReservation]:
    """Read one raw target snapshot and parsed model for verification."""
    api_client = entry_data.get("api_client")
    if api_client is None:
        raise ServiceValidationError("Hostaway API client is not available")
    try:
        if target_type == LISTING_OBJECT_TYPE:
            snapshot: dict[str, Any] = await api_client.get_listing_payload(target_id)
            result: HostawayListing | HostawayReservation = (
                HostawayListing.from_api_response(snapshot)
            )
        else:
            snapshot = await api_client.get_reservation_payload(target_id)
            result = HostawayReservation.from_api_response(snapshot)
    except (AttributeError, HostawayApiError, ValueError) as exc:
        raise ServiceValidationError(
            f"Unable to read {target_type} {target_id}: {exc}"
        ) from exc
    return snapshot, result


def _verify_post_write_readback(verification: _PostWriteVerification) -> None:
    """Verify Hostaway persisted only the requested custom-field change."""
    if (
        _snapshot_value(
            verification.after_snapshot,
            verification.custom_field_id,
        )
        != verification.value
    ):
        if verification.ambiguous_result:
            raise ServiceValidationError(
                f"Hostaway {verification.target_type} {verification.target_id} "
                f"customFieldId {verification.custom_field_id} write returned an "
                "ambiguous transport/server result, and mandatory read-back did "
                "not show the custom-field value. No retry was attempted because "
                "the original whole-array mutation outcome is unknown."
            )
        raise ServiceValidationError(
            f"Hostaway reported success updating {verification.target_type} "
            f"{verification.target_id} customFieldId "
            f"{verification.custom_field_id}, "
            "but the mandatory read-back did not show the custom-field value. "
            "This matches the known Hostaway success-without-custom-field-"
            "persistence bug; no success was reported."
        )
    expected = dict(verification.before_snapshot)
    expected["customFieldValues"] = verification.payload["customFieldValues"]
    differences = canonical_snapshot_differences(
        expected,
        verification.after_snapshot,
    )
    if differences:
        raise ServiceValidationError(
            f"Hostaway {verification.target_type} {verification.target_id} "
            f"customFieldId {verification.custom_field_id} read-back changed "
            f"unrelated data at {', '.join(differences)}; "
            "recovery was not attempted because "
            "no verified conditional/version-protected recovery path is available"
        )


def _snapshot_value(snapshot: dict[str, Any], custom_field_id: int) -> Any:
    """Return one custom-field value from a snapshot."""
    values = snapshot.get("customFieldValues")
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, dict) and item.get("customFieldId") == custom_field_id:
            return item.get("value")
    return None


def _custom_field_values_response(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    collection: HostawayCustomFieldCollection,
    definitions: list[HostawayCustomFieldDefinition],
) -> dict[str, dict[str, Any]]:
    """Build the exact get_custom_field_values response mapping."""
    object_definitions = definitions_for_object_type(definitions, target_type)
    definition_by_id = {
        definition.custom_field_id: definition for definition in object_definitions
    }
    custom_field_ids = set(definition_by_id)
    custom_field_ids.update(collection.values)
    result: dict[str, dict[str, Any]] = {}
    context = _value_response_context(
        hass,
        entry_data,
        target_type,
        target_id,
        object_definitions,
    )
    for custom_field_id in sorted(custom_field_ids):
        definition = definition_by_id.get(custom_field_id)
        value = collection.values.get(custom_field_id)
        key = _response_key(
            context,
            custom_field_id,
            definition,
        )
        if definition is None:
            result[key] = {
                "customFieldId": custom_field_id,
                "value": None if value is None else value.value,
                "resolved": False,
            }
            continue
        result[key] = {
            "customFieldId": custom_field_id,
            "varName": definition.var_name,
            "name": definition.name,
            "type": definition.field_type,
            "possibleValues": list(definition.possible_values),
            "value": None if value is None else value.value,
            "resolved": True,
        }
    return result


def _response_key(
    context: _ValueResponseContext,
    custom_field_id: int,
    definition: HostawayCustomFieldDefinition | None,
) -> str:
    """Return a response key for listing or reservation custom-field values."""
    if context.target_type == RESERVATION_OBJECT_TYPE:
        return f"custom_field_{custom_field_id}"
    if context.allocation is None:
        raise ServiceValidationError("Listing key allocation is not available")
    return context.allocation.allocate(
        custom_field_id,
        definition,
        context.definitions,
    )


def _value_response_context(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    definitions: list[HostawayCustomFieldDefinition],
) -> _ValueResponseContext:
    """Return response context with one non-mutating listing allocation."""
    allocation = None
    if target_type == LISTING_OBJECT_TYPE:
        allocation = _listing_allocation_preview(
            hass,
            entry_data,
            target_id,
        )
    return _ValueResponseContext(
        hass=hass,
        entry_data=entry_data,
        target_type=target_type,
        target_id=target_id,
        definitions=definitions,
        allocation=allocation,
    )


def _listing_allocation_preview(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    listing_id: int,
) -> ListingCustomFieldKeyAllocation:
    """Return a non-persistent allocation preview for a listing response."""
    allocations = cast(
        dict[int, ListingCustomFieldKeyAllocation],
        entry_data.get("custom_field_key_allocations", {}),
    )
    allocation = allocations.get(listing_id)
    if allocation is not None:
        return allocation.clone()
    entry = getattr(entry_data.get("listings_coordinator"), "config_entry", None)
    if entry is None:
        return ListingCustomFieldKeyAllocation(listing_id=listing_id)
    return ListingCustomFieldKeyAllocation.from_entity_registry(
        hass,
        entry,
        listing_id,
    )


def _write_lock(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> asyncio.Lock:
    """Return the per-entry, per-target write lock."""
    registry = entry_data.setdefault("custom_field_write_locks", None)
    if registry is None:
        locks: dict[tuple[str, int], asyncio.Lock] = {}
        entry_data["custom_field_write_locks"] = type(
            "_RuntimeWriteLocks",
            (),
            {"locks": locks},
        )()
        registry = entry_data["custom_field_write_locks"]
    locks = cast(dict[tuple[str, int], asyncio.Lock], registry.locks)
    return locks.setdefault((target_type, target_id), asyncio.Lock())


def _advance_generation(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
) -> None:
    """Advance the write generation counter when available."""
    registry = entry_data.get("custom_field_write_generations")
    if registry is not None and hasattr(registry, "advance"):
        registry.advance(target_type, target_id)


def _reservation_evidence(
    entry_data: dict[str, Any],
) -> ReservationCustomFieldEvidenceState | None:
    """Return account-bound reservation evidence for this entry if present."""
    evidence = entry_data.get("reservation_custom_field_evidence")
    if isinstance(evidence, ReservationCustomFieldEvidenceState):
        return evidence
    return None


def _listing_evidence(
    entry_data: dict[str, Any],
) -> ListingCustomFieldEvidenceState | None:
    """Return account-bound listing evidence for this entry if present."""
    evidence = entry_data.get("listing_custom_field_evidence")
    if isinstance(evidence, ListingCustomFieldEvidenceState):
        return evidence
    return None


def _entry_account_id(entry_data: dict[str, Any]) -> int | None:
    """Return the verified account id associated with runtime data."""
    value = entry_data.get("account_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    observed_ids = {
        definition.account_id for definition in _entry_definitions(entry_data)
    }
    if None in observed_ids or observed_ids != {value}:
        return None
    return value


def _entry_config_entry_id(entry_data: dict[str, Any]) -> str | None:
    """Return the config entry id associated with runtime data."""
    entry = getattr(entry_data.get("listings_coordinator"), "config_entry", None)
    entry_id = getattr(entry, "entry_id", None)
    if isinstance(entry_id, str):
        return entry_id
    value = entry_data.get("config_entry_id")
    if isinstance(value, str):
        return value
    return None


def _patch_local_state(
    entry_data: dict[str, Any],
    target_type: str,
    target_id: int,
    target: HostawayListing | HostawayReservation,
) -> None:
    """Patch coordinator data after a verified write read-back."""
    if target_type == LISTING_OBJECT_TYPE and isinstance(target, HostawayListing):
        coordinator = entry_data.get("listings_coordinator")
        data = getattr(coordinator, "data", None)
        if isinstance(data, dict) and (
            target_id in data or target_id in _selected_listing_ids(coordinator)
        ):
            updated = dict(data)
            updated[target_id] = target
            _publish_coordinator_data(coordinator, updated)
        return
    if target_type != RESERVATION_OBJECT_TYPE or not isinstance(
        target,
        HostawayReservation,
    ):
        return
    coordinator = entry_data.get("reservations_coordinator")
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict):
        return
    updated_reservations: dict[int, list[HostawayReservation]] = {}
    changed = False
    for listing_id, reservations in data.items():
        patched: list[HostawayReservation] = []
        for reservation in reservations:
            if reservation.id == target_id:
                patched.append(target)
                changed = True
            else:
                patched.append(reservation)
        updated_reservations[listing_id] = patched
    if changed:
        _publish_coordinator_data(coordinator, updated_reservations)


def _publish_coordinator_data(coordinator: Any, data: Any) -> None:
    """Publish coordinator data using HA helper when available."""
    if hasattr(coordinator, "async_set_updated_data"):
        coordinator.async_set_updated_data(data)
    else:
        coordinator.data = data


def _selected_listing_ids(coordinator: Any) -> set[int]:
    """Return selected listing ids from a coordinator config entry."""
    entry = getattr(coordinator, "config_entry", None)
    raw_ids = getattr(entry, "data", {}).get(CONF_SELECTED_LISTINGS, [])
    return {
        item
        for item in raw_ids
        if not isinstance(item, bool) and isinstance(item, int) and item > 0
    }


def _validate_set_request(
    data: dict[str, Any],
    entry_data: dict[str, Any],
    target_type: str,
) -> HostawayCustomFieldDefinition | None:
    """Validate set_custom_field addressing and local value constraints."""
    has_id = "customFieldId" in data
    has_var_name = "varName" in data
    if has_id == has_var_name:
        raise ServiceValidationError("exactly one field identifier is required")
    if "value" not in data:
        raise ServiceValidationError("value is required")
    definitions = _entry_definitions(entry_data)
    if not definitions:
        raise ServiceValidationError(
            "custom field definitions are unavailable; writes are disabled"
        )
    try:
        if has_id:
            definition = lookup_definition_by_id(
                definitions,
                validate_identifier(data.get("customFieldId"), "customFieldId"),
                target_type,
            )
        else:
            definition = resolve_var_name(
                definitions,
                data.get("varName"),
                target_type,
            )
        validate_custom_field_value(definition, data["value"])
    except (CustomFieldDefinitionError, ValueError) as exc:
        raise ServiceValidationError(str(exc)) from exc
    return definition
