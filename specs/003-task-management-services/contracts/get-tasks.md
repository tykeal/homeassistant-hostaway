# Contract: hostaway.get_tasks

## Service Definition

**Domain**: hostaway **Service**: get_tasks **Response**: SupportsResponse.ONLY
(always returns task list)

## Input Schema

<!-- markdownlint-disable MD013 MD060 -->
| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| listing_id | positive int | ❌ | Filter by listing ID |
| listing_name | string | ❌ | Filter by listing (resolved to listingMapId) |
| reservation_id | positive int | ❌ | Filter by reservation |
| status | string (enum) | ❌ | Filter: pending/confirmed/inProgress/completed/cancelled |
| can_start_from_start | string (ISO date) | ❌ | Date range start for canStartFrom |
| can_start_from_end | string (ISO date) | ❌ | Date range end for canStartFrom |
| config_entry_id | string | ❌ | Required when multiple accounts configured |
<!-- markdownlint-enable MD013 MD060 -->

## Output

Returns a dict containing the aggregated tasks list. The service paginates
through all matching Hostaway task-list responses before returning data, and
each task item preserves the raw Hostaway API camelCase field names:

```json
{
  "tasks": [
    {
      "id": 12345,
      "title": "Change batteries",
      "listingMapId": 67890,
      "status": "pending",
      "canStartFrom": "2025-07-15",
      "shouldEndBy": "2025-07-20",
      ...
    },
    ...
  ]
}
```

## Error Cases

<!-- markdownlint-disable MD013 MD060 -->
| Condition | Exception | Message |
|-----------|-----------|---------|
| listing_name not found in cache | ServiceValidationError | "Listing '{name}' not found" |
| listings data not loaded | ServiceValidationError | "Listings data not available for name resolution" |
| Multiple config entries, no config_entry_id | ServiceValidationError | "config_entry_id required when multiple entries exist" |
| API returns error | HomeAssistantError | "Failed to retrieve tasks: {detail}" |
<!-- markdownlint-enable MD013 MD060 -->

## API Call

```http
GET /v1/tasks?listingMapId=67890&status=pending&canStartFromStart=2025-07-01&canStartFromEnd=2025-07-31
Authorization: Bearer {token}
```

Expected response:

```json
{
  "status": "success",
  "result": [ ... task objects ... ],
  "limit": 100,
  "offset": 0,
  "count": 5
}
```

## Notes

- All filter parameters are optional; calling with no filters returns all
  matching tasks across every Hostaway response page.
- Date range filters use `canStartFromStart` and `canStartFromEnd` as query
  parameter names (matching Hostaway API naming convention for range filters).
- The service handles `limit` / `offset` pagination internally; callers do not
  pass paging parameters.
- Results are returned in a wrapper dict (`{"tasks": [...]}`) for consistency
  with other service responses.
