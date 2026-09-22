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

- A Hostaway test account for manual verification of FR-035 before listing
  writes are enabled.

## Development order

1. API models and parsers.
2. `includeResources=1` listing and reservation reads.
3. Definitions coordinator and options flow.
4. Listing custom-field key allocator and sensors.
5. Reservation `custom_fields` attributes.
6. Read services.
7. FR-035 listing partial-PUT verification.
8. Write service and no-clobber merge.
9. Documentation and final validation.

The FR-035 verification step is intentionally before listing write support.
If listing partial PUT is destructive, keep listing writes disabled and fail
closed while continuing with reads and reservation writes.

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

Check line budgets before final review:

```bash
wc -l \
  custom_components/hostaway/api/*.py \
  custom_components/hostaway/coordinator.py \
  custom_components/hostaway/services/*.py \
  custom_components/hostaway/sensor/*.py \
  custom_components/hostaway/config_flow.py
```

## Manual FR-035 verification

Do not run live, mutating verification until CI is green for the
implementation branch. The repository constitution prohibits manual or
exploratory testing before automated CI has passed.

Use a disposable real listing with at least three custom fields populated.
If a disposable listing is not available, capture a complete private rollback
snapshot before sending any mutation. Only redacted summaries may be logged or
committed.

1. Read the listing with `includeResources=1`.
2. Record a complete private before snapshot of built-in fields and all
   `customFieldValues`, plus a redacted summary for review notes.
3. Send a partial `PUT /v1/listings/{id}` payload that changes one harmless
   custom field through the planned merged `customFieldValues` shape.
4. Re-read the listing with `includeResources=1`.
5. Confirm:
   - the target custom field changed;
   - every other custom field is unchanged;
   - built-in fields such as name, pricing, occupancy, and door-code-related
     fields are unchanged;
   - hidden custom fields remain present.
6. Roll back from the complete private snapshot if any unexpected mutation is
   detected; otherwise restore the original target value if needed.

Do not enable listing writes unless this verification passes. If it fails,
the implementation must make `hostaway.set_custom_field` fail closed for
`target_type: listing` with an actionable message.

## Manual reservation no-clobber verification

Use a disposable real reservation with at least three populated reservation
custom fields and at least one built-in reservation field such as `doorCode`.
If a disposable reservation is not available, capture a complete private
rollback snapshot before sending any mutation. Only redacted summaries may be
logged or committed.

1. Read the reservation with `includeResources=1`.
2. Record a complete private before snapshot of built-in fields and all
   `customFieldValues`, plus a redacted summary for review notes.
3. Send a merged `PUT /v1/reservations/{id}` payload that changes one harmless
   custom field.
4. Re-read the reservation with `includeResources=1`.
5. Confirm:
   - the target custom field changed;
   - every other reservation custom field is unchanged;
   - built-in fields such as `doorCode`, `doorCodeVendor`, and
     `doorCodeInstruction` are unchanged;
   - hidden custom fields remain present.
6. Roll back from the complete private snapshot if any unexpected mutation is
   detected; otherwise restore the original target value if needed.

Do not enable reservation writes unless this verification passes. If it fails,
the implementation must make `hostaway.set_custom_field` fail closed for
`target_type: reservation` with an actionable message.

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
