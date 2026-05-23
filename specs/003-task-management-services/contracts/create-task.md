# Contract: hostaway.create_task

## Service Definition

**Domain**: hostaway **Service**: create_task **Response**:
SupportsResponse.ONLY (always returns task data)

## Input Schema

<!-- markdownlint-disable MD013 MD060 -->
| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| title | string (non-empty) | ✅ | Task title |
| description | string | ❌ | Task description |
| listing_id | positive int | ❌ | Listing ID (takes precedence over listing_name) |
| listing_name | string | ❌ | Listing internal name (resolved to listingMapId) |
| reservation_id | positive int | ❌ | Associated reservation |
| status | string (enum) | ❌ | pending/confirmed/inProgress/completed/cancelled |
| priority | positive int | ❌ | Task priority |
| assignee_user_id | positive int | ❌ | Assigned user |
| categories_map | list[int] | ❌ | Category IDs |
| can_start_from | string (ISO date) | ❌ | Earliest start date |
| should_end_by | string (ISO date) | ❌ | Deadline |
| config_entry_id | string | ❌ | Required when multiple accounts configured |
<!-- markdownlint-enable MD013 MD060 -->

## Output

Returns the created task object in the raw Hostaway API response shape.
Service inputs use snake_case, but returned task fields remain camelCase
(e.g., `listingMapId`) and are passed through as-is.

```json
{
  "id": 12345,
  "title": "Change batteries",
  "listingMapId": 67890,
  "status": "pending",
  ...
}
```

## Error Cases

<!-- markdownlint-disable MD013 MD060 -->
| Condition | Exception | Message |
|-----------|-----------|---------|
| listing_name not found in cache | ServiceValidationError | "Listing '{name}' not found" |
| listings data not loaded | ServiceValidationError | "Listings data not available for name resolution" |
| Multiple config entries, no config_entry_id | ServiceValidationError | "config_entry_id required when multiple entries exist" |
| API returns error | HomeAssistantError | "Failed to create task: {detail}" |
<!-- markdownlint-enable MD013 MD060 -->

## API Call

```http
POST /v1/tasks
Authorization: Bearer {token}
Content-Type: application/json

{
  "title": "Change batteries",
  "listingMapId": 67890,
  "description": "Replace all AA batteries in smoke detectors",
  "canStartFrom": "2025-07-15",
  "shouldEndBy": "2025-07-20"
}
```
