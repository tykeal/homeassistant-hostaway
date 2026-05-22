# Contract: hostaway.delete_task

## Service Definition

**Domain**: hostaway **Service**: delete_task **Response**:
SupportsResponse.NONE (no return data)

## Input Schema

<!-- markdownlint-disable MD013 MD060 -->
| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| task_id | positive int | ✅ | Task to delete |
| config_entry_id | string | ❌ | Required when multiple accounts configured |
<!-- markdownlint-enable MD013 MD060 -->

## Output

None. Service returns no data (FR-006).

## Error Cases

<!-- markdownlint-disable MD013 MD060 -->
| Condition | Exception | Message |
|-----------|-----------|---------|
| task_id not found (API 404) | ServiceValidationError | "Task {task_id} not found" |
| Multiple config entries, no config_entry_id | ServiceValidationError | "config_entry_id required when multiple entries exist" |
| API returns other error | HomeAssistantError | "Failed to delete task: {detail}" |
<!-- markdownlint-enable MD013 MD060 -->

## API Call

```http
DELETE /v1/tasks/{taskId}
Authorization: Bearer {token}
```text

Expected response:

```json
{
  "status": "success",
  "result": []
}
```text
