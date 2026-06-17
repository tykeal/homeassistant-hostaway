<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project will be documented
in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Initial development — will become 0.1.0 on first release.

### Added

- OAuth 2.0 client credentials authentication with
  automatic token refresh
- Multi-step config flow with listing selection
- Options flow for polling intervals
- Listing sensors: status, base price, bedrooms,
  bathrooms, max guests
- Reservation sensors with guest name state and door
  code attributes
- `hostaway.set_door_code` service for updating
  reservation door codes
- `hostaway.get_reservations` service firing
  `hostaway_reservations_retrieved` event
- Automatic retry with exponential backoff on API
  rate limits and server errors
- HACS compatibility

### Fixed

- Reservations with zero nights no longer crash
  coordinator refreshes
- A single malformed reservation is skipped and
  logged instead of failing the whole batch
