<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Custom Field Write Safety Evidence

## 2026-10-04 Hostaway support correspondence

The 2026-10-04 written Hostaway Technical Support response is recorded as the
authoritative evidence for listing partial `PUT /v1/listings/{id}` omitted
top-level field preservation. Hostaway stated that a request body containing
only `customFieldValues` updates only included fields and leaves omitted
top-level listing fields, including `doorCode`, pricing, and availability,
untouched. This disproves Option C for omitted top-level listing fields.

Certainty levels remain distinct:

- **AUTHORITATIVE**: partial listing `PUT /v1/listings/{id}` preserves omitted
  top-level fields.
- **OBSERVED-ONLY, not documented contract**: array fields appear to be
  replaced wholesale per array, based on Hostaway's `listingImages` precedent.
  This is why production writes must send the complete merged
  `customFieldValues` array produced by `build_custom_field_values_payload`
  and must never send only the changed entry.
- **EXPLICITLY UNCONFIRMED plus known bug**: reservation and task
  `customFieldValues` write semantics are not documented or confirmed.
  Hostaway has a tracked bug where the reservation update endpoint returned
  HTTP success without persisting a custom field value.

No live listing ladder step was executed for this PR. Listing Steps 4 and 5
remain optional corroboration and must not run unless a verified
target-specific conflict-safe recovery path with conditional/version
protection exists before any listing mutation.

## Conditional/version protection investigation

The repository's Hostaway API client, contracts, and feature research contain
no support for ETag, `If-Match`, version fields, or another conditional update
mechanism on listing or reservation update endpoints. The implementation
therefore keeps full-object listing dispatch disabled. The selected listing
strategy is the partial payload strategy: send exactly
`{"customFieldValues": complete_merged_array}` after reading the current target
with `includeResources=1`.

Because no conditional/version-protected recovery path is available,
post-write unrelated-data-change detection raises an actionable error and does
not send a second mutation or blind stale-snapshot restore.

## Reservation evidence and residual risk

The existing built-in reservation door-code service sends only `doorCode` plus
optional `doorCodeVendor` and `doorCodeInstruction` through
`HostawayApiClient.update_reservation` in
`custom_components/hostaway/services/reservation_handlers.py`. That is
supporting implementation evidence for reservation top-level partial updates
only.

This repository does not substantiate a v0.4.0 production release or any
no-data-loss production history. Reservation `customFieldValues`
merge-versus-replace semantics remain explicitly unconfirmed. Reservation
custom-field writes are enabled only with mandatory FR-056 read-back
verification and account-bound owner residual-risk acceptance; other accounts
or config entries remain disabled.
