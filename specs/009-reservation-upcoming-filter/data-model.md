<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Upcoming Reservation Date Filter

## Existing Reservation

The existing immutable `HostawayReservation` object is the sole input record.
The implementation does not add fields, alter API parsing, or persist derived
state.

| Field | Type | Rule |
| --- | --- | --- |
| `check_out` | `str` | Parse as canonical date; compare with local today. |
| ID, guest, check-in, status | Existing fields | Pass through unchanged. |
| Other model fields | Existing fields | Not examined or modified. |

## Transient Date Decision

For each reservation supplied to the attribute-list filter, classify its
check-out value against a `today: datetime.date` supplied by the sensor:

| Classification | Condition | Attribute result |
| --- | --- | --- |
| Past | Parsed date is before `today` | Exclude. |
| Today or future | Parsed date is on or after `today` | Retain. |
| Unparsable | Invalid value reaches the helper | Retain; warn once. |

This decision is ephemeral and recalculated as the sensor constructs its
attributes. Entries that do not reach the sensor because existing API model
validation rejects them are not changed by this feature.

## Invariants

- Input order is retained for every output record.
- A record is removed only when its check-out date is valid and strictly less
  than `today`.
- The attribute entry shape and unrelated selected-reservation attributes are
  unchanged.
- The helper does not mutate reservations or the coordinator's collection.
