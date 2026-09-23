# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the guarded custom-field live verification helper."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from scripts.verify_custom_field_writes import (
    compare_unrelated,
    custom_field_value,
    redact,
    snapshot_path,
)


def test_script_uses_runtime_token() -> None:
    """Verification helper builds the auth header from runtime credentials."""
    source = (
        __import__("pathlib").Path("scripts/verify_custom_field_writes.py").read_text()
    )

    assert '"Bearer " + token' in source


def test_redaction_hides_string_values() -> None:
    """Logs redact sensitive custom-field values."""
    assert redact({"customFieldValues": [{"value": "secret"}]}) == {
        "customFieldValues": [{"value": "<redacted>"}]
    }


def test_snapshot_path_is_outside_git() -> None:
    """Private snapshots are not stored in the repository."""
    path = snapshot_path("listing", 123)

    assert ".hostaway" in path.parts
    assert "custom-field-write-snapshots" in path.parts


def test_compare_unrelated_detects_changes() -> None:
    """Verification compares unrelated custom values and built-ins."""
    before = {
        "name": "Listing",
        "customFieldValues": [{"customFieldId": 1, "value": "a"}],
    }
    after = {
        "name": "Changed",
        "customFieldValues": [{"customFieldId": 1, "value": "b"}],
    }

    assert compare_unrelated(before, after, 2)


def test_custom_field_value_reads_addressed_value() -> None:
    """Verification checks that the target field actually changed."""
    data = {"customFieldValues": [{"customFieldId": 7, "value": "changed"}]}

    assert custom_field_value(data, 7) == "changed"


def test_compare_unrelated_checks_all_built_ins() -> None:
    """Verification compares every visible built-in field from the snapshot."""
    before = {"price": 100, "customFieldValues": [{"customFieldId": 1, "value": "a"}]}
    after = {"price": 200, "customFieldValues": [{"customFieldId": 1, "value": "b"}]}

    assert compare_unrelated(before, after, 1) == ["built-in fields changed"]
