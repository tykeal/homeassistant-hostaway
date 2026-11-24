<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Current or Upcoming Reservation Selection

## Existing Reservation

The immutable `HostawayReservation` object is the sole input. No fields are
added; API parsing now rejects invalid dates (see Ingest Validity), and no
derived state is persisted.

| Field | Type | Use |
| --- | --- | --- |
| `id` | `int` | Final tie-break for current and upcoming reservations. |
| `check_in` | `str` | Canonical date (validated at ingest); orders stays. |
| `check_out` | `str` | Canonical date (validated at ingest); decides past. |
| `status` | `str` | Existing `_STATUS_PRIORITY` rank within a group. |
| Other fields | Existing | Passed through to attributes unchanged. |

## Ingest Validity (Amendment, Issue #300)

`HostawayReservation` rejects invalid dates with `ValueError` at
construction, so no reservation with invalid dates exists downstream:

| Rule | Error message (no raw value) |
| --- | --- |
| `check_in` is a canonical `YYYY-MM-DD` date | `check_in must be ...` |
| `check_out` is a canonical `YYYY-MM-DD` date | `check_out must be ...` |
| `check_out` is on or after `check_in` | `check_out must not ...` |

"Canonical" means a string equal to the ISO form of the date it parses to.
Equal dates (zero-night stays) are valid; `nights` is not cross-checked.
List parsing skips a rejected record and logs at most its reservation ID,
the listing ID, and the generic message.

## Transient Date Group

Evaluated per reservation against `today: datetime.date` supplied by the
sensor, in this order:

| Group | Condition | Selection | Upcoming list |
| --- | --- | --- | --- |
| Past | Check-out before `today`. | Never | No |
| Current | Check-in on or before `today`. | 1st | Yes |
| Upcoming | Check-in after `today`. | 2nd | Yes |

Because past is checked first and dates are valid by construction, a current
reservation always satisfies check-in ≤ today ≤ check-out. The original
design's "undated" group, ranked third for selection and retained in the list
when its check-out was unparsable, is removed.

## Rank Key

The selected reservation is the minimum rank key among non-past entries.
Lower values win; `priority` is `_STATUS_PRIORITY[status]`, or the existing
fallback for unknown statuses.

| Group | Key |
| --- | --- |
| Current | `(0, priority, -check_in.toordinal(), id)` |
| Upcoming | `(1, priority, check_in.toordinal(), id)` |

## State Transitions

As `today` advances or data refreshes, a reservation moves
upcoming → current → past. A selection therefore moves from the next booking,
to the stay in progress, to the following booking, or to no reservation. With
no selection the sensor reports `no_reservation` and the existing null-detail
attributes.

## Invariants

- A past reservation is never selected and never appears in
  `upcoming_reservations`; a reservation with invalid dates cannot be
  constructed.
- A selected reservation always appears in `upcoming_reservations` computed
  for the same `today`.
- The rank key is a total order over distinct reservations, so the result is
  independent of input order.
- The helpers do not mutate reservations or the coordinator's collections.
