#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify Hostaway custom-field write no-clobber behavior."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from custom_components.hostaway.api.custom_fields import (
    build_allowlisted_restore_payload,
    build_custom_field_values_payload,
    canonical_snapshot_differences,
    canonicalize_complete_snapshot,
)

REDACTED = "<redacted>"
BUILT_IN_EXCLUDED_KEYS = frozenset({"customFieldValues"})
LIVE_VERIFICATION_LOG = Path("specs/007-custom-field-support/live-verification.md")


def redact(value: Any) -> Any:
    """Return a recursively redacted value for logs."""
    if isinstance(value, Mapping):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if value is not None:
        return REDACTED
    return value


def snapshot_dir() -> Path:
    """Return an outside-git snapshot directory."""
    return Path.home() / ".hostaway" / "custom-field-write-snapshots"


def snapshot_path(target_type: str, target_id: int) -> Path:
    """Return the private snapshot path for a target."""
    return snapshot_dir() / f"{target_type}-{target_id}.json"


def _write_private_snapshot(path: Path, data: Mapping[str, Any]) -> None:
    """Write a snapshot with private permissions outside the repository."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    path.chmod(0o600)


def append_live_verification_summary(summary: Mapping[str, Any]) -> None:
    """Append a redacted verification summary to the feature evidence log."""
    LIVE_VERIFICATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LIVE_VERIFICATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write("\n## Verification ladder tooling evidence\n\n")
        handle.write("```json\n")
        handle.write(json.dumps(redact(summary), indent=2, sort_keys=True))
        handle.write("\n```\n")


def custom_field_value(data: Mapping[str, Any], field_id: int) -> Any:
    """Return a custom field value from a Hostaway object."""
    for item in data.get("customFieldValues", []):
        if isinstance(item, Mapping) and item.get("customFieldId") == field_id:
            return item.get("value")
    return None


def compare_unrelated(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    field_id: int,
) -> list[str]:
    """Compare unrelated custom values and visible built-in fields."""
    problems: list[str] = []
    before_values = [
        item
        for item in before.get("customFieldValues", [])
        if not isinstance(item, Mapping) or item.get("customFieldId") != field_id
    ]
    after_values = [
        item
        for item in after.get("customFieldValues", [])
        if not isinstance(item, Mapping) or item.get("customFieldId") != field_id
    ]
    if before_values != after_values:
        problems.append("unrelated customFieldValues changed")
    before_built_ins = {
        key: value for key, value in before.items() if key not in BUILT_IN_EXCLUDED_KEYS
    }
    after_built_ins = {key: after.get(key) for key in before_built_ins}
    if before_built_ins != after_built_ins:
        problems.append("built-in fields changed")
    return problems


def compare_complete_snapshots(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[str]:
    """Compare canonicalized complete snapshots."""
    return canonical_snapshot_differences(before, after)


def validate_restore_payload(
    snapshot: Mapping[str, Any],
    target_type: str,
) -> dict[str, Any]:
    """Build and validate the allowlisted production restore payload."""
    return build_allowlisted_restore_payload(snapshot, target_type)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send an authenticated Hostaway request and return its object result."""
    headers = {"Accept": "application/json"}
    headers["Authorization"] = "Bearer " + token
    response = await client.request(
        method,
        f"https://api.hostaway.com{path}",
        headers=headers,
        **kwargs,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") not in (None, "success"):
        raise RuntimeError("Hostaway returned a non-success status")
    result = data.get("result")
    if method == "DELETE" and result is None:
        return {}
    if not isinstance(result, dict):
        raise RuntimeError("Hostaway response result is not an object")
    return result


async def capture_snapshot(args: argparse.Namespace) -> int:
    """Capture a read-only snapshot and validate restore reconstruction."""
    token = os.environ.get("HOSTAWAY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("HOSTAWAY_ACCESS_TOKEN is required")
    endpoint = "listings" if args.target_type == "listing" else "reservations"
    path = f"/v1/{endpoint}/{args.target_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        before = await _request(
            client, "GET", path, token, params={"includeResources": 1}
        )
    restore_payload = validate_restore_payload(before, args.target_type)
    snap_path = snapshot_path(args.target_type, args.target_id)
    _write_private_snapshot(snap_path, before)
    summary = {
        "mode": "snapshot",
        "target_type": args.target_type,
        "target_id": args.target_id,
        "snapshot": str(snap_path),
        "restore_payload_keys": sorted(restore_payload),
        "canonical_summary": canonicalize_complete_snapshot(redact(before)),
    }
    append_live_verification_summary(summary)
    print(json.dumps(redact(summary), sort_keys=True))
    return 0


def _task_canary_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Return a disposable task payload for the canary."""
    payload: dict[str, Any] = {
        "title": "Hostaway custom-field verification canary",
        "description": "Disposable task created by verification tooling.",
        "status": "pending",
        "customFieldValues": [
            {"customFieldId": args.custom_field_id, "value": args.original_value},
            {"customFieldId": args.unrelated_custom_field_id, "value": "preserve"},
        ],
    }
    listing_map_id = getattr(args, "listing_map_id", None)
    if listing_map_id is not None:
        payload["listingMapId"] = listing_map_id
    return payload


def _only_addressed_custom_field_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    field_id: int,
) -> bool:
    """Return whether only the addressed custom value differs."""
    return not _complete_differences_ignoring_addressed_value(before, after, field_id)


def _complete_differences_ignoring_addressed_value(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    field_id: int,
) -> list[str]:
    """Return complete-snapshot diffs while allowing one addressed value edit."""
    normalized_after = json.loads(json.dumps(after))
    for item in normalized_after.get("customFieldValues", []):
        if isinstance(item, dict) and item.get("customFieldId") == field_id:
            item["value"] = custom_field_value(before, field_id)
    return compare_complete_snapshots(before, normalized_after)


async def run_task_canary(args: argparse.Namespace) -> int:
    """Run the disposable task canary restore protocol."""
    token = os.environ.get("HOSTAWAY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("HOSTAWAY_ACCESS_TOKEN is required")
    async with httpx.AsyncClient(timeout=30) as client:
        created = await _request(
            client,
            "POST",
            "/v1/tasks",
            token,
            json=_task_canary_create_payload(args),
        )
        task_id = created.get("id")
        if not isinstance(task_id, int):
            raise RuntimeError("created task response did not include an integer id")
        path = f"/v1/tasks/{task_id}"
        restore_payload: dict[str, Any] | None = None
        try:
            before = await _request(
                client, "GET", path, token, params={"includeResources": 1}
            )
            restore_payload = validate_restore_payload(before, "task")
            snap_path = snapshot_path("task", task_id)
            _write_private_snapshot(snap_path, before)
            mutation = build_custom_field_values_payload(
                before, args.custom_field_id, args.value
            )
            await _request(client, "PUT", path, token, json=mutation)
            after = await _request(
                client, "GET", path, token, params={"includeResources": 1}
            )
            if not _only_addressed_custom_field_changed(
                before, after, args.custom_field_id
            ):
                raise RuntimeError("task canary partial PUT changed unexpected data")
            await _request(client, "PUT", path, token, json=restore_payload)
            restored = await _request(
                client, "GET", path, token, params={"includeResources": 1}
            )
            differences = compare_complete_snapshots(before, restored)
            if differences:
                raise RuntimeError(
                    "task canary restore changed complete snapshot: "
                    + ", ".join(differences)
                )
        except BaseException:
            if restore_payload is not None:
                await _request(client, "PUT", path, token, json=restore_payload)
            raise
        finally:
            await _request(client, "DELETE", path, token)
    summary = {
        "mode": "task-canary",
        "task_id": task_id,
        "indicative_not_conclusive": True,
    }
    append_live_verification_summary(summary)
    print(json.dumps(redact(summary), sort_keys=True))
    return 0


async def verify(args: argparse.Namespace) -> int:
    """Run the guarded live verification."""
    if getattr(args, "snapshot", False):
        return await capture_snapshot(args)
    if getattr(args, "task_canary", False):
        if args.custom_field_id is None or args.value is None:
            raise RuntimeError("task canary requires custom_field_id and value")
        return await run_task_canary(args)
    if args.custom_field_id is None or args.value is None:
        raise RuntimeError("custom_field_id and value are required")
    token = os.environ.get("HOSTAWAY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("HOSTAWAY_ACCESS_TOKEN is required")
    endpoint = "listings" if args.target_type == "listing" else "reservations"
    path = f"/v1/{endpoint}/{args.target_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        before = await _request(
            client, "GET", path, token, params={"includeResources": 1}
        )
        payload = build_custom_field_values_payload(
            before, args.custom_field_id, args.value
        )
        if set(payload) != {"customFieldValues"}:
            raise RuntimeError("payload contains unsafe top-level keys")
        snap_path = snapshot_path(args.target_type, args.target_id)
        if not args.mutate:
            print(json.dumps({"dry_run": True, "payload": redact(payload)}))
            return 0
        _write_private_snapshot(snap_path, before)
        answer = input(f"Type MUTATE {args.target_type} {args.target_id} to continue: ")
        if answer != f"MUTATE {args.target_type} {args.target_id}":
            raise RuntimeError("confirmation did not match; no mutation sent")
        custom_field_restore_payload = validate_restore_payload(
            before, args.target_type
        )
        verification_error: BaseException | None = None
        restore_payload: dict[str, Any] = custom_field_restore_payload
        mutation_sent = False
        try:
            mutation_sent = True
            await _request(client, "PUT", path, token, json=payload)
            after = await _request(
                client, "GET", path, token, params={"includeResources": 1}
            )
            if custom_field_value(after, args.custom_field_id) != args.value:
                raise RuntimeError("target custom field did not change")
            problems = _complete_differences_ignoring_addressed_value(
                before, after, args.custom_field_id
            )
            if problems:
                raise RuntimeError("complete snapshot changed: " + "; ".join(problems))
        except BaseException as exc:
            verification_error = exc
        finally:
            if mutation_sent:
                await _request(client, "PUT", path, token, json=restore_payload)
                restored = await _request(
                    client, "GET", path, token, params={"includeResources": 1}
                )
                original_target = custom_field_value(before, args.custom_field_id)
                restored_target = custom_field_value(restored, args.custom_field_id)
                restore_problems = compare_complete_snapshots(before, restored)
                if restored_target != original_target or restore_problems:
                    details = restore_problems or [
                        "target custom field was not restored"
                    ]
                    raise RuntimeError(
                        "restore verification failed: " + "; ".join(details)
                    )
        if verification_error is not None:
            raise verification_error
        print(
            json.dumps({"verified": True, "restored": True, "snapshot": str(snap_path)})
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_type", choices=("listing", "reservation"))
    parser.add_argument("target_id", type=int)
    parser.add_argument("custom_field_id", type=int, nargs="?")
    parser.add_argument("value", nargs="?")
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="perform the guarded live mutation; default is dry-run",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="capture and validate a read-only restore snapshot",
    )
    parser.add_argument(
        "--task-canary",
        action="store_true",
        help="create, mutate, restore, verify, and delete a disposable task",
    )
    parser.add_argument(
        "--original-value",
        default="original",
        help="initial addressed custom-field value for task canary",
    )
    parser.add_argument(
        "--unrelated-custom-field-id",
        type=int,
        default=999999,
        help="second custom-field id used only by task-canary tests",
    )
    parser.add_argument(
        "--listing-map-id",
        type=int,
        help="optional listingMapId for the disposable task canary",
    )
    return parser


def main() -> int:
    """Run the script."""
    args = build_parser().parse_args()
    return asyncio.run(verify(args))


if __name__ == "__main__":
    raise SystemExit(main())
