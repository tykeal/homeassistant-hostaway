#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify Hostaway custom-field write no-clobber behavior.

Examples:
  verify_custom_field_writes.py listing LISTING_ID --snapshot
  verify_custom_field_writes.py listing LISTING_ID CUSTOM_FIELD_ID SENTINEL
  verify_custom_field_writes.py task-canary --custom-field-id FIELD_ID
    --unrelated-custom-field-id OTHER_FIELD_ID --value SENTINEL

The task canary never accepts a listing or reservation target. It creates,
mutates, restores, verifies, and deletes only its own disposable Hostaway task.
"""

# aislop-ignore-file complexity/file-too-large -- cohesive live verification helper

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar, overload

import httpx

from custom_components.hostaway.api.custom_fields import (
    build_allowlisted_restore_payload,
    build_custom_field_values_payload,
    canonical_snapshot_differences,
    canonicalize_complete_snapshot,
    validate_identifier,
)

REDACTED = "<redacted>"
BUILT_IN_EXCLUDED_KEYS = frozenset({"customFieldValues"})
LIVE_VERIFICATION_LOG = Path("specs/007-custom-field-support/live-verification.md")
SUMMARY_SAFE_SCALAR_KEYS = frozenset({"mode", "indicative_not_conclusive"})
SUMMARY_SAFE_LIST_KEYS = frozenset({"restore_payload_keys"})
SUMMARY_REDACT_WHOLE_KEYS = frozenset({"canonical_summary"})
PAYLOAD_LOG_KEYS = frozenset({"customFieldValues", "customFieldId", "value"})

_N = TypeVar("_N")


class VerificationArgumentParser(argparse.ArgumentParser):
    """Argument parser with cross-mode safety validation."""

    @overload
    def parse_args(
        self,
        args: Iterable[str] | None = None,
        namespace: None = None,
    ) -> argparse.Namespace:
        """Parse arguments into a new namespace."""
        ...

    @overload
    def parse_args(self, args: Iterable[str] | None, namespace: _N) -> _N:
        """Parse arguments into an existing namespace."""
        ...

    @overload
    def parse_args(self, *, namespace: _N) -> _N:
        """Parse runtime arguments into an existing namespace."""
        ...

    def parse_args(
        self,
        args: Iterable[str] | None = None,
        namespace: Any = None,
    ) -> Any:
        """Parse arguments and reject unsafe mixed-mode invocations."""
        raw_args = sys.argv[1:] if args is None else list(args)
        if (
            len(raw_args) > 1
            and raw_args[0] == "task-canary"
            and not raw_args[1].startswith("-")
        ):
            self.error(
                "task canary only operates on a disposable task it creates "
                "itself; do not pass listing or reservation target arguments"
            )
        if raw_args and raw_args[0] == "task-canary" and "--listing-map-id" in raw_args:
            self.error(
                "task canary only operates on a disposable task it creates "
                "itself; do not pass listing or reservation target arguments"
            )
        parsed = super().parse_args(raw_args if args is not None else None, namespace)
        if getattr(parsed, "legacy_task_canary", False):
            self.error(
                "task canary only operates on a disposable task it creates "
                "itself; do not pass listing or reservation target arguments "
                "with task-canary mode"
            )
        if getattr(parsed, "task_canary", False) and getattr(
            parsed, "target_arguments", []
        ):
            self.error(
                "task canary only operates on a disposable task it creates "
                "itself; do not pass listing or reservation target arguments"
            )
        return parsed


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
        handle.write(json.dumps(sanitize_summary(summary), indent=2, sort_keys=True))
        handle.write("\n```\n")


def sanitize_summary(value: Any, key: str | None = None) -> Any:
    """Redact evidence values while preserving protocol metadata."""
    if key in SUMMARY_REDACT_WHOLE_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            item_key: sanitize_summary(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        if key in SUMMARY_SAFE_LIST_KEYS:
            return [item for item in value if isinstance(item, str)]
        return [sanitize_summary(item, key) for item in value]
    if key in SUMMARY_SAFE_SCALAR_KEYS:
        return value
    if value is not None:
        return REDACTED
    return value


def redact_payload_for_log(value: Any) -> Any:
    """Redact payload values and omit arbitrary metadata keys for logs."""
    if isinstance(value, Mapping):
        return {
            key: redact_payload_for_log(item)
            for key, item in value.items()
            if key in PAYLOAD_LOG_KEYS
        }
    if isinstance(value, list):
        return [redact_payload_for_log(item) for item in value]
    if value is not None:
        return REDACTED
    return value


def custom_field_value(data: Mapping[str, Any], field_id: int) -> Any:
    """Return a custom field value from a Hostaway object."""
    for item in data.get("customFieldValues", []):
        if isinstance(item, Mapping) and item.get("customFieldId") == field_id:
            return item.get("value")
    return None


def has_populated_custom_field(data: Mapping[str, Any], field_id: int) -> bool:
    """Return whether a custom field entry has a non-null value."""
    for item in data.get("customFieldValues", []):
        if (
            isinstance(item, Mapping)
            and item.get("customFieldId") == field_id
            and item.get("value") is not None
        ):
            return True
    return False


def normalize_task_snapshot(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize documented task custom-field response aliases."""
    normalized = dict(data)
    if "customFieldValues" not in normalized and "customFieldValue" in normalized:
        normalized["customFieldValues"] = normalized.pop("customFieldValue")
    return normalized


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
    if method == "DELETE":
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
        before = normalize_task_snapshot(
            await _request(client, "GET", path, token, params={"includeResources": 1})
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
    print(json.dumps(sanitize_summary(summary), sort_keys=True))
    return 0


def _task_canary_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Return a disposable task payload for the canary."""
    validate_identifier(args.custom_field_id, "customFieldId")
    validate_identifier(args.unrelated_custom_field_id, "unrelated_custom_field_id")
    custom_values = build_custom_field_values_payload(
        {
            "customFieldValues": [
                {
                    "customFieldId": args.unrelated_custom_field_id,
                    "value": "preserve",
                }
            ]
        },
        args.custom_field_id,
        args.original_value,
    )["customFieldValues"]
    payload: dict[str, Any] = {
        "title": "Hostaway custom-field verification canary",
        "description": "Disposable task created by verification tooling.",
        "status": "pending",
        "customFieldValues": custom_values,
    }
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
    expected = json.loads(json.dumps(before))
    expected["customFieldValues"] = build_custom_field_values_payload(
        before, field_id, custom_field_value(after, field_id)
    )["customFieldValues"]
    return compare_complete_snapshots(expected, after)


async def run_task_canary(args: argparse.Namespace) -> int:
    """Run the disposable task canary restore protocol."""
    token = os.environ.get("HOSTAWAY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("HOSTAWAY_ACCESS_TOKEN is required")
    if args.unrelated_custom_field_id == args.custom_field_id:
        raise RuntimeError(
            "task canary requires two distinct objectType: task custom field "
            "definitions"
        )
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
            before = normalize_task_snapshot(
                await _request(
                    client, "GET", path, token, params={"includeResources": 1}
                )
            )
            original_value = custom_field_value(before, args.custom_field_id)
            if original_value == args.value:
                raise RuntimeError("task canary sentinel must differ from baseline")
            if not has_populated_custom_field(before, args.unrelated_custom_field_id):
                raise RuntimeError(
                    "task canary requires a populated unrelated task custom "
                    "field; create two objectType: task definitions and leave "
                    "the unrelated field populated for preservation evidence"
                )
            validated_restore_payload = validate_restore_payload(before, "task")
            snap_path = snapshot_path("task", task_id)
            _write_private_snapshot(snap_path, before)
            mutation = build_custom_field_values_payload(
                before, args.custom_field_id, args.value
            )
            restore_payload = validated_restore_payload
            await _request(client, "PUT", path, token, json=mutation)
            after = normalize_task_snapshot(
                await _request(
                    client, "GET", path, token, params={"includeResources": 1}
                )
            )
            if custom_field_value(after, args.custom_field_id) != args.value:
                raise RuntimeError("task canary mutation did not persist")
            if not _only_addressed_custom_field_changed(
                before, after, args.custom_field_id
            ):
                raise RuntimeError("task canary partial PUT changed unexpected data")
            await _request(client, "PUT", path, token, json=restore_payload)
            restored = normalize_task_snapshot(
                await _request(
                    client, "GET", path, token, params={"includeResources": 1}
                )
            )
            differences = compare_complete_snapshots(before, restored)
            if differences:
                raise RuntimeError(
                    "task canary restore changed complete snapshot: "
                    f"{len(differences)} difference(s)"
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
    print(json.dumps(sanitize_summary(summary), sort_keys=True))
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
        if not args.mutate:
            print(
                json.dumps(
                    {"dry_run": True, "payload": redact_payload_for_log(payload)}
                )
            )
            return 0
        raise RuntimeError(
            "live listing and reservation mutation steps remain disabled; "
            "use --snapshot or the task-canary subcommand for authorized "
            "ladder steps"
        )


def positive_int(value: str) -> int:
    """Parse a positive integer argument."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def non_empty_string(value: str) -> str:
    """Parse a non-empty string argument."""
    if value == "":
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _add_target_parser(
    subparsers: Any,
    target_type: str,
) -> None:
    """Add a listing or reservation verification subcommand."""
    target = subparsers.add_parser(
        target_type,
        help=f"verify {target_type} snapshots and dry-run payloads",
    )
    target.set_defaults(
        target_type=target_type,
        task_canary=False,
        legacy_task_canary=False,
    )
    target.add_argument("target_id", type=positive_int)
    target.add_argument("custom_field_id", type=positive_int, nargs="?")
    target.add_argument("value", nargs="?")
    target.add_argument(
        "--mutate",
        action="store_true",
        help="perform the guarded live mutation; default is dry-run",
    )
    target.add_argument(
        "--snapshot",
        action="store_true",
        help="capture and validate a read-only restore snapshot",
    )
    target.add_argument(
        "--task-canary",
        action="store_true",
        dest="legacy_task_canary",
        help=argparse.SUPPRESS,
    )
    target.add_argument("--unrelated-custom-field-id", help=argparse.SUPPRESS)
    target.add_argument("--custom-field-id", help=argparse.SUPPRESS)
    target.add_argument("--original-value", help=argparse.SUPPRESS)
    target.add_argument("--value", dest="canary_value", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = VerificationArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    _add_target_parser(subparsers, "listing")
    _add_target_parser(subparsers, "reservation")

    canary = subparsers.add_parser(
        "task-canary",
        help="create, mutate, restore, verify, and delete a disposable task",
        description=(
            "Run a disposable Hostaway task canary. This mode never accepts "
            "a listing or reservation id and can never target a pre-existing "
            "task."
        ),
    )
    canary.set_defaults(
        target_type="task",
        target_id=None,
        mutate=False,
        snapshot=False,
        task_canary=True,
        legacy_task_canary=False,
    )
    canary.add_argument(
        "--custom-field-id",
        type=positive_int,
        required=True,
        help="task custom field id to mutate with the sentinel value",
    )
    canary.add_argument(
        "--value",
        type=non_empty_string,
        required=True,
        help="sentinel value for the addressed task custom field",
    )
    canary.add_argument(
        "--unrelated-custom-field-id",
        type=positive_int,
        required=True,
        help=(
            "second populated objectType: task custom field id used to prove "
            "unrelated values survive"
        ),
    )
    canary.add_argument(
        "--original-value",
        default="original",
        help="initial addressed custom-field value for task canary",
    )
    canary.add_argument(
        "target_arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS
    )
    return parser


def main() -> int:
    """Run the script."""
    args = build_parser().parse_args()
    return asyncio.run(verify(args))


if __name__ == "__main__":
    raise SystemExit(main())
