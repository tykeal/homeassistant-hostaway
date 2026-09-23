<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

<!-- markdownlint-disable MD013 MD040 MD060 -->

# API Contract: Hostaway Custom Fields

**Feature**: 007-custom-field-support
**Date**: 2026-09-22
**API Version**: Hostaway API v1

## Hostaway API endpoints

### GET /v1/customFields

Fetch account-level custom field definitions.

**Authentication**: OAuth 2.0 bearer token through the existing Hostaway API
client.

**Request**:

```http
GET /v1/customFields?limit=500&offset=0 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Accept: application/json
```

**Query parameters**:

| Name | Required | Description |
|------|----------|-------------|
| `limit` | No | Default 100, maximum 500. |
| `offset` | No | Offset for pagination. |
| `objectType` | No | `reservation`, `listing`, or `task`; omitted fetches all. |

**Response object**:

```json
{
  "status": "success",
  "result": [
    {
      "id": 123,
      "accountId": 456,
      "name": "Parking Bay",
      "varName": "parking_bay",
      "possibleValues": null,
      "type": "text",
      "objectType": "listing",
      "isPublic": 0,
      "sortOrder": 10
    }
  ],
  "limit": 500,
  "offset": 0,
  "count": 1,
  "page": 1,
  "totalPages": 1
}
```

**Pagination contract**:

- Request pages with `limit=500` and increasing `offset`.
- Continue until Hostaway pagination metadata shows no next page.
- If pagination metadata is absent, stop when a page returns fewer entries
  than the requested limit.
- Definition pagination requests remain subject to the integration rate
  limiter.

**Integration mapping**:

| API field | Integration field |
|-----------|-------------------|
| `id` | `customFieldId` / `custom_field_id` |
| `accountId` | `account_id` |
| `name` | `name` |
| `varName` | `varName` / `var_name` |
| `possibleValues` | `possibleValues` / `possible_values` |
| `type` | `type` / `field_type` |
| `objectType` | `objectType` / `object_type` |
| `isPublic` | `isPublic` / `is_public` |
| `sortOrder` | `sortOrder` / `sort_order` |

**Errors**:

- 401/403: authentication failure.
- 429: rate limit.
- 5xx: Hostaway server error.
- Malformed entries: skipped with a warning; remaining definitions are cached.

### GET /v1/listings with includeResources

Fetch listing pages with populated custom field values.

**Request**:

```http
GET /v1/listings?offset=0&limit=100&includeResources=1 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Accept: application/json
```

**Contract**:

- `includeResources=1` is required or `customFieldValues` is empty.
- Existing listing pagination behavior remains unchanged.
- Custom field values are parsed for entity presentation and preserved in raw
  form for write merges.

**Relevant response fragment**:

```json
{
  "id": 67890,
  "name": "Ocean Suite",
  "customFieldValues": [
    {
      "customFieldId": 123,
      "value": "A12"
    }
  ]
}
```

### GET /v1/listings/{id} with includeResources

Fetch one listing by Hostaway listing id for read services and write merges.

**Request**:

```http
GET /v1/listings/67890?includeResources=1 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Accept: application/json
```

**Contract**:

- Used by `get_custom_field_values` for `target_type: listing`.
- Used by `set_custom_field` immediately before listing write merge.
- Returns a clear not-found or inaccessible error for FR-026/FR-042.
- Must include `includeResources=1` so `customFieldValues` is populated.

### GET /v1/reservations with includeResources

Fetch reservation pages with populated custom field values.

**Request**:

```http
GET /v1/reservations?listingId=67890&limit=100&includeResources=1 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Accept: application/json
```

**Contract**:

- `includeResources=1` is required or `customFieldValues` is empty.
- Existing `afterId` cursor pagination remains based on raw reservation ids.
- Malformed reservation records are skipped with the existing warning pattern.
- Malformed custom field value entries do not skip the whole reservation.

### GET /v1/reservations/{id} with includeResources

Fetch one reservation by Hostaway reservation id for read services and write
merges.

**Request**:

```http
GET /v1/reservations/99001?includeResources=1 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Accept: application/json
```

**Contract**:

- Used by `get_custom_field_values` for `target_type: reservation`.
- Used by `set_custom_field` immediately before reservation write merge.
- Does not require `listing_id`; services accept `target_id` only.
- Returns a clear not-found or inaccessible error for FR-026/FR-042.
- Must include `includeResources=1` so `customFieldValues` is populated.

### PUT /v1/listings/{id}

Update a listing through Hostaway's whole-object listing endpoint.

**Request shape for custom-field writes after FR-035 passes**:

```http
PUT /v1/listings/67890 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Content-Type: application/json

{
  "customFieldValues": [
    {
      "customFieldId": 123,
      "value": "B14"
    },
    {
      "customFieldId": 124,
      "value": "Pet friendly"
    }
  ]
}
```

**Safety contract**:

- Listing writes are disabled until FR-035 verifies this partial payload does
  not clear omitted built-in listing fields.
- The outgoing `customFieldValues` array is based on the current raw listing
  values read immediately before the write.
- Unaddressed values are preserved.
- Raw malformed entries are included unchanged.
- If a raw malformed entry carries the addressed `customFieldId`, the write
  fails before `PUT`; it is not replaced, dropped, or duplicated.
- Duplicate raw entries for the addressed `customFieldId` fail before `PUT`.
- If partial PUT verification fails, this endpoint cannot be used for listing
  writes until a safe full-payload strategy is proven.
- The executable gate is
  `custom_field_write_safety.listing_partial_put_verified`, stored per config
  entry under `hass.data[DOMAIN][entry.entry_id]` and defaulting to false.
  While false, `hostaway.set_custom_field` rejects listing writes with
  `listing custom-field writes are disabled until FR-035 partial-PUT
  verification passes` before reading the target or sending any mutation.

### PUT /v1/reservations/{id}

Update a reservation through Hostaway's whole-object reservation endpoint.

**Request shape**:

```http
PUT /v1/reservations/99001 HTTP/1.1
Host: api.hostaway.com
Authorization: Bearer <redacted>
Content-Type: application/json

{
  "customFieldValues": [
    {
      "customFieldId": 223,
      "value": "Late arrival"
    }
  ]
}
```

**Safety contract**:

- Reservation custom-field writes are disabled until SC-003 verifies a live
  merged reservation write leaves every unrelated custom field and visible
  built-in field unchanged.
- Custom-field writes still read current reservation values first and submit a
  merged `customFieldValues` collection.
- Unaddressed values and unaddressed raw malformed entries are preserved.
- If a raw malformed entry carries the addressed `customFieldId`, the write
  fails before `PUT`; it is not replaced, dropped, or duplicated.
- Duplicate raw entries for the addressed `customFieldId` fail before `PUT`.
- Built-in fields visible before the write, including `doorCode`, must remain
  unchanged.
- The executable gate is
  `custom_field_write_safety.reservation_no_clobber_verified`, stored per
  config entry under `hass.data[DOMAIN][entry.entry_id]` and defaulting to
  false. While false, `hostaway.set_custom_field` rejects reservation writes
  with `reservation custom-field writes are disabled until SC-003 no-clobber
  verification passes` before reading the target or sending any mutation.

## Home Assistant services

### hostaway.get_custom_fields

**Registration**: `supports_response=SupportsResponse.ONLY`

**Input schema**:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `config_entry_id` | No | string | Required when multiple accounts are loaded. |

**Success response**:

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

**Empty response**:

```json
{
  "custom_fields": []
}
```

### hostaway.get_custom_field_values

**Registration**: `supports_response=SupportsResponse.ONLY`

**Input schema**:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `target_type` | Yes | string | `listing` or `reservation`. |
| `target_id` | Yes | integer | Hostaway listing or reservation id; booleans are invalid. |
| `config_entry_id` | No | string | Required when multiple accounts are loaded. |

**Success response**:

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
    }
  }
}
```

**Unresolved value entry**:

```json
{
  "customFieldId": 999,
  "value": "legacy",
  "resolved": false
}
```

**Contract**:

- Listing keys use the listing key allocator and persisted sensor keys.
- Reservation keys use `custom_field_<customFieldId>`.
- Defined fields without current values are included with `value: null`.
- Empty results return `{"custom_fields": {}}`.

### hostaway.set_custom_field

**Registration**: `supports_response=SupportsResponse.OPTIONAL`

**Input schema**:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `target_type` | Yes | string | `listing` or `reservation`. |
| `target_id` | Yes | integer | Hostaway listing or reservation id; booleans are invalid. |
| `customFieldId` | Conditionally | integer | Required when `varName` is omitted; booleans are invalid. |
| `varName` | Conditionally | string | Required when `customFieldId` is omitted. |
| `value` | Yes | any | New value; explicit `null` clears the field. |
| `config_entry_id` | No | string | Required when multiple accounts are loaded. |

Exactly one of `customFieldId` or `varName` is required.

**Success response**:

```json
{
  "target_type": "reservation",
  "target_id": 99001,
  "customFieldId": 223,
  "varName": "cleaner_note",
  "addressed_by": "varName",
  "result": "success"
}
```

**Validation errors**:

- Missing or multiple field identifiers.
- Boolean supplied for integer identifiers such as `target_id` or
  `customFieldId`; bool is not accepted even though Python treats it as int.
- Unknown `customFieldId` for target object type.
- Unknown or ambiguous same-object-type `varName`.
- Dropdown value not in `possibleValues`.
- Boolean supplied for `number`.
- Non-string supplied for `text` or `textarea`.
- Missing `config_entry_id` when multiple entries are loaded.
- Latest definitions refresh failed; stale definitions are read-only until the
  next successful refresh.

**Write errors**:

- Target listing or reservation not found or inaccessible.
- Definitions unavailable for write resolution.
- Current object cannot be read for merge.
- Current object's `customFieldValues` collection is missing, null, or a
  non-list value. A present empty list is valid; any other non-list or absent
  shape is rejected so writes cannot clear unknown existing values.
- Malformed raw entries cannot be preserved.
- A malformed raw entry carries the addressed `customFieldId`; replacing it
  could drop raw data, and appending beside it could create an arbitrary
  duplicate.
- Duplicate raw entries carry the addressed `customFieldId`.
- Hostaway rejects the update.
- Listing write attempted before FR-035 verification passes.
- Reservation write attempted before SC-003 verification passes.

## Rate limits

Hostaway limit: 200 requests / 10 seconds, per account and per IP.

The implementation contract adds no normal listing/reservation poll requests
beyond existing page reads. It adds one definitions coordinator cycle per
configured interval, defaulting to 15 minutes, using `limit=500`.
