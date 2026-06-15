<!-- markdownlint-disable MD013 MD040 MD060 -->

# Data Model: Services Package Refactor

**Feature**: 005-services-package-refactor
**Date**: 2026-06-15

## Entities

### ServiceDefinition (NamedTuple)

A structured record used by the table-driven registration loop. Each entry
fully describes one HA service.

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `name` | `str` | Service name (e.g., `"set_door_code"`) | Non-empty, unique in table |
| `handler` | `Callable[[HomeAssistant, ServiceCall], Awaitable[Any]]` | Async handler function | Must be awaitable |
| `schema` | `vol.Schema` | Voluptuous validation schema | Must validate service call data |
| `supports_response` | `SupportsResponse \| None` | Response support flag | `None` = no response, `OPTIONAL` or `ONLY` |

**Location**: `services/__init__.py`

**Usage**:

```python
SERVICE_DEFINITIONS: list[ServiceDefinition] = [
    ServiceDefinition(
        name="set_door_code",
        handler=async_handle_set_door_code,
        schema=SERVICE_SET_DOOR_CODE_SCHEMA,
        supports_response=None,
    ),
    # ... 8 more entries
]
```

---

### Service Registration Table (SERVICE_DEFINITIONS)

The complete service table containing all 9 services:

| Service Name | Handler Module | Response Support |
|--------------|---------------|-----------------|
| `set_door_code` | `reservation_handlers` | None |
| `get_reservations` | `reservation_handlers` | `SupportsResponse.OPTIONAL` |
| `find_reservation` | `reservation_handlers` | `SupportsResponse.ONLY` |
| `create_task` | `task_handlers` | `SupportsResponse.ONLY` |
| `update_task` | `task_handlers` | `SupportsResponse.ONLY` |
| `delete_task` | `task_handlers` | None |
| `get_tasks` | `task_handlers` | `SupportsResponse.ONLY` |
| `get_users` | `lookup_handlers` | `SupportsResponse.ONLY` |
| `get_groups` | `lookup_handlers` | `SupportsResponse.ONLY` |

---

### Module State (helpers.py)

Module-level state for the locked-reservation rate-limiting:

| Variable | Type | Description |
|----------|------|-------------|
| `_LOCKED_LOG_COOLDOWN_SECONDS` | `int` | Cooldown between repeated warnings (3600s) |
| `_LOCKED_RESERVATION_LOG_STATE` | `dict[int, float]` | Maps reservation_id → last monotonic timestamp |

**Invariant**: State dict is pruned of entries older than 2× cooldown on each new WARNING emission.

---

### Validation Functions (schemas.py)

| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `_positive_int` | `Any` | `int` | Coerces to positive int, rejects bool/fractional |
| `_non_empty_string` | `Any` | `str` | Strips whitespace, rejects empty |
| `_strict_string` | `Any` | `str` | Validates string type without coercion |
| `_positive_int_list` | `Any` | `list[int]` | Validates list of positive integers |
| `_is_user_correctable_task_error` | `HostawayResponseError` | `bool` | Classifies error as user-correctable |

---

### Helper Functions (helpers.py)

| Function | Signature | Description |
|----------|-----------|-------------|
| `_resolve_entry_data` | `(hass, call_data) → dict` | Resolves correct config entry runtime data |
| `_get_listing_name_index` | `(listings_coordinator) → dict[str, int]` | Returns cached name→ID mapping |
| `_resolve_listing_id` | `(call_data, entry_data) → int \| None` | Resolves listing by ID or name |
| `_prune_locked_state` | `(now: float) → None` | Removes stale entries from log state |
| `_log_locked_reservation` | `(reservation_id, exc) → None` | Rate-limited locked-reservation logging |

---

## Relationships

```
services/__init__.py
  │
  ├── SERVICE_DEFINITIONS references handlers from:
  │     ├── reservation_handlers.py (3 handlers)
  │     ├── task_handlers.py (4 handlers)
  │     └── lookup_handlers.py (2 handlers)
  │
  └── SERVICE_DEFINITIONS references schemas from:
        └── schemas.py (8 schemas)

All handler modules depend on:
  └── helpers.py (_resolve_entry_data, _resolve_listing_id)

reservation_handlers.py additionally depends on:
  └── helpers.py (_log_locked_reservation)
```

---

## State Transitions

No new state machines. The existing locked-reservation log state behavior is preserved identically — it simply moves from `services.py` module scope to `helpers.py` module scope.
