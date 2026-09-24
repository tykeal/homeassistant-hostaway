# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Hostaway custom-field foundation API."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from custom_components.hostaway.api.custom_fields import (
    CUSTOM_FIELD_PAGE_LIMIT,
    CustomFieldDefinitionError,
    CustomFieldMergeError,
    CustomFieldWriteSafetyGates,
    HostawayCustomFieldCollection,
    HostawayCustomFieldDefinition,
    HostawayCustomFieldValue,
    build_custom_field_values_payload,
    fetch_custom_field_definitions,
    lookup_definition_by_id,
    read_listing_with_custom_fields,
    read_reservation_with_custom_fields,
    resolve_var_name,
    validate_custom_field_value,
    validate_identifier,
)


def _definition(**overrides: Any) -> dict[str, Any]:
    """Return a valid custom-field definition fixture."""
    data = {
        "id": 12,
        "accountId": 99,
        "name": "Parking Bay",
        "varName": "parking_bay",
        "possibleValues": None,
        "type": "text",
        "objectType": "listing",
        "isPublic": 0,
        "sortOrder": 10,
    }
    data.update(overrides)
    return data


def test_write_safety_gates_default_off() -> None:
    """Both live write gates default to disabled."""
    gates = CustomFieldWriteSafetyGates()

    assert gates.listing_partial_put_verified is False
    assert gates.reservation_no_clobber_verified is False


@pytest.mark.parametrize("bad", [True, False, 0, -1, "1", 1.2])
def test_validate_identifier_rejects_bad_values(bad: object) -> None:
    """Identifier validation rejects bools and non-positive values."""
    with pytest.raises(ValueError):
        validate_identifier(bad, "customFieldId")


def test_definition_parses_listing_hidden_dropdown() -> None:
    """Definition parser maps Hostaway fields and keeps hidden fields."""
    parsed = HostawayCustomFieldDefinition.from_api_dict(
        _definition(
            type="dropdown",
            possibleValues=["A", "B"],
            isPublic=0,
            objectType="reservation",
        )
    )

    assert parsed is not None
    assert parsed.custom_field_id == 12
    assert parsed.object_type == "reservation"
    assert parsed.possible_values == ["A", "B"]
    assert parsed.is_public is False
    assert parsed.as_service_dict()["customFieldId"] == 12


def test_definition_ignores_task_and_preserves_unknown_type() -> None:
    """Task definitions are ignored and future field types are preserved."""
    assert (
        HostawayCustomFieldDefinition.from_api_dict(_definition(objectType="task"))
        is None
    )

    parsed = HostawayCustomFieldDefinition.from_api_dict(_definition(type="future"))

    assert parsed is not None
    assert parsed.field_type == "future"


def test_definition_skips_malformed_and_bool_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed definitions log warnings without raising."""
    with caplog.at_level("WARNING"):
        parsed = HostawayCustomFieldDefinition.from_api_dict(_definition(id=True))

    assert parsed is None
    assert "Skipping malformed Hostaway custom field" in caplog.text


@pytest.mark.parametrize("is_public", [None, True, False, "0", 1.0, 0.0, 2])
def test_definition_rejects_malformed_is_public(is_public: object) -> None:
    """Definition parser rejects non-0/1 isPublic values."""
    data = _definition()
    if is_public is None:
        del data["isPublic"]
    else:
        data["isPublic"] = is_public

    assert HostawayCustomFieldDefinition.from_api_dict(data) is None


def test_value_collection_states_and_raw_preservation() -> None:
    """Collections distinguish four states and retain raw malformed entries."""
    raw = [
        {"customFieldId": 1, "value": "A"},
        {"customFieldId": 2, "value": None},
        {"customFieldId": 3},
        "bad",
    ]
    collection = HostawayCustomFieldCollection.from_object({"customFieldValues": raw})

    assert collection.state == "present"
    assert collection.values[1].value == "A"
    assert collection.values[2].value is None
    assert collection.raw_entries == raw
    assert collection.malformed_entries == [{"customFieldId": 3}, "bad"]
    assert HostawayCustomFieldCollection.from_object({}).state == "missing"
    null_collection = HostawayCustomFieldCollection.from_object(
        {"customFieldValues": None}
    )
    assert null_collection.state == "null"
    invalid = HostawayCustomFieldCollection.from_object({"customFieldValues": "bad"})
    assert invalid.state == "invalid"
    assert invalid.invalid_raw == "bad"


def test_value_rejects_bool_custom_field_id() -> None:
    """Value parser rejects bool customFieldId values."""
    assert (
        HostawayCustomFieldValue.from_api_entry({"customFieldId": True, "value": "bad"})
        is None
    )


def test_duplicate_and_addressed_malformed_preflight() -> None:
    """Payload builder fails closed for duplicate or malformed addressed ids."""
    with pytest.raises(CustomFieldMergeError, match="malformed"):
        build_custom_field_values_payload(
            {"customFieldValues": [{"customFieldId": 4}]}, 4, "new"
        )
    with pytest.raises(CustomFieldMergeError, match="duplicate"):
        build_custom_field_values_payload(
            {
                "customFieldValues": [
                    {"customFieldId": 4, "value": "old"},
                    {"customFieldId": 4, "value": "other"},
                ]
            },
            4,
            "new",
        )


@pytest.mark.parametrize(
    "current",
    [{}, {"customFieldValues": None}, {"customFieldValues": "bad"}],
)
def test_payload_builder_requires_present_list(current: dict[str, Any]) -> None:
    """Missing, null, or invalid collections are not writable."""
    with pytest.raises(CustomFieldMergeError):
        build_custom_field_values_payload(current, 4, "new")


def test_payload_builder_preserves_unaddressed_and_exact_keys() -> None:
    """Merge preserves unaddressed raw entries and sends one top-level key."""
    malformed = {"customFieldId": 99}
    payload = build_custom_field_values_payload(
        {
            "name": "Do not send",
            "customFieldValues": [
                {"customFieldId": 1, "value": "keep"},
                malformed,
                {"customFieldId": 2, "value": "old"},
            ],
        },
        2,
        None,
    )

    assert set(payload) == {"customFieldValues"}
    assert payload["customFieldValues"] == [
        {"customFieldId": 1, "value": "keep"},
        malformed,
        {"customFieldId": 2, "value": None},
    ]


def test_payload_builder_appends_to_empty_collection() -> None:
    """A present empty customFieldValues list is writable."""
    assert build_custom_field_values_payload({"customFieldValues": []}, 7, "x") == {
        "customFieldValues": [{"customFieldId": 7, "value": "x"}]
    }


async def test_fetch_custom_field_definitions_paginates_and_filters_task() -> None:
    """Definition fetch uses limit=500 and skips task definitions."""
    calls: list[dict[str, Any]] = []

    async def request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Return paginated custom-field definition responses."""
        calls.append(kwargs["params"])
        result = [_definition(id=1), _definition(id=2, objectType="task")]
        if kwargs["params"]["offset"]:
            result = [_definition(id=3, objectType="reservation")]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "result": result,
                "limit": CUSTOM_FIELD_PAGE_LIMIT,
                "page": len(calls),
                "totalPages": 2,
            },
        )

    definitions = await fetch_custom_field_definitions(request)

    assert [call["limit"] for call in calls] == [500, 500]
    assert [call["offset"] for call in calls] == [0, 500]
    assert [definition.custom_field_id for definition in definitions] == [1, 3]


async def test_direct_reads_include_resources() -> None:
    """Direct listing and reservation reads request includeResources=1."""
    request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "result": {"id": 1}},
        )
    )

    await read_listing_with_custom_fields(request, 1)
    await read_reservation_with_custom_fields(request, 2)

    assert request.await_args_list[0].kwargs["params"] == {"includeResources": 1}
    assert request.await_args_list[1].kwargs["params"] == {"includeResources": 1}


def test_malformed_value_warning_redacts_raw_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed value logs skip presentation while preserving raw data."""
    raw = {"customFieldId": True, "value": "secret-gate-code"}

    with caplog.at_level("WARNING"):
        collection = HostawayCustomFieldCollection.from_object(
            {"customFieldValues": [raw, {"customFieldId": 9, "value": "ok"}]}
        )

    assert collection.values[9].value == "ok"
    assert collection.raw_entries[0] == raw
    assert collection.malformed_entries == [raw]
    assert "secret-gate-code" not in caplog.text


def test_definition_lookup_scopes_by_object_type() -> None:
    """Definition lookup resolves ids only for the requested object type."""
    listing = HostawayCustomFieldDefinition.from_api_dict(_definition(id=1))
    reservation = HostawayCustomFieldDefinition.from_api_dict(
        _definition(id=1, objectType="reservation", varName="door_note")
    )
    assert listing is not None
    assert reservation is not None
    definitions = [listing, reservation]

    assert lookup_definition_by_id(definitions, 1, "listing") is listing
    assert lookup_definition_by_id(definitions, 1, "reservation") is reservation
    with pytest.raises(CustomFieldDefinitionError, match="unknown"):
        lookup_definition_by_id(definitions, 2, "listing")


def test_resolve_var_name_rejects_ambiguous_and_unknown() -> None:
    """varName resolution is scoped and rejects ambiguous matches."""
    first = HostawayCustomFieldDefinition.from_api_dict(_definition(id=1))
    second = HostawayCustomFieldDefinition.from_api_dict(_definition(id=2))
    reservation = HostawayCustomFieldDefinition.from_api_dict(
        _definition(id=3, objectType="reservation")
    )
    assert first is not None
    assert second is not None
    assert reservation is not None

    assert resolve_var_name([first, reservation], "parking_bay", "listing") is first
    with pytest.raises(CustomFieldDefinitionError, match="ambiguous"):
        resolve_var_name([first, second], "parking_bay", "listing")
    with pytest.raises(CustomFieldDefinitionError, match="unknown"):
        resolve_var_name([reservation], "parking_bay", "listing")


@pytest.mark.parametrize(
    ("field_type", "possible_values", "good", "bad"),
    [
        ("text", [], "hello", 1),
        ("textarea", [], "hello", 1),
        ("number", [], 1.5, True),
        ("dropdown", ["A", "B"], " A ", "C"),
    ],
)
def test_validate_custom_field_value_known_types(
    field_type: str,
    possible_values: list[str],
    good: object,
    bad: object,
) -> None:
    """Known custom-field types validate locally before writes."""
    definition = HostawayCustomFieldDefinition.from_api_dict(
        _definition(type=field_type, possibleValues=possible_values)
    )
    assert definition is not None

    expected = "A" if field_type == "dropdown" else good
    assert validate_custom_field_value(definition, good) == expected
    assert validate_custom_field_value(definition, None) is None
    with pytest.raises(CustomFieldDefinitionError):
        validate_custom_field_value(definition, bad)


def test_validate_custom_field_value_future_type_passthrough() -> None:
    """Unknown future field types pass through for Hostaway validation."""
    definition = HostawayCustomFieldDefinition.from_api_dict(_definition(type="json"))
    assert definition is not None

    assert validate_custom_field_value(definition, {"x": 1}) == {"x": 1}
