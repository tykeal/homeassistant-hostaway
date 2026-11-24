<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Current or Upcoming Reservation Selection

## Local Development

Use the existing project environment; no dependencies are added:

```bash
uv sync --frozen
```

## Test-Driven Implementation

1. In `tests/sensor/test_reservation.py`, add helper tests that pass an
   explicit `today` to `_select_reservation`, and sensor tests that
   monkeypatch `homeassistant.util.dt.now`, for every scenario in the plan's
   test design. Replace `test_past_reservation_still_drives_selected_state`
   with the new expected behavior.
2. Run the focused module and confirm the new tests fail on current code:

   ```bash
   uv run pytest tests/sensor/test_reservation.py -q
   ```

3. Implement the parser, classification, selection, and sensor wiring; pin
   the Home Assistant date in existing tests whose fixture dates are now past;
   rerun the focused tests.
4. Run the full suite and quality gates:

   ```bash
   uv run pytest tests/ -q
   uv run ruff check custom_components/ tests/
   uv run mypy custom_components/
   uv run interrogate custom_components/
   uv run pre-commit run --all-files
   ```

No live API or manual Home Assistant testing is required: the behavior is
fully determined by reservation fixtures and a frozen Home Assistant clock.
