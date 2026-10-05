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

- A Hostaway account for optional manual FR-035 corroboration before listing
  writes are enabled. No sandbox is assumed; the verification ladder starts
  with zero-risk read-only and dry-run steps. Authoritative Hostaway written
  confirmation dated 2026-10-04 now satisfies listing omitted top-level field
  preservation; live listing mutation is optional corroboration for that point.

## Development order

1. Setup and guardrails.
2. API models, parsers, direct reads, and `includeResources=1` listing and
   reservation reads.
3. Definitions coordinator and options flow.
4. FR-035 listing written-confirmation evidence, optional live
   corroboration, reservation owner-accepted evidence, and mandatory read-back
   verification, using the production no-clobber payload builders.
5. Listing custom-field key allocator, listing sensors, and reservation
   `custom_fields` attributes.
6. Read services.
7. Field addressing and value validation.
8. Write service dispatch, local patching, and no-clobber integration.
9. Documentation and UX clarity.
10. Polish and final validation.

The FR-035 evidence recording and reservation read-back mitigation are
intentionally before write support. Listing and reservation writes each have a
default-off executable gate. If evidence and read-back protection have not
passed for a target type, `hostaway.set_custom_field` must fail closed for that
target type while continuing to allow reads.

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

Hostaway Technical Support gave authoritative written confirmation on
2026-10-04 that a listing update body containing only `customFieldValues`
preserves omitted top-level listing fields, including `doorCode`, pricing, and
availability. Record that answer in
`specs/007-custom-field-support/live-verification.md` with its certainty level.
This satisfies the top-level partial-body evidence path. It does not document
`customFieldValues` array merge semantics.

Support also reported observed, non-contractual behaviour that array fields are
replaced wholesale per array, based on the `listingImages` precedent. Continue
to send the complete read-modify-write `customFieldValues` array. Do not send
only the changed entry.

Do not run live, mutating corroboration until CI is green for the
implementation branch. The repository constitution prohibits manual or
exploratory testing before automated CI has passed.

Use a real listing with one populated custom variable. No minimum number of
populated custom variables is required because the no-op empty-diff protocol
uses the whole object as the control group. If a disposable listing is not
available, capture a complete private rollback snapshot before sending any
mutation. Only redacted summaries may be logged or committed.
If the live object has only one populated custom variable, record that live
endpoint preservation of additional populated custom values could not be
observed. Automated tests still cover only the local merge/payload builder.

Optional corroboration steps:

- Step 1: Read the listing with `includeResources=1`, record a complete
  private before snapshot outside git, and verify an allowlisted restore
  payload is reconstructable from that snapshot without deep-copying the GET
  response.

  ```bash
  HOSTAWAY_ACCESS_TOKEN=... scripts/verify_custom_field_writes.py     listing LISTING_ID --snapshot
  ```

- Step 2: Inspect the generated dry-run payload. The verification script must
  default to dry-run, so this step sends no mutation.

  ```bash
  HOSTAWAY_ACCESS_TOKEN=... scripts/verify_custom_field_writes.py     listing LISTING_ID CUSTOM_FIELD_ID SENTINEL_VALUE
  ```

- Step 3: Run a disposable task canary: create a throwaway Hostaway task,
  snapshot it, send partial `PUT /v1/tasks/{id}`, verify unrelated task fields
  survive, apply the allowlisted restore payload built by the same production
  restore-path code that listing Steps 4 and 5 would use, re-read, confirm the
  task matches its pre-mutation snapshot, and delete the task. Treat the
  server-accepted task restore as indicative, not conclusive, for listings.

Requires a disposable listing or the allowlisted restore path from Step 1 plus
the server-accepted restore demonstrated by Step 3, and a separate explicit
owner decision:

- Step 4: Send a listing no-op self-write of the populated custom variable's
  current value through the production payload strategy, re-read the listing
  with `includeResources=1`, and confirm the canonicalized whole-object diff
  is empty.
- Step 5: Send a distinct sentinel value, confirm exactly one field changed,
  and restore the original value only when the recovery path is verified to be
  conflict-safe for the target and can preserve concurrent external edits with
  conditional/version protection. Otherwise, record the failure and raise
  without another mutation.

If optional corroboration fails, the implementation must make
`hostaway.set_custom_field` fail closed for `target_type: listing` with an
actionable message until a safe strategy is selected.

## Reservation no-clobber evidence

Record production top-level merge evidence in
`specs/007-custom-field-support/live-verification.md` before enabling
reservation writes. `hostaway.set_door_code` sends a partial
`PUT /v1/reservations/{id}` containing only `doorCode` plus optional
`doorCodeVendor` and `doorCodeInstruction`, through
`HostawayApiClient.update_reservation`. Owner-provided external account
history MAY be recorded separately as empirical evidence, but this repository
does not substantiate a v0.4.0 production release or no-data-loss history.

Record the residual gap honestly: Hostaway explicitly did not confirm
reservation `customFieldValues` merge-versus-replace behaviour. Support also
reported a known tracked case where the reservation update endpoint returned
success while a custom field value was not updated. Reservation custom-field
writes may be enabled only when mandatory post-write read-back verification is
implemented for all custom-field writes. That read-back must convert silent
non-persistence into an actionable error.

The owner accepts the residual reservation risk for the verified account
because it has zero reservation custom variables today, limiting the
custom-variable clobber surface. If reservation custom variables become
available later, run the same no-op, sentinel, and restore protocol used for
listings as corroborating evidence.

Bind this evidence and owner acceptance to the verified Hostaway
account/config entry. Other accounts remain disabled until their own
account-bound evidence or owner acceptance is recorded with read-back
protection in place.

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
payload builder, with explicit listing strategy selection state. A target type
may use a partial payload only when recorded evidence supports that strategy;
a full-object listing payload must be reconstructable from the pre-write
snapshot before any live mutation uses it. A verified full-object strategy
must not be represented by a flag that claims partial `PUT` was verified.
Hostaway's observed array replacement means the partial custom-field payload
must contain the complete merged `customFieldValues` array, not only the
changed entry. Optional no-op, sentinel, and restore steps must run with the
selected listing payload shape when they are used for corroboration. If a
full-object listing strategy is selected for any reason, repeat those steps
with the reconstructed full-object payload before enabling listing writes.
Full-object payloads require a per-target writable-field allowlist and
normalization rules. Do not deep-copy an `includeResources=1` response into a
`PUT` payload.
For full-object writes, require a Hostaway conditional/version check that
detects concurrent external dashboard edits after the pre-write read; otherwise
keep the full-object strategy disabled.

### Post-write read-back

After every successful-looking custom-field `PUT`, re-read the target with
`includeResources=1`. Compare canonicalized complete snapshots using the same
volatile-field normalization helpers as the verification code. Raise an
actionable error if the addressed value did not persist, and reference the
known Hostaway reservation issue where the endpoint can return success without
updating a custom field. If unrelated custom values or built-in fields changed, raise. Attempt recovery
from the pre-write state only when a verified target-specific recovery path can
preserve concurrent external edits with conditional/version protection. If the
change cannot safely be attributed to this write, raise without another
mutation. Never log-and-ignore read-back failures.

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
