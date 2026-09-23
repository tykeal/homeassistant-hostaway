<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Quickstart: Custom Field Support

**Feature**: 007-custom-field-support
**Date**: 2026-09-22

## Prerequisites

- Python 3.14.2+.
- `uv` installed.
- Repository dependencies installed from the worktree root:

  ```bash
  uv sync --all-extras --group dev
  ```

- A Hostaway account for manual FR-035 verification before listing writes are
  enabled. No sandbox is assumed; the verification ladder starts with
  zero-risk read-only and dry-run steps.

## Development order

1. Setup and guardrails.
2. API models, parsers, direct reads, and `includeResources=1` listing and
   reservation reads.
3. Definitions coordinator and options flow.
4. FR-035 listing verification ladder and reservation production-evidence
   recording, using the production no-clobber payload builders.
5. Listing custom-field key allocator, listing sensors, and reservation
   `custom_fields` attributes.
6. Read services.
7. Field addressing and value validation.
8. Write service dispatch, local patching, and no-clobber integration.
9. Documentation and UX clarity.
10. Polish and final validation.

The FR-035 verification ladder and reservation evidence recording are
intentionally before write support. Listing and reservation writes each have a
default-off executable gate. If evidence has not passed for a target type,
`hostaway.set_custom_field` must fail closed for that target type while
continuing to allow reads.

## Targeted test commands

Run focused tests while implementing each slice:

```bash
uv run pytest tests/api/test_custom_fields.py -x -q
uv run pytest tests/sensor/test_custom_fields.py -x -q
uv run pytest tests/services/test_custom_fields.py -x -q
uv run pytest tests/test_config_flow.py -x -q -k custom_field
```

Run the required validation before committing:

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
```

Run the configured quality gate before final review:

```bash
uvx --from aislop==0.12.0 aislop ci
```

## Manual FR-035 verification

Do not run live, mutating verification until CI is green for the
implementation branch. The repository constitution prohibits manual or
exploratory testing before automated CI has passed.

Use a real listing with one populated custom variable. No minimum number of
populated custom variables is required because the no-op empty-diff protocol
uses the whole object as the control group. If a disposable listing is not
available, capture a complete private rollback snapshot before sending any
mutation. Only redacted summaries may be logged or committed.

Authorized now:

- Step 0: Ask Hostaway support for authoritative
  `PUT /v1/listings/{id}` semantics.
- Step 1: Read the listing with `includeResources=1`, record a complete
  private before snapshot outside git, and verify the restore payload is
  reconstructable from that snapshot.
- Step 2: Inspect the generated dry-run payload. The verification script must
  default to dry-run, so this step sends no mutation.
- Step 3: Run a disposable task canary: create a throwaway Hostaway task, send
  partial `PUT /v1/tasks/{id}`, verify unrelated task fields survive, and
  delete the task. Treat the result as indicative, not conclusive, for
  listings.

Requires a separate explicit owner decision:

- Step 4: Send a listing no-op self-write of the populated custom variable's
  current value through the production payload strategy, re-read the listing
  with `includeResources=1`, and confirm the whole-object diff is empty.
- Step 5: Send a distinct sentinel value, confirm exactly one field changed,
  restore the original value, and confirm the final object matches the
  pre-write snapshot exactly. Attempt automatic restore from the complete
  private snapshot if any unexpected mutation is detected, and document that
  restore depends on any cleared built-in fields being writable.

Do not enable listing writes unless this verification passes. If it fails,
the implementation must make `hostaway.set_custom_field` fail closed for
`target_type: listing` with an actionable message.

## Reservation no-clobber evidence

Record production evidence in
`specs/007-custom-field-support/live-verification.md` before enabling
reservation writes. `hostaway.set_door_code` sends a partial
`PUT /v1/reservations/{id}` containing only `doorCode` plus optional
`doorCodeVendor` and `doorCodeInstruction`, through
`HostawayApiClient.update_reservation`. That behavior has shipped since
v0.4.0 with no reported reservation data loss, which supports top-level merge
semantics for the reservation endpoint.

Record the residual gap honestly: this evidence does not prove reservation
`customFieldValues` specifically round-trips. With zero reservation custom
variables in the owner's account today, the current clobber surface is limited
to built-in fields, which the door-code evidence covers. If reservation custom
variables become available later, run the same no-op, sentinel, and restore
protocol used for listings.

## Key implementation patterns

### API module injection

Keep custom-field API logic free of Home Assistant imports:

```python
class HostawayCustomFieldsClient:
    def __init__(self, api_client: HostawayApiClient) -> None:
        self._api_client = api_client
```

The module may define protocols for request helpers to keep tests small.

### Direct target reads

Read and write services accept only `target_type` and `target_id`, so the API
layer must use direct object reads:

```text
GET /v1/listings/{id}?includeResources=1
GET /v1/reservations/{id}?includeResources=1
```

Do not require a reservation `listing_id` and do not scan all selected
listings to find a reservation by id.

### Entry resolution

All three services must call the existing fail-closed helper:

```python
entry_data = _resolve_entry_data(hass, dict(call.data))
```

This preserves the required multi-account behavior:
`config_entry_id required when multiple entries exist`.

### Key allocation

Seed from the entity registry before allocating:

```text
entry.unique_id + listing_id + customFieldId -> persisted allocated key
```

Then allocate only missing keys using:

```text
custom_<slugified varName>
custom_<slugified varName>_<customFieldId>
custom_field_<customFieldId>
```

Existing keys always win, including fallback keys that later gain resolved
definition metadata.

### Write merge

Never build a write payload from only parsed presentation values. Use the raw
`customFieldValues` entries read immediately before the write, replace or add
only the addressed valid entry, and pass through malformed entries unchanged.
A present empty `customFieldValues: []` list is genuinely empty, but missing,
`null`, or non-list `customFieldValues` must fail closed before any `PUT`.
Before replacing or adding the addressed entry, scan raw entries with a
bool-safe `customFieldId` check. If a malformed entry carries the addressed id,
or if more than one entry carries that id, fail closed before any `PUT` so the
write cannot drop raw data or create an ambiguous duplicate.

The API layer must expose both a partial payload builder and a full-object
payload builder, with strategy selection per target type. A target type may
use a partial payload only when recorded evidence supports that strategy; a
full-object payload must be reconstructable from the pre-write snapshot before
any live mutation uses it.

## User-facing behavior to verify

- Listing sensors appear dynamically for present custom values.
- Existing listing diagnostic sensors remain unchanged.
- Reservation attributes add `custom_fields` and keep all existing keys.
- Hidden fields are visible.
- Task custom fields are ignored.
- Built-in `doorCode` is documented separately from custom variables.
- Unknown value ids surface with `resolved: false`.
- Defined but unset fields appear in the value read service with
  `value: null`.
- Clearing a value with explicit `null` leaves an existing listing sensor
  present with native value `None`.

## Final validation

Before opening the implementation PR for this feature stage, run:

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
```

For the plan stage, the same commands should remain green because the changes
are documentation-only.
