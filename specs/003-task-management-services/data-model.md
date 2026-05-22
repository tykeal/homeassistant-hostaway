# Data Model: Hostaway Task Management Services

## Entities

### Task (raw dictionary — no model class)

Per the spec assumptions, task service inputs use snake_case, but task service
responses preserve the raw Hostaway API task dictionaries in camelCase. No
separate model class is introduced.

#### Fields

<!-- markdownlint-disable MD013 MD060 -->
| Field | Type | Required (Create) | Required (Update) | Description |
|-------|------|:-----------------:|:-----------------:|-------------|
| id | int | — (returned) | — (path param) | Unique task identifier (from API) |
| title | str | ✅ | ❌ | Task title/summary |
| description | str | ❌ | ❌ | Detailed task description |
| listingMapId | int | ❌ | ❌ | Associated listing in returned task data |
| reservationId | int | ❌ | ❌ | Associated reservation |
| status | str | ❌ | ❌ | One of: pending, confirmed, inProgress, completed, cancelled |
| priority | int | ❌ | ❌ | Task priority level |
| assigneeUserId | int | ❌ | ❌ | Assigned user ID |
| categoriesMap | list[int] | ❌ | ❌ | List of category IDs |
| canStartFrom | str | ❌ | ❌ | Earliest start (ISO datetime string) |
| shouldEndBy | str | ❌ | ❌ | Deadline (ISO datetime string) |
| resolutionNote | str | ❌ | ❌ | Note added when resolving/completing task |
<!-- markdownlint-enable MD013 MD060 -->

#### Service Input → Response Field Mapping

<!-- markdownlint-disable MD013 MD060 -->
| Service Input (snake_case) | Task Output / Hostaway API (camelCase) |
|---------------------------|----------------------------------------|
| title | title |
| description | description |
| listing_id / listing_name | listingMapId |
| reservation_id | reservationId |
| status | status |
| priority | priority |
| assignee_user_id | assigneeUserId |
| categories_map | categoriesMap |
| can_start_from | canStartFrom |
| should_end_by | shouldEndBy |
| resolution_note | resolutionNote |
<!-- markdownlint-enable MD013 MD060 -->

#### Status Values (enum)

<!-- markdownlint-disable MD013 MD060 -->
| Value | Description |
|-------|-------------|
| pending | Task created, not yet started |
| confirmed | Task acknowledged/assigned |
| inProgress | Task actively being worked on |
| completed | Task finished successfully |
| cancelled | Task cancelled/abandoned |
<!-- markdownlint-enable MD013 MD060 -->

#### Validation Rules

- `title`: Non-empty string (required for create, optional for update)
- `task_id`: Positive integer (required for update/delete)
- `listing_id` / `listing_name`: Service input resolution path; `listing_id`
  takes precedence and is emitted as `listingMapId` in returned task data
- `status`: Must be one of the 5 defined values
- `priority`: Positive integer
- `assignee_user_id`: Positive integer
- `categories_map`: List of positive integers
- `can_start_from`, `should_end_by`: ISO format strings (validated by API)
- `reservation_id`: Positive integer

### Listing (existing — read-only reference)

Used for `listing_name` → `listing_map_id` resolution.

<!-- markdownlint-disable MD013 MD060 -->
| Field | Type | Source |
|-------|------|--------|
| id | int | `HostawayListing.id` (coordinator cache key) |
| internal_name | str \| None | `HostawayListing.internal_name` |
<!-- markdownlint-enable MD013 MD060 -->

#### Resolution Logic

```text
listings_coordinator.data: dict[int, HostawayListing]

For listing_name resolution:
  1. Iterate all listings in coordinator.data.values()
  2. Find listing where listing.internal_name == listing_name (exact match)
  3. Return listing.id as listingMapId
  4. If not found → ServiceValidationError
  5. If coordinator.data is None → ServiceValidationError
```

### Config Entry (existing — service dispatch)

<!-- markdownlint-disable MD013 MD060 -->
| Field | Type | Description |
|-------|------|-------------|
| config_entry_id | str | Unique HA config entry identifier |
| api_client | HostawayApiClient | Authenticated API client instance |
| listings_coordinator | HostawayListingsCoordinator | Cached listings data |
<!-- markdownlint-enable MD013 MD060 -->

Resolution via existing `_resolve_entry_data()` helper.

## Relationships

```text
Config Entry 1──* Task (via API client)
Listing 1──* Task (via listingMapId)
Reservation 1──* Task (via reservationId)
```

## State Transitions

```text
Task Status Flow:
  pending → confirmed → inProgress → completed
                    └──→ cancelled
  pending → cancelled (direct cancellation)
  Any status can be set to any other (API does not enforce transitions)
```

Note: The Hostaway API does not enforce status transitions. Any status can be
set at any time. The integration passes the requested status through without
additional validation beyond the allowed values list.
