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
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from custom_components.hostaway.api.custom_fields import (
    build_custom_field_values_payload,
)

REDACTED = "<redacted>"
BUILT_IN_EXCLUDED_KEYS = frozenset({"customFieldValues"})


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
    if not isinstance(result, dict):
        raise RuntimeError("Hostaway response result is not an object")
    return result


async def verify(args: argparse.Namespace) -> int:
    """Run the guarded live verification."""
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
        snap_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        snap_path.write_text(json.dumps(before, indent=2, sort_keys=True))
        answer = input(f"Type MUTATE {args.target_type} {args.target_id} to continue: ")
        if answer != f"MUTATE {args.target_type} {args.target_id}":
            raise RuntimeError("confirmation did not match; no mutation sent")
        custom_field_restore_payload = {
            "customFieldValues": deepcopy(before["customFieldValues"])
        }
        complete_restore_payload = deepcopy(before)
        verification_error: Exception | None = None
        restore_payload: dict[str, Any] = custom_field_restore_payload
        try:
            await _request(client, "PUT", path, token, json=payload)
            after = await _request(
                client, "GET", path, token, params={"includeResources": 1}
            )
            if custom_field_value(after, args.custom_field_id) != args.value:
                raise RuntimeError("target custom field did not change")
            problems = compare_unrelated(before, after, args.custom_field_id)
            if problems:
                restore_payload = complete_restore_payload
                raise RuntimeError("; ".join(problems))
        except Exception as exc:
            verification_error = exc

        await _request(client, "PUT", path, token, json=restore_payload)
        restored = await _request(
            client, "GET", path, token, params={"includeResources": 1}
        )
        original_target = custom_field_value(before, args.custom_field_id)
        restored_target = custom_field_value(restored, args.custom_field_id)
        restore_problems = compare_unrelated(before, restored, args.custom_field_id)
        if restored_target != original_target or restore_problems:
            details = restore_problems or ["target custom field was not restored"]
            raise RuntimeError("restore verification failed: " + "; ".join(details))
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
    parser.add_argument("custom_field_id", type=int)
    parser.add_argument("value")
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="perform the guarded live mutation; default is dry-run",
    )
    return parser


def main() -> int:
    """Run the script."""
    args = build_parser().parse_args()
    return asyncio.run(verify(args))


if __name__ == "__main__":
    raise SystemExit(main())
