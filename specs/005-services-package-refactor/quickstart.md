<!-- markdownlint-disable MD013 MD040 MD060 -->

# Quickstart: Services Package Refactor

**Feature**: 005-services-package-refactor
**Date**: 2026-06-15

## Overview

This refactor converts the monolithic `custom_components/hostaway/services.py`
(1129 lines) into a `services/` package with focused sub-modules. The change
is purely structural — no behavioral changes.

## Quick Verification

After implementation, run the full test suite:

```bash
uv run pytest tests/test_services.py -v
```

All 66 tests must pass. The only test changes are patch path updates (string
replacements pointing to sub-modules instead of the monolithic module).

## Package Layout

```
custom_components/hostaway/services/
├── __init__.py              # Registration: async_setup_services, async_unregister_services
├── schemas.py              # All vol.Schema objects + validator functions
├── helpers.py              # Shared utilities used by multiple handlers
├── reservation_handlers.py # set_door_code, get_reservations, find_reservation
├── task_handlers.py        # create_task, update_task, delete_task, get_tasks
└── lookup_handlers.py      # get_users, get_groups
```

## Adding a New Service (Post-Refactor)

1. **Add the handler** in the appropriate `*_handlers.py` module:

   ```python
   async def async_handle_my_service(
       hass: HomeAssistant,
       call: ServiceCall,
   ) -> dict[str, Any]:
       """Handle hostaway.my_service call."""
       entry_data = _resolve_entry_data(hass, call.data)
       # ... implementation
   ```

2. **Add the schema** in `schemas.py`:

   ```python
   SERVICE_MY_SERVICE_SCHEMA = vol.Schema({
       vol.Required("some_field"): _positive_int,
   })
   ```

3. **Add a single entry** to `SERVICE_DEFINITIONS` in `__init__.py`:

   ```python
   ServiceDefinition(
       "my_service",
       async_handle_my_service,
       SERVICE_MY_SERVICE_SCHEMA,
       SupportsResponse.ONLY,
   ),
   ```

That's it — no closures, no duplicate registration code.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `functools.partial` for hass injection | Replaces 9 closure definitions; cleaner than lambdas |
| `NamedTuple` for service definitions | Type-safe, immutable, supports named field access |
| Validators in `schemas.py` | Centralized; validators are schema concerns, not handler concerns |
| Locked-state in `helpers.py` | Module-level state stays in helpers; handlers remain stateless |
| `async_unregister_services` function | DRY — uses same table as registration; replaces 9 inline calls |

## File Size Targets

| File | Target | Limit |
|------|--------|-------|
| `__init__.py` | ~65 lines | < 80 |
| `schemas.py` | ~175 lines | < 200 |
| `helpers.py` | ~165 lines | < 200 |
| `reservation_handlers.py` | ~310 lines | < 400 |
| `task_handlers.py` | ~340 lines | < 400 |
| `lookup_handlers.py` | ~95 lines | < 150 |

## Test Patch Path Migration

Tests that patch `custom_components.hostaway.services.X` must update to
reference the sub-module where `X` is actually imported:

| Old Path | New Path |
|----------|----------|
| `...services.HostawayApiClient.update_reservation` | `...services.reservation_handlers.HostawayApiClient.update_reservation` |
| `...services.HostawayApiClient.get_all_reservations` | `...services.reservation_handlers.HostawayApiClient.get_all_reservations` |
| `...services.HostawayApiClient.create_task` | `...services.task_handlers.HostawayApiClient.create_task` |
| `...services.HostawayApiClient.update_task` | `...services.task_handlers.HostawayApiClient.update_task` |
| `...services.HostawayApiClient.delete_task` | `...services.task_handlers.HostawayApiClient.delete_task` |
| `...services.HostawayApiClient.get_tasks` | `...services.task_handlers.HostawayApiClient.get_tasks` |
| `...services.HostawayApiClient.get_users` | `...services.lookup_handlers.HostawayApiClient.get_users` |
| `...services.HostawayApiClient.get_groups` | `...services.lookup_handlers.HostawayApiClient.get_groups` |
| `...services._resolve_entry_data` | `...services.helpers._resolve_entry_data` |
| Logger `custom_components.hostaway.services` | `custom_components.hostaway.services.reservation_handlers` |

## Shell Commands

```bash
# Run all service tests
uv run pytest tests/test_services.py -v

# Check file line counts
wc -l custom_components/hostaway/services/*.py

# Verify old services.py is gone
test ! -f custom_components/hostaway/services.py && echo "OK: removed"

# Run full pre-commit
pre-commit run --all-files

# Type check
uv run mypy custom_components/hostaway/services/
```
