# Contract: hostaway.update_task

## Service Definition

**Domain**: hostaway **Service**: update_task **Response**:
SupportsResponse.ONLY (always returns updated task data)

## Input Schema

<!-- markdownlint-disable MD013 MD060 -->
| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| task_id | positive int | ✅ | Task to update |
| title | string (non-empty) | ❌ | New title |
| description | string | ❌ | New description |
| listing_id | positive int | ❌ | New listing ID |
| listing_name | string | ❌ | New listing (resolved to listingMapId) |
| reservation_id | positive int | ❌ | New reservation association |
| status | string (enum) | ❌ | pending/confirmed/inProgress/completed/cancelled |
| priority | positive int | ❌ | New priority |
| assignee_user_id | positive int | ❌ | New assignee |
| categories_map | list[int] | ❌ | New category IDs |
| can_start_from | string (ISO date) | ❌ | New start date |
| should_end_by | string (ISO date) | ❌ | New deadline |
| resolution_note | string | ❌ | Resolution/completion note |
| config_entry_id | string | ❌ | Required when multiple accounts configured |
<!-- markdownlint-enable MD013 MD060 -->

## Output

Returns the updated task object as returned by the Hostaway API.

```json
{
  "id": 12345,
  "title": "Change batteries",
  "status": "completed",
  "resolutionNote": "All batteries replaced on 2025-07-16",
  ...
}
```

## Error Cases

<!-- markdownlint-disable MD013 MD060 -->
| Condition | Exception | Message |
|-----------|-----------|---------|
| task_id not found (API 404) | ServiceValidationError | "Task {task_id} not found" |
| listing_name not found in cache | ServiceValidationError | "Listing '{name}' not found" |
| listings data not loaded | ServiceValidationError | "Listings data not available for name resolution" |
| Multiple config entries, no config_entry_id | ServiceValidationError | "config_entry_id required when multiple entries exist" |
| API returns other error | HomeAssistantError | "Failed to update task: {detail}" |
<!-- markdownlint-enable MD013 MD060 -->

## API Call

```http
PUT /v1/tasks/{taskId}
Authorization: Bearer {token}
Content-Type: application/json

{
  "status": "completed",
  "resolutionNote": "All batteries replaced"
}
```
