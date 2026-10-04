# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the guarded custom-field live verification helper."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

import shutil
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_custom_field_writes import (
    compare_complete_snapshots,
    compare_unrelated,
    custom_field_value,
    redact,
    snapshot_path,
    validate_restore_payload,
    verify,
)


def test_script_uses_runtime_token() -> None:
    """Verification helper builds the auth header from runtime credentials."""
    source = (
        __import__("pathlib").Path("scripts/verify_custom_field_writes.py").read_text()
    )

    assert '"Bearer " + token' in source


def test_redaction_hides_string_values() -> None:
    """Logs redact sensitive custom-field values."""
    assert redact({"customFieldValues": [{"customFieldId": 1, "value": 123}]}) == {
        "customFieldValues": [{"customFieldId": "<redacted>", "value": "<redacted>"}]
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


def test_compare_complete_snapshots_normalizes_volatile_fields() -> None:
    """Complete comparison ignores server-managed update timestamps."""
    before = {"id": 1, "updatedAt": "old", "name": "Task"}
    after = {"id": 1, "updatedAt": "new", "name": "Changed"}

    assert compare_complete_snapshots(before, after) == ["$.name"]


def test_validate_restore_payload_refuses_raw_get_copy() -> None:
    """Restore validation rebuilds an allowlisted payload."""
    snapshot = {"id": 1, "name": "Listing", "customFieldValues": []}

    assert validate_restore_payload(snapshot, "listing") == {
        "name": "Listing",
        "customFieldValues": [],
    }


def test_compare_unrelated_handles_malformed_raw_entries() -> None:
    """Malformed preserved raw entries do not crash comparisons."""
    before = {"customFieldValues": ["legacy", {"customFieldId": 1, "value": "a"}]}
    after = {"customFieldValues": ["legacy", {"customFieldId": 1, "value": "b"}]}

    assert compare_unrelated(before, after, 1) == []


async def test_verify_restores_complete_snapshot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper restores the full snapshot after unrelated changes."""
    before = {
        "id": 10,
        "price": 100,
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    after = {
        "id": 10,
        "price": 200,
        "customFieldValues": [{"customFieldId": 1, "value": "new"}],
    }
    calls: list[dict[str, Any]] = []
    reads = [before, after, before]

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return scripted Hostaway responses and capture writes."""
        method = args[1]
        if method == "GET":
            return reads.pop(0)
        calls.append(kwargs["json"])
        return {"id": 10}

    snapshot = Path(".verify-test-artifacts/restore-failure/listing-10.json")
    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MUTATE listing 10")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        with pytest.raises(RuntimeError, match="complete snapshot changed"):
            await verify(
                Namespace(
                    target_type="listing",
                    target_id=10,
                    custom_field_id=1,
                    value="new",
                    mutate=True,
                )
            )
    finally:
        shutil.rmtree(snapshot.parent.parent, ignore_errors=True)

    assert calls[0] == {"customFieldValues": [{"customFieldId": 1, "value": "new"}]}
    assert calls[1] == {
        "price": 100,
        "customFieldValues": before["customFieldValues"],
    }


async def test_verify_detects_addressed_entry_metadata_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete comparison fails when addressed raw entry metadata is lost."""
    before = {
        "id": 10,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old", "metadata": "keep"},
            {"customFieldId": 2, "value": "other"},
        ],
    }
    after = {
        "id": 10,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "other"},
        ],
    }
    calls: list[dict[str, Any]] = []
    reads = [before, after, before]
    snapshot = Path(".verify-test-artifacts/metadata/listing-10.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return scripted Hostaway responses and capture writes."""
        if args[1] == "GET":
            return reads.pop(0)
        calls.append(kwargs["json"])
        return {"id": 10}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MUTATE listing 10")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        with pytest.raises(RuntimeError, match="complete snapshot changed"):
            await verify(
                Namespace(
                    target_type="listing",
                    target_id=10,
                    custom_field_id=1,
                    value="new",
                    mutate=True,
                )
            )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert calls[1] == {"customFieldValues": before["customFieldValues"]}


async def test_verify_sends_no_mutation_on_preflight_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper sends no PUT when payload preflight fails."""
    calls: list[str] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return an unsafe object and record request methods."""
        del kwargs
        method = args[1]
        calls.append(method)
        return {"id": 10}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)

    with pytest.raises(ValueError, match="customFieldValues"):
        await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=1,
                value="new",
                mutate=True,
            )
        )

    assert calls == ["GET"]


async def test_verify_writes_private_snapshot_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper stores private rollback snapshots before confirmation."""
    before = {"id": 10, "customFieldValues": [{"customFieldId": 1, "value": "old"}]}
    snapshot_root = Path(".verify-test-artifacts/permissions")
    snapshot = snapshot_root / "listing-10.json"
    snapshot_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    snapshot_root.chmod(0o755)

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return the initial object and fail if mutation is attempted."""
        del kwargs
        if args[1] == "GET":
            return before
        raise AssertionError("mutation should not be sent before confirmation")

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        with pytest.raises(RuntimeError, match="confirmation did not match"):
            await verify(
                Namespace(
                    target_type="listing",
                    target_id=10,
                    custom_field_id=1,
                    value="new",
                    mutate=True,
                )
            )

        assert snapshot.parent.stat().st_mode & 0o777 == 0o700
        assert snapshot.stat().st_mode & 0o777 == 0o600
    finally:
        shutil.rmtree(snapshot_root, ignore_errors=True)


async def test_snapshot_mode_is_read_only_and_records_redacted_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot mode writes no mutations and logs only redacted summaries."""
    before = {
        "id": 10,
        "name": "Secret Listing",
        "customFieldValues": [{"customFieldId": 1, "value": "secret"}],
    }
    calls: list[str] = []
    snapshot = Path(".verify-test-artifacts/snapshot/listing-10.json")
    summaries: list[dict[str, Any]] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return a listing snapshot and record methods."""
        del kwargs
        calls.append(args[1])
        return before

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.append_live_verification_summary",
        summaries.append,
    )

    try:
        result = await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=None,
                value=None,
                mutate=False,
                snapshot=True,
                task_canary=False,
            )
        )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert result == 0
    assert calls == ["GET"]
    assert summaries[0]["restore_payload_keys"] == ["customFieldValues", "name"]


async def test_task_canary_uses_production_restore_and_deletes_own_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task canary creates, mutates, restores, verifies, and deletes its task."""
    before = {
        "id": 42,
        "title": "Hostaway custom-field verification canary",
        "updatedAt": "before",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    after = {
        **before,
        "updatedAt": "after",
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    restored = {**before, "updatedAt": "restored"}
    reads = [before, after, restored]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    snapshot = Path(".verify-test-artifacts/task/task-42.json")
    summaries: list[dict[str, Any]] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return scripted task canary responses and record requests."""
        method = args[1]
        path = args[2]
        calls.append((method, path, kwargs.get("json")))
        if method == "POST":
            return {"id": 42}
        if method == "GET":
            return reads.pop(0)
        return {}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.append_live_verification_summary",
        summaries.append,
    )

    try:
        result = await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=1,
                value="new",
                original_value="old",
                unrelated_custom_field_id=2,
                listing_map_id=None,
                mutate=False,
                snapshot=False,
                task_canary=True,
            )
        )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert result == 0
    assert [call[0] for call in calls] == [
        "POST",
        "GET",
        "PUT",
        "GET",
        "PUT",
        "GET",
        "DELETE",
    ]
    assert calls[2][2] == {
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ]
    }
    assert calls[4][2] == {
        "title": before["title"],
        "customFieldValues": before["customFieldValues"],
    }
    assert summaries == [
        {"mode": "task-canary", "task_id": 42, "indicative_not_conclusive": True}
    ]


async def test_verify_restores_complete_when_target_does_not_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper keeps complete rollback default until checks pass."""
    before = {
        "id": 10,
        "price": 100,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    after = {
        "id": 10,
        "price": 100,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    calls: list[dict[str, Any]] = []
    reads = [before, after, before]
    snapshot = Path(".verify-test-artifacts/target/listing-10.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return scripted Hostaway responses and capture writes."""
        if args[1] == "GET":
            return reads.pop(0)
        calls.append(kwargs["json"])
        return {"id": 10}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MUTATE listing 10")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        with pytest.raises(RuntimeError, match="target custom field did not change"):
            await verify(
                Namespace(
                    target_type="listing",
                    target_id=10,
                    custom_field_id=1,
                    value="new",
                    mutate=True,
                )
            )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert calls[0] == {
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ]
    }
    assert calls[1] == {
        "price": 100,
        "customFieldValues": before["customFieldValues"],
    }


async def test_verify_restores_when_mutation_response_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper restores when a sent mutation raises."""
    before = {
        "id": 10,
        "price": 100,
        "customFieldValues": [{"customFieldId": 1, "value": "old"}],
    }
    calls: list[dict[str, Any]] = []
    reads = [before, before]
    snapshot = Path(".verify-test-artifacts/timeout/listing-10.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Raise for the mutation and capture the restore payload."""
        if args[1] == "GET":
            return reads.pop(0)
        calls.append(kwargs["json"])
        if len(calls) == 1:
            raise RuntimeError("request timed out")
        return {"id": 10}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MUTATE listing 10")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        with pytest.raises(RuntimeError, match="request timed out"):
            await verify(
                Namespace(
                    target_type="listing",
                    target_id=10,
                    custom_field_id=1,
                    value="new",
                    mutate=True,
                )
            )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert calls[0] == {"customFieldValues": [{"customFieldId": 1, "value": "new"}]}
    assert calls[1] == {
        "price": 100,
        "customFieldValues": before["customFieldValues"],
    }


async def test_verify_uses_allowlisted_restore_after_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live helper restores through the allowlisted production path."""
    before = {
        "id": 10,
        "price": 100,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    after = {
        "id": 10,
        "price": 100,
        "customFieldValues": [
            {"customFieldId": 1, "value": "new"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    calls: list[dict[str, Any]] = []
    reads = [before, after, before]
    snapshot = Path(".verify-test-artifacts/success/listing-10.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return scripted Hostaway responses and capture writes."""
        if args[1] == "GET":
            return reads.pop(0)
        calls.append(kwargs["json"])
        return {"id": 10}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MUTATE listing 10")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )

    try:
        result = await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=1,
                value="new",
                mutate=True,
            )
        )
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    assert result == 0
    assert calls[1] == {
        "price": 100,
        "customFieldValues": before["customFieldValues"],
    }
