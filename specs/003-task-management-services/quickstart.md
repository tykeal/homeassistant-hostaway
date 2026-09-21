# Quickstart: Hostaway Task Management Services

## Prerequisites

- Python 3.14.2+
- `uv` package manager installed
- Repository cloned and dependencies installed:

  ```bash
  uv sync

  ```

## Development Setup

```bash
# Install pre-commit hooks
uv run pre-commit install

# Run tests
uv run pytest tests/ -v

# Run linting
uv run ruff check .
uv run ruff format --check .

# Run type checking
uv run mypy custom_components/hostaway/
```

## Implementation Order (TDD)

### Phase 1: API Client Methods

1. **Test**: Write failing tests in `tests/api/test_client.py` for
   `create_task()`
2. **Implement**: Add `create_task()` to
   `custom_components/hostaway/api/client.py`
3. **Refactor**: Ensure method follows `update_reservation()` pattern
4. Repeat for `update_task()`, `delete_task()`, `get_tasks()`

### Phase 2: Service Layer

1. **Test**: Write failing tests in `tests/test_services.py` for
   `async_handle_create_task()`
2. **Implement**: Add handler + schema to
   `custom_components/hostaway/services.py`
3. **Refactor**: Extract shared helpers (e.g., `_resolve_listing_id()`)
4. Repeat for update, delete, get handlers

### Phase 3: Service Registration

1. Add service definitions to `custom_components/hostaway/services.yaml`
2. Register new services in `async_setup_services()`
3. Run full test suite to verify integration

## Key Patterns to Follow

### API Client Method (reference: `update_reservation`)

```python
async def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
    """Create a task via POST."""
    response = await self._request("POST", "/v1/tasks", json=data)
    parsed = self._parse_response(response)
    status = parsed.get("status")
    if status is not None and status != "success":
        raise HostawayResponseError(f"Create failed: {parsed.get('result', status)}")
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise HostawayResponseError("Create response missing 'result' object")
    return result
```

### Service Handler (reference: `async_handle_set_door_code`)

```python
async def async_handle_create_task(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    """Handle hostaway.create_task service call."""
    payload: dict[str, Any] = {"title": call.data["title"]}
    # ... build camelCase payload from call.data ...

    # Resolve listing if listing_name provided
    listing_id = _resolve_listing_id(hass, call.data)
    if listing_id is not None:
        payload["listingMapId"] = listing_id

    entry_data = _resolve_entry_data(hass, call.data)
    api_client: HostawayApiClient = entry_data["api_client"]

    try:
        return await api_client.create_task(payload)
    except HostawayResponseError as exc:
        if "not found" in str(exc).lower():
            raise ServiceValidationError("...") from exc
        raise HomeAssistantError(f"Failed to create task: {exc}") from exc
    except HostawayApiError as exc:
        raise HomeAssistantError(f"Failed to create task: {exc}") from exc
```

### Listing Name Resolution

```python
def _resolve_listing_id(
    hass: HomeAssistant, call_data: dict[str, Any]
) -> int | None:
    """Resolve listing_id from call data (listing_id or listing_name)."""
    if "listing_id" in call_data:
        return call_data["listing_id"]
    if "listing_name" not in call_data:
        return None
    # Look up in coordinator cache...
```

## Testing Patterns

### API Client Tests (uses respx)

```python
async def test_create_task_success(api_client, respx_mock):
    respx_mock.post("/v1/tasks").respond(
        json={"status": "success", "result": {"id": 1, "title": "Test"}}
    )
    result = await api_client.create_task({"title": "Test"})
    assert result == {"id": 1, "title": "Test"}
```

### Service Tests (mocks API client)

```python
async def test_create_task_service(hass, mock_api_client):
    mock_api_client.create_task.return_value = {"id": 1, "title": "Test"}
    result = await async_handle_create_task(hass, mock_call)
    assert result["id"] == 1
```

## Commit Convention

```text
Feat: Add create_task API client method

Implement POST /v1/tasks endpoint call with response validation.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Signed-off-by: Andrew Grimberg <tykeal@bardicgrove.org>
```
