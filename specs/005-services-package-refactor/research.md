<!-- markdownlint-disable MD013 MD040 MD060 -->

# Research: Services Package Refactor

**Feature**: 005-services-package-refactor
**Date**: 2026-06-15
**Status**: Complete

## Research Questions

### RQ-1: Table-Driven Service Registration in Home Assistant

**Decision**: Use a list of `ServiceDefinition` named tuples iterated in a single loop within `async_setup_services()`.

**Rationale**: Home Assistant's `hass.services.async_register()` accepts a handler callable, a schema, and an optional `supports_response` parameter. A table-driven approach replaces 8 individual closure definitions + 8 registration blocks (~136 lines) with a single loop over a list of definitions (~20 lines). The handler injection pattern uses `functools.partial` to bind `hass` to each handler.

**Pattern**:

```python
from collections.abc import Callable
from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse


class ServiceDefinition(NamedTuple):
    """Definition for a single service registration."""

    name: str
    handler: Callable[[HomeAssistant, ServiceCall], Any]
    schema: vol.Schema
    supports_response: SupportsResponse | None


SERVICE_DEFINITIONS: list[ServiceDefinition] = [
    ServiceDefinition("set_door_code", async_handle_set_door_code, SERVICE_SET_DOOR_CODE_SCHEMA, None),
    ServiceDefinition("get_reservations", async_handle_get_reservations, SERVICE_GET_RESERVATIONS_SCHEMA, SupportsResponse.OPTIONAL),
    # ... etc
]


def async_setup_services(hass: HomeAssistant) -> None:
    for svc in SERVICE_DEFINITIONS:
        if hass.services.has_service(DOMAIN, svc.name):
            continue
        kwargs: dict[str, Any] = {"schema": svc.schema}
        if svc.supports_response is not None:
            kwargs["supports_response"] = svc.supports_response
        hass.services.async_register(
            DOMAIN, svc.name, partial(svc.handler, hass), **kwargs
        )
```

**Alternatives considered**:

- **Decorators on handlers**: Rejected — requires runtime module introspection and non-obvious registration ordering.
- **Class-based service registry**: Rejected — over-engineered for 8 services; NamedTuple is simpler.
- **Dict-based definitions**: Rejected — NamedTuple provides type safety and named field access.

---

### RQ-2: Test Patch Path Impact

**Decision**: Test patch paths MUST be updated to reference the sub-module where `HostawayApiClient` is imported.

**Rationale**: The existing tests patch at paths like `custom_components.hostaway.services.HostawayApiClient.update_reservation`. Python's `unittest.mock.patch` works by replacing the name *where it is looked up*. After the refactor, `HostawayApiClient` will be imported in `reservation_handlers.py`, `task_handlers.py`, and `lookup_handlers.py` — not in `services/__init__.py`.

**Impact Analysis**:

- `custom_components.hostaway.services.HostawayApiClient.*` → `custom_components.hostaway.services.reservation_handlers.HostawayApiClient.*` (for reservation methods)
- `custom_components.hostaway.services.HostawayApiClient.*` → `custom_components.hostaway.services.task_handlers.HostawayApiClient.*` (for task methods)
- `custom_components.hostaway.services.HostawayApiClient.*` → `custom_components.hostaway.services.lookup_handlers.HostawayApiClient.*` (for user/group methods)
- `custom_components.hostaway.services._resolve_entry_data` → `custom_components.hostaway.services.helpers._resolve_entry_data`
- Logger name `custom_components.hostaway.services` → each sub-module gets its own logger (e.g., `custom_components.hostaway.services.reservation_handlers`)

**Spec Compliance**: The spec acknowledges this in Assumptions: "Test files may require import path updates [...] but test logic and assertions remain unchanged."

**Alternatives considered**:

- **Re-export all symbols from `__init__.py`**: Rejected — defeats the purpose of the split and creates a maintenance burden. Tests should reference the actual module where code lives.
- **Import `HostawayApiClient` in `__init__.py` and have handlers use it from there**: Rejected — creates circular import risk and non-standard Python patterns.

---

### RQ-3: Module-to-Package Refactoring (Import Compatibility)

**Decision**: Replace `services.py` with `services/__init__.py`. The import statement `from custom_components.hostaway.services import async_setup_services` works identically for both.

**Rationale**: Python treats `package/__init__.py` as the module-level interface. When importing `from package import X`, Python looks for `X` in `package/__init__.py`. The integration's `__init__.py` uses exactly this pattern:

```python
from custom_components.hostaway.services import async_setup_services
```

This requires zero changes when `services.py` becomes `services/__init__.py`.

**Key Constraints**:

- `services.py` and `services/` cannot coexist — Python would be ambiguous about which to use.
- The old `services.py` MUST be deleted before (or in the same commit as) creating `services/__init__.py`.
- Git tracks this as a delete + create (not a rename), since the paths differ structurally.

**Alternatives considered**:

- **Keep `services.py` as a facade that imports from `services/` subpackage (e.g., `_services/`)**: Rejected — unnecessarily complex; Python's package mechanism handles this natively.

---

### RQ-4: `functools.partial` vs. Closures for Handler Injection

**Decision**: Use `functools.partial(handler, hass)` to create the registered callback from the table-defined handler.

**Rationale**: Each handler function takes `(hass: HomeAssistant, call: ServiceCall)` but HA's service registration expects a callback with signature `(call: ServiceCall) -> ...`. The current code uses inline closure definitions to capture `hass`:

```python
async def _handle_set_door_code(call: ServiceCall) -> None:
    await async_handle_set_door_code(hass, call)
```

This is exactly what `partial(async_handle_set_door_code, hass)` does, without the 3-line closure boilerplate per service.

**Alternatives considered**:

- **Lambda**: Rejected — mypy complains about async lambdas and they're less readable.
- **Closure factory function**: Rejected — adds unnecessary indirection.

---

### RQ-5: `async_unregister_services` Function

**Decision**: Add an `async_unregister_services(hass)` function in `services/__init__.py` that uses the same `SERVICE_DEFINITIONS` table to remove all services.

**Rationale**: Currently, service removal is inlined in `__init__.py` with 9 hardcoded `hass.services.async_remove()` calls. Moving this to a table-driven function in the services package:

1. Keeps service names defined in one place (DRY).
2. Allows the integration `__init__.py` to import and call it symmetrically with `async_setup_services`.
3. Automatically stays in sync when services are added/removed.

The integration `__init__.py` will change from:

```python
hass.services.async_remove(DOMAIN, "set_door_code")
hass.services.async_remove(DOMAIN, "get_reservations")
# ... 7 more lines
```

To:

```python
from custom_components.hostaway.services import async_unregister_services
async_unregister_services(hass)
```

**Alternatives considered**:

- **Leave inline removal in `__init__.py`**: Rejected — violates DRY and the spec (FR-002 requires `async_unregister_services` exposed from the package).

---

### RQ-6: File Size Feasibility Analysis

**Decision**: All target file sizes are achievable based on line-count analysis of the current monolithic file.

**Analysis**:

| Target File | Content | Est. Lines | Limit |
|-------------|---------|-----------|-------|
| `__init__.py` | Imports, `ServiceDefinition`, `SERVICE_DEFINITIONS` list, `async_setup_services`, `async_unregister_services` | ~65 | < 80 |
| `schemas.py` | 8 schema definitions + 5 validator functions (`_positive_int`, `_non_empty_string`, `_strict_string`, `_positive_int_list`, `_is_user_correctable_task_error`) + constants (`_TASK_STATUS_VALUES`) | ~175 | < 200 |
| `helpers.py` | `_resolve_entry_data`, `_get_listing_name_index`, `_resolve_listing_id`, `_prune_locked_state`, `_log_locked_reservation`, module-level state (`_LOCKED_*`) | ~165 | < 200 |
| `reservation_handlers.py` | `async_handle_set_door_code`, `async_handle_get_reservations`, `async_handle_find_reservation`, `_reservation_result` | ~310 | < 400 |
| `task_handlers.py` | `async_handle_create_task`, `async_handle_update_task`, `async_handle_delete_task`, `async_handle_get_tasks` | ~340 | < 400 |
| `lookup_handlers.py` | `async_handle_get_users`, `async_handle_get_groups` | ~95 | < 150 |

**Total current**: 1129 lines → Estimated total after split: ~1150 lines (slight increase from file headers and imports in each module).

---

### RQ-7: Dependency Direction Between Sub-Modules

**Decision**: Strict one-way dependency: `__init__.py` → handler modules → `helpers.py` / `schemas.py`. No circular imports.

**Import Graph**:

```
__init__.py
  ├── imports from reservation_handlers (handler functions)
  ├── imports from task_handlers (handler functions)
  ├── imports from lookup_handlers (handler functions)
  └── imports from schemas (schema objects)

reservation_handlers.py
  ├── imports from helpers (_resolve_entry_data, _log_locked_reservation, _resolve_listing_id)
  └── imports from schemas (SERVICE_SET_DOOR_CODE_SCHEMA used implicitly via __init__)

task_handlers.py
  ├── imports from helpers (_resolve_entry_data, _resolve_listing_id)
  └── imports from schemas (used implicitly via __init__)

lookup_handlers.py
  └── imports from helpers (_resolve_entry_data)

helpers.py
  └── imports from external packages only (no intra-package imports)

schemas.py
  └── imports from external packages only (no intra-package imports)
```

No module imports from `__init__.py`, eliminating any circular import risk.

---

### RQ-8: Logger Naming Strategy

**Decision**: Each sub-module uses `logging.getLogger(__name__)` which produces per-module logger names.

**Impact**:

- `custom_components.hostaway.services` (current) → `custom_components.hostaway.services.reservation_handlers` (for locked-reservation warnings)
- Tests that check `caplog.at_level("DEBUG", logger="custom_components.hostaway.services")` must be updated to use the sub-module logger name.

**Rationale**: Using `__name__` is the Python standard and provides better log filtering granularity. The test updates are trivial (string replacement).
