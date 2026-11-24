<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Current or Upcoming Reservation Selection

## Existing Reservation

The immutable `HostawayReservation` object is the sole input. No fields are
added, API parsing is unchanged, and no derived state is persisted.

| Field | Type | Use |
| --- | --- | --- |
| `id` | `int` | Final tie-break for dated reservations. |
| `check_in` | `str` | Parsed as a canonical date for grouping and ordering. |
| `check_out` | `str` | Parsed as a canonical date for past and range checks. |
| `status` | `str` | Existing `_STATUS_PRIORITY` rank within a group. |
| Other fields | Existing | Passed through to attributes unchanged. |

## Transient Date Group

Evaluated per reservation against `today: datetime.date` supplied by the
sensor, in this order:

| Group | Condition | Rank |
| --- | --- | --- |
| Past | Check-out is valid and before `today`. | Never |
| Undated | Not past; a date is invalid or check-out precedes check-in. | 3rd |
| Current | Both valid; check-in on or before `today`. | 1st |
| Upcoming | Both valid; check-in after `today`. | 2nd |

"Valid" means a string equal to the ISO form of the date it parses to
(`YYYY-MM-DD`). Because past is checked first and check-out is on or after
check-in for dated groups, a current reservation always satisfies
check-in ≤ today ≤ check-out.

## Rank Key

The selected reservation is the minimum rank key among non-past entries.
Lower values win; `priority` is `_STATUS_PRIORITY[status]`, or the existing
fallback for unknown statuses.

| Group | Key |
| --- | --- |
| Current | `(0, priority, -check_in.toordinal(), id)` |
| Upcoming | `(1, priority, check_in.toordinal(), id)` |
| Undated | `(2, priority, 0, input_index)` |

## State Transitions

As `today` advances or data refreshes, a reservation moves
upcoming → current → past. A selection therefore moves from the next booking,
to the stay in progress, to the following booking, or to no reservation. With
no selection the sensor reports `no_reservation` and the existing null-detail
attributes.

## Invariants

- A past reservation is never selected.
- A selected reservation always appears in `upcoming_reservations` computed
  for the same `today`.
- The rank key is a total order over distinct reservations, so the result is
  independent of input order except within the undated group.
- The helpers do not mutate reservations or the coordinator's collections.
