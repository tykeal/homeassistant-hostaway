# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the guarded custom-field live verification helper."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

import shutil
from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.verify_custom_field_writes import (
    compare_complete_snapshots,
    compare_unrelated,
    custom_field_value,
    redact,
    sanitize_summary,
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


def test_summary_sanitizer_preserves_protocol_metadata() -> None:
    """Evidence summaries keep protocol state while redacting identifiers."""
    assert sanitize_summary(
        {
            "mode": "task-canary",
            "task_id": 42,
            "restore_payload_keys": ["title", "customFieldValues"],
            "indicative_not_conclusive": True,
        }
    ) == {
        "mode": "task-canary",
        "task_id": "<redacted>",
        "restore_payload_keys": ["title", "customFieldValues"],
        "indicative_not_conclusive": True,
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


async def test_verify_mutation_mode_fails_closed_after_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing and reservation mutation steps remain disabled."""
    calls: list[str] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return a safe object and record request methods."""
        del kwargs
        calls.append(args[1])
        return {
            "id": 10,
            "customFieldValues": [{"customFieldId": 1, "value": "old"}],
        }

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)

    with pytest.raises(RuntimeError, match="mutation steps remain disabled"):
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


def test_complete_diff_accepts_addressed_insertion() -> None:
    """Complete comparison permits only the builder-approved insertion."""
    before = {"customFieldValues": [{"customFieldId": 2, "value": "keep"}]}
    after = {
        "customFieldValues": [
            {"customFieldId": 2, "value": "keep"},
            {"customFieldId": 1, "value": "new"},
        ]
    }

    from scripts.verify_custom_field_writes import (
        _complete_differences_ignoring_addressed_value,
    )

    assert _complete_differences_ignoring_addressed_value(before, after, 1) == []


def test_complete_diff_detects_addressed_metadata_loss() -> None:
    """Complete comparison catches addressed raw-entry metadata loss."""
    before = {
        "customFieldValues": [{"customFieldId": 1, "value": "old", "metadata": "keep"}]
    }
    after = {"customFieldValues": [{"customFieldId": 1, "value": "new"}]}

    from scripts.verify_custom_field_writes import (
        _complete_differences_ignoring_addressed_value,
    )

    assert _complete_differences_ignoring_addressed_value(before, after, 1)


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
    """Snapshot mode stores private snapshots with restricted permissions."""
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
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.snapshot_path",
        lambda _target_type, _target_id: snapshot,
    )
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes.append_live_verification_summary",
        lambda _summary: None,
    )

    try:
        await verify(
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


async def test_request_accepts_delete_array_result() -> None:
    """DELETE cleanup accepts Hostaway's empty-array success result."""
    from scripts.verify_custom_field_writes import _request

    class Client:
        """Minimal async client returning a DELETE response."""

        async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
            """Return a successful empty-array result response."""
            del args, kwargs
            return httpx.Response(
                200,
                json={"status": "success", "result": []},
                request=httpx.Request("DELETE", "https://api.hostaway.com/v1/tasks/42"),
            )

    client: Any = Client()
    assert await _request(client, "DELETE", "/v1/tasks/42", "token") == {}


async def test_task_canary_rejects_noop_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task canary rejects a sentinel equal to the fetched baseline."""
    before = {
        "id": 42,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    reads = [before]
    snapshot = Path(".verify-test-artifacts/task-noop/task-42.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return a created task and unchanged baseline."""
        del kwargs
        method = args[1]
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

    with pytest.raises(RuntimeError, match="sentinel must differ"):
        await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=1,
                value="old",
                original_value="old",
                unrelated_custom_field_id=2,
                listing_map_id=None,
                mutate=False,
                snapshot=False,
                task_canary=True,
            )
        )


async def test_task_canary_rejects_invalid_ids_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task canary validates both custom-field ids before creating a task."""
    calls: list[str] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Fail if any live request is attempted."""
        del kwargs
        calls.append(args[1])
        return {}

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)

    with pytest.raises(ValueError, match="customFieldId"):
        await verify(
            Namespace(
                target_type="listing",
                target_id=10,
                custom_field_id=0,
                value="new",
                original_value="old",
                unrelated_custom_field_id=-1,
                listing_map_id=None,
                mutate=False,
                snapshot=False,
                task_canary=True,
            )
        )

    assert calls == []


async def test_task_canary_does_not_restore_before_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task canary does not send rollback when snapshot storage fails."""
    before = {
        "id": 42,
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    reads = [before]
    calls: list[str] = []

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Record task canary calls and return baseline data."""
        del kwargs
        method = args[1]
        calls.append(method)
        if method == "POST":
            return {"id": 42}
        if method == "GET":
            return reads.pop(0)
        return {}

    def fail_snapshot(*args: Any, **kwargs: Any) -> None:
        """Raise before a rollback snapshot is saved."""
        del args, kwargs
        raise OSError("snapshot failed")

    monkeypatch.setenv("HOSTAWAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr("scripts.verify_custom_field_writes._request", fake_request)
    monkeypatch.setattr(
        "scripts.verify_custom_field_writes._write_private_snapshot",
        fail_snapshot,
    )

    with pytest.raises(OSError, match="snapshot failed"):
        await verify(
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

    assert calls == ["POST", "GET", "DELETE"]


async def test_task_canary_requires_mutation_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task canary fails when Hostaway ignores the partial PUT."""
    before = {
        "id": 42,
        "title": "Hostaway custom-field verification canary",
        "customFieldValues": [
            {"customFieldId": 1, "value": "old"},
            {"customFieldId": 2, "value": "keep"},
        ],
    }
    reads = [before, before]
    calls: list[str] = []
    snapshot = Path(".verify-test-artifacts/task-ignored/task-42.json")

    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return unchanged task reads after the mutation."""
        del kwargs
        method = args[1]
        calls.append(method)
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

    try:
        with pytest.raises(RuntimeError, match="mutation did not persist"):
            await verify(
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

    assert calls == ["POST", "GET", "PUT", "GET", "PUT", "DELETE"]
