<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Upcoming Reservation Date Filter

## Local Development

Use the existing project environment and do not add dependencies:

```bash
uv sync --frozen
```

## Test-Driven Implementation

1. In `tests/sensor/test_reservation.py`, add deterministic unit and sensor
   tests for strict-past exclusion, checkout-today and future retention,
   malformed date handling, order/shape, local timezone boundaries, existing
   cancellation behavior, and unchanged state selection.
2. Run the focused module and verify new behavior tests fail before adding
   production changes:

   ```bash
   uv run pytest tests/sensor/test_reservation.py -q
   ```

3. Implement the date filter and sensor-local-date handoff, then rerun the
   focused tests.
4. Run the full suite and required quality gates:

   ```bash
   uv run pytest tests/ -x -q
   uv run ruff check custom_components/ tests/
   uv run mypy custom_components/
   uv run interrogate custom_components/
   uv run pre-commit run --all-files
   ```

The full test suite is the regression check for reservation state, API parsing,
coordinator behavior, services, and unrelated entities. Do not perform live API
or Home Assistant manual testing; the behavior is fully testable with local
fixtures and a frozen Home Assistant clock.
