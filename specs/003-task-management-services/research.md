# Research: Hostaway Task Management Services

## R1: Hostaway Tasks API Endpoints

**Decision**: Use the following Hostaway Tasks API endpoints (added 2023-08-17
per changelog):

- `POST /v1/tasks` — Create a task
- `GET /v1/tasks` — List tasks (with query parameter filters)
- `PUT /v1/tasks/{taskId}` — Update a task
- `DELETE /v1/tasks/{taskId}` — Delete a task

**Rationale**: These are the standard RESTful endpoints documented in the
Hostaway API. The response format follows the standard `{"status": "success",
"result": ...}` envelope used by all other Hostaway endpoints (confirmed by
existing `update_reservation()` parsing logic).

**Alternatives considered**:

- Using webhooks for task state: Rejected — spec explicitly requires
  services-only behavior (FR-017), with no polling/sensors.

## R2: Task Object Field Mapping (snake_case → camelCase)

**Decision**: Map service call parameters to Hostaway API camelCase fields:

<!-- markdownlint-disable MD013 MD060 -->
| Service Parameter (snake_case) | API Field (camelCase) | Type | Notes |
|-------------------------------|----------------------|------|-------|
| title | title | string | Required for create |
| description | description | string | Optional |
| listing_id | listingMapId | int | Direct numeric ID |
| listing_name | → resolved to listingMapId | string | Resolved via coordinator |
| reservation_id | reservationId | int | Optional |
| status | status | string | pending/confirmed/inProgress/completed/cancelled |
| priority | priority | int | Numeric priority level |
| assignee_user_id | assigneeUserId | int | Optional |
| categories_map | categoriesMap | list[int] | List of category IDs |
| can_start_from | canStartFrom | string | ISO datetime |
| should_end_by | shouldEndBy | string | ISO datetime |
| resolution_note | resolutionNote | string | Optional (update only) |
<!-- markdownlint-enable MD013 MD060 -->

**Rationale**: The `listingMapId` field name (not `listingId`) is confirmed by
Hostaway API conventions — Hostaway uses "listing map" IDs internally. The spec
requires snake_case→camelCase conversion (FR-010). Existing code in
`async_handle_set_door_code` demonstrates this pattern manually (e.g.,
`doorCode`, `doorCodeVendor`).

**Alternatives considered**:

- Automatic camelCase conversion utility: Rejected — only ~10 fields, manual
  mapping is clearer and avoids edge cases.
- Using a dataclass for Task: Rejected — spec states "Task data is represented
  as raw dictionaries" (Assumptions section).

## R3: Listing Name Resolution Pattern

**Decision**: Implement a `_resolve_listing_id()` helper in `services.py` that:

1. If `listing_id` is provided, use it directly
2. If `listing_name` is provided, look up in `listings_coordinator.data` (a
   `dict[int, HostawayListing]`)
3. Match against `internal_name` field (exact match, case-sensitive)
4. If no match found, raise `ServiceValidationError`
5. If `listings_coordinator.data` is None/empty, raise `ServiceValidationError`
   (FR-008)

**Rationale**: The spec states "exact string matching against the internal
listing name field" (Assumptions). The coordinator holds `HostawayListing`
objects with `internal_name` attribute. The `get_reservations` service already
demonstrates accessing `listings_coordinator.data.get(listing_id)`.

**Alternatives considered**:

- Case-insensitive matching: Rejected — spec says exact match.
- Matching against `name` (public name): Rejected — spec says "internal listing
  name".
- Raising error when both `listing_id` and `listing_name` provided: Considered
  but spec says "use listing_id directly and ignore listing_name" (Edge Cases).

## R4: Get Tasks Filtering

**Decision**: Pass filter parameters as query parameters to `GET /v1/tasks`:

- `listingMapId` — filter by listing
- `reservationId` — filter by reservation
- `status` — filter by status
- `canStartFromStart` / `canStartFromEnd` — date range for canStartFrom field

**Rationale**: Hostaway API uses query parameters for filtering on list
endpoints (established by `get_reservations_page` in client.py which passes
params dict). The spec mentions `can_start_from_start` and `can_start_from_end`
as date range filters (Acceptance Scenario 3.4).

**Alternatives considered**:

- Client-side filtering: Rejected — API supports server-side filtering, more
  efficient.

## R5: Error Handling Pattern

**Decision**: Follow the exact pattern from `async_handle_set_door_code`:

1. Catch `HostawayResponseError` — check for "not found" → raise
   `ServiceValidationError`
2. Catch `HostawayApiError` — raise `HomeAssistantError` with descriptive
   message
3. For delete: no return value (FR-006)
4. For create/update/get: return task data dict (FR-005)

**Rationale**: Matches FR-011 ("not found" → validation error) and FR-012 (other
errors → general error). Consistent with existing codebase patterns.

**Alternatives considered**:

- Custom `HostawayNotFoundError` exception: Not needed — the string check
  pattern is already established and works well.

## R6: Service Response Pattern

**Decision**:

- `create_task`: `supports_response=SupportsResponse.ONLY` — always returns
  created task data
- `update_task`: `supports_response=SupportsResponse.ONLY` — always returns
  updated task data
- `get_tasks`: `supports_response=SupportsResponse.ONLY` — always returns tasks
  list
- `delete_task`: `supports_response=SupportsResponse.NONE` — no return data

**Rationale**: FR-005 requires create/update/get to return data. FR-006 requires
delete to return None. Using `ONLY` for data-returning services ensures callers
always get structured data. The existing `find_reservation` uses `ONLY` as
precedent.

**Alternatives considered**:

- Using `OPTIONAL` for all: Rejected — the spec mandates data return for
  non-delete services.

## R7: API Client Method Design

**Decision**: Add these methods to `HostawayApiClient`:

- `async def create_task(self, data: dict[str, Any]) -> dict[str, Any]`
- `async def update_task(self, task_id: int, data: dict[str, Any]) -> dict[str,
  Any]`
- `async def delete_task(self, task_id: int) -> None`
- `async def get_tasks_page(self, params: dict[str, Any] | None = None, offset:
  int = 0, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]`
- `async def get_tasks(self, params: dict[str, Any] | None = None) ->
  list[dict[str, Any]]`

**Rationale**: Mirrors the existing page-plus-aggregate patterns already used
for listings and reservations. Delete returns None (just validates success).
`get_tasks_page()` handles the raw `limit` / `offset` API call, while
`get_tasks()` iterates through all pages and returns a fully aggregated task
list to satisfy the constitution's pagination requirement for list endpoints.

**Alternatives considered**:

- Single-page `get_tasks`: Rejected — the Hostaway task endpoint returns
  pagination metadata (`limit`, `offset`, `count`), and Constitution Principle
  II requires pagination support for list endpoints.
- Returning raw response: Rejected — must validate response format per
  constitution.
