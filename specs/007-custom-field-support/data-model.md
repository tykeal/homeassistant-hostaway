<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# Data Model: Custom Field Support

**Feature**: 007-custom-field-support
**Date**: 2026-09-22

## Entities

### HostawayCustomFieldDefinition

Account-level custom field definition fetched from
`GET /v1/customFields`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `custom_field_id` | `int` | Yes | Hostaway definition `id`. |
| `account_id` | `int \| None` | No | Definition account id. |
| `name` | `str` | Yes | Human-readable display name. |
| `var_name` | `str` | Yes | Machine name used for addressing. |
| `field_type` | `str` | Yes | `text`, `textarea`, `number`, `dropdown`, or future type. |
| `object_type` | `str` | Yes | `listing` or `reservation`; `task` is ignored. |
| `possible_values` | `list[str]` | Yes | Dropdown choices; empty for other types. |
| `is_public` | `bool` | Yes | False for hidden fields. |
| `sort_order` | `int \| None` | No | Hostaway ordering metadata. |

**Factory**:
`from_api_dict(data: dict[str, Any]) -> HostawayCustomFieldDefinition | None`

**Validation**:

- Returns `None` and logs a warning for malformed required fields.
- Rejects booleans as ids even though Python treats bool as int.
- Parses dropdown `possibleValues` from Hostaway's JSON representation.
- Preserves unknown future `type` strings for server-side validation.
- Ignores `objectType: task` before adding definitions to the cache.

### HostawayCustomFieldValue

Presentation-safe custom field value parsed from an object's
`customFieldValues` collection.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `custom_field_id` | `int` | Yes | Numeric field id from `customFieldId`. |
| `value` | `Any` | Yes | JSON value, including explicit `None`. |
| `raw` | `Any` | Yes | Original API entry for merge preservation. |

**Factory**:
`from_api_entry(data: Any) -> HostawayCustomFieldValue | None`

**Validation**:

- Non-mapping entries, missing or non-integer `customFieldId`, boolean
  `customFieldId`, or a missing `value` key return `None` and log a warning
  without logging the raw value.
- An explicitly present `value: null` is valid and distinct from a missing
  `value` key.
- The raw entry is preserved separately even when presentation parsing fails,
  so write merges can resubmit it unchanged.
- Presentation code never raises from one malformed value entry.

### HostawayCustomFieldCollection

Parsed custom field values for one listing or reservation.

| Field | Type | Description |
|-------|------|-------------|
| `state` | `Literal["present", "missing", "null", "invalid"]` | Raw collection presence and validity. |
| `values` | `dict[int, HostawayCustomFieldValue]` | Presentation-safe values keyed by id. |
| `raw_entries` | `list[Any]` | Original API entries in Hostaway order. |
| `malformed_entries` | `list[Any]` | Raw entries skipped from presentation. |
| `invalid_raw` | `Any \| None` | Original invalid collection value when `state == "invalid"`. |

**Invariants**:

- `state == "present"` means Hostaway supplied `customFieldValues` as a list.
  A present empty list is represented as `state == "present"` with
  `raw_entries == []`.
- `state == "missing"` means the object omitted `customFieldValues`.
- `state == "null"` means the object supplied `customFieldValues: null`.
- `state == "invalid"` means the object supplied a non-list collection, with
  the original value retained in `invalid_raw` for diagnostics.
- Only `state == "present"` is valid for write merges. Missing, null, and
  invalid collections are read/presentation states only and must make
  `hostaway.set_custom_field` fail closed before any `PUT`.
- Every raw entry from a present Hostaway list appears in `raw_entries`.
- Malformed entries are excluded from `values` but remain in
  `malformed_entries` and `raw_entries`.
- A write merge must fail closed if a raw malformed entry cannot be carried
  into the outgoing payload unchanged.
- A write merge must fail closed if any malformed entry carries the addressed
  `customFieldId`, even when it is the only raw entry for that id. Replacing it
  could drop raw data, and appending beside it could leave Hostaway to resolve
  an arbitrary duplicate.
- A write merge must fail closed if more than one raw entry carries the
  addressed `customFieldId`, including malformed entries with an id but no
  `value`, because the integration cannot safely choose one duplicate without
  risking a clobber.

### ListingCustomFieldKeyAllocation

Runtime representation of the per-listing entity key namespace.

| Field | Type | Description |
|-------|------|-------------|
| `listing_id` | `int` | Hostaway listing id. |
| `field_to_key` | `dict[int, str]` | Persisted and newly allocated key map. |
| `reserved_keys` | `set[str]` | Shared namespace for the listing. |
| `persisted_keys` | `dict[int, str]` | Keys reconstructed from entity registry. |

**Unique ID format**:

```text
{entry.unique_id}_{listing_id}_custom_field_{customFieldId}_{allocated_key}
```

**Allocation rules**:

1. Reuse `persisted_keys[customFieldId]` when present.
2. Use `custom_<slugified varName>` when a definition is available and that
   slug is unique among listing definitions.
3. Use `custom_<slugified varName>_<customFieldId>` when definitions contain
   duplicate `varName` values or slug collisions.
4. Use `custom_field_<customFieldId>` when the value has no definition.
5. If the candidate collides with a reserved key owned by another field,
   append `_<customFieldId>` to the base candidate.
6. If the appended key is also reserved by another field, append numeric
   disambiguators deterministically:
   `<base>_<customFieldId>_2`, `<base>_<customFieldId>_3`, and so on.
7. Reserve the final key for the listing namespace.

**Restart stability**: The allocator always seeds from the entity registry
before allocating new keys. This preserves existing entity IDs and response
keys after restarts, reloads, user entity renames, and later definition
collisions.

### HostawayListingCustomFieldSensor

Diagnostic sensor for one listing custom field value.

| Attribute | Type | Description |
|-----------|------|-------------|
| `listing_id` | `int` | Hostaway listing id. |
| `custom_field_id` | `int` | Hostaway custom field id. |
| `allocated_key` | `str` | Stable per-listing key from allocator. |
| `definition` | `HostawayCustomFieldDefinition \| None` | Current resolved metadata. |

**State**: Native value from the listing's parsed custom field collection.
Existing sensors remain present with native value `None` after successful
clears.

**Attributes**:

- Resolved: `customFieldId`, `varName`, `name`, `type`, `possibleValues`,
  `value`, `resolved: true`
- Unresolved: `customFieldId`, `value`, `resolved: false`

### HostawayCustomFieldsCoordinator

Per-config-entry definitions coordinator.

| Property | Type | Description |
|----------|------|-------------|
| `data` | `list[HostawayCustomFieldDefinition]` | Cached definitions. |
| `by_id` | `dict[int, HostawayCustomFieldDefinition]` | Lookup cache. |
| `by_object_type` | `dict[str, list[HostawayCustomFieldDefinition]]` | Listing/reservation filters. |
| `last_refresh_succeeded` | `bool` | Whether the most recent definitions refresh succeeded. |
| `last_refresh_error` | `Exception \| None` | Most recent refresh failure for diagnostics. |

**Refresh interval**:
`custom_field_definitions_scan_interval`, default 15 minutes, minimum one
minute.

**Methods**:

- `get_definition(custom_field_id, object_type)`.
- `get_definitions_for_object_type(object_type)`.
- `resolve_var_name(var_name, object_type)`; raises ambiguous or unknown
  errors for write services.

**Write invariant**: Read surfaces may use stale `data` when
`last_refresh_succeeded` is false so entity labels do not churn. Write
services must reject all mutations while this flag is false, even when `data`
is non-empty, because FR-031 requires current definition resolution.

### CustomFieldWriteSafetyGates

Per-config-entry executable gates for live no-clobber verification.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `listing_partial_put_verified` | `bool` | `False` | FR-035 listing partial-PUT verification passed. |
| `reservation_no_clobber_verified` | `bool` | `False` | SC-003 reservation no-clobber verification passed. |

**Storage**: The gate object lives under
`hass.data[DOMAIN][entry.entry_id]["custom_field_write_safety"]` and is seeded
from implementation constants that default to `False` for each target type.

**Enablement rule**: A target type's flag may be changed to `True` only in an
implementation change that records the matching live verification result. Until
then, `hostaway.set_custom_field` rejects that target type before reading,
merging, or sending a mutating request.

### CustomFieldWriteLockRegistry

Per-entry registry of `asyncio.Lock` instances for write serialization.

| Key | Type | Description |
|-----|------|-------------|
| `(target_type, target_id)` | `tuple[str, int]` | One lock per listing or reservation. |

**Invariant**: Two Home Assistant writes to the same object cannot overlap.
Each successful write reads current values after any earlier successful write
has completed.

### CustomFieldWriteGenerationRegistry

Per-entry generation counters that coordinators and write services share.

| Key | Type | Description |
|-----|------|-------------|
| `(target_type, target_id)` | `tuple[str, int]` | Object generation. |

**Invariant**: A coordinator refresh that started before a successful write
cannot publish stale custom-field data over the post-write value. Coordinators
capture generations at refresh start and compare them before publishing. If a
target generation advanced, the coordinator preserves or merges the post-write
custom-field value until a later refresh that started after the write.

## Service Response Shapes

### `hostaway.get_custom_fields`

```json
{
  "custom_fields": [
    {
      "customFieldId": 123,
      "varName": "parking_bay",
      "name": "Parking Bay",
      "type": "text",
      "objectType": "listing",
      "possibleValues": [],
      "isPublic": false,
      "sortOrder": 10
    }
  ]
}
```

The top-level key is always present. Empty cache returns
`{"custom_fields": []}`.

### `hostaway.get_custom_field_values`

```json
{
  "custom_fields": {
    "custom_parking_bay": {
      "customFieldId": 123,
      "varName": "parking_bay",
      "name": "Parking Bay",
      "type": "text",
      "possibleValues": [],
      "value": "A12",
      "resolved": true
    },
    "custom_field_999": {
      "customFieldId": 999,
      "value": "legacy",
      "resolved": false
    }
  }
}
```

For read services, defined fields without object values are included with
`value: null`. Listing response keys use the allocator in non-mutating mode.
Reservation response keys always use `custom_field_<customFieldId>`.

### `hostaway.set_custom_field`

```json
{
  "target_type": "listing",
  "target_id": 67890,
  "customFieldId": 123,
  "varName": "parking_bay",
  "addressed_by": "varName",
  "result": "success"
}
```

The response is returned only when a caller requests service response data.

## State Transitions

### Definition cache

```text
empty/unavailable -> refresh success -> cached definitions
cached definitions -> refresh failure -> stale cache retained by coordinator
cached definitions -> refresh success -> replacement definition list
```

Listing and reservation coordinators do not fail because definitions refresh
fails. Writes fail closed if definitions are unavailable or cannot resolve the
addressed field.

### Listing custom-field sensor

```text
absent value -> value observed -> sensor created
unresolved value -> definition refresh resolves id -> same sensor, new metadata
resolved value -> field cleared by service -> same sensor, native value None
existing key -> later slug collision -> existing key retained
```

### Write path

```text
service call
  -> schema validation
  -> reject boolean integer identifiers
  -> config entry resolution
  -> field definition resolution
  -> local value validation
  -> acquire target lock
  -> increment or mark target write generation
  -> read target by id with includeResources=1
  -> reject malformed raw entries for the addressed customFieldId
  -> reject duplicate raw entries for the addressed customFieldId
  -> merge raw customFieldValues
  -> PUT target
  -> update local coordinator data
  -> advance target generation and post-write override
  -> release lock
  -> optional response
```

Any failure before the `PUT` sends no mutation. Any Hostaway API failure after
the `PUT` raises an actionable Home Assistant error and does not publish local
success state.
