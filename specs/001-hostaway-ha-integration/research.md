# Research: Hostaway Home Assistant Integration

## R-001: Hostaway Authentication Flow

**Decision**: OAuth 2.0 Client Credentials Grant with long-lived token caching.

**Rationale**: The Hostaway API uses a standard Client Credentials flow
(`POST /v1/accessTokens`) with `client_id` (account ID), `client_secret`,
`grant_type=client_credentials`, and `scope=general`. Tokens are valid for
24 months. The API documentation explicitly states tokens should be stored
and reused — not re-generated per request. A mandatory 1-second delay after
token generation is required before making any API calls. HTTP 403 indicates
token expiry requiring refresh.

**Alternatives considered**:

- Per-request token generation: Rejected — violates API documentation guidance,
  wasteful, and risks hitting undocumented token generation rate limits.
- OAuth 2.0 Authorization Code Grant: Not available — Hostaway uses Client
  Credentials only for their public API.

**Implementation notes**:

- Token endpoint: `https://api.hostaway.com/v1/accessTokens`
- Response:
  `{ "access_token": "...", "token_type": "Bearer",
  "expires_in": ... }`
- Token refresh trigger: HTTP 403 (not 401 as in Guesty)
- Post-generation delay: `asyncio.sleep(1.0)` after acquiring new token
- Token persistence: HA config entry data (same pattern as Guesty)

## R-002: Hostaway API Rate Limits

**Decision**: Implement dual-key rate limiting (IP + account) with exponential
backoff on HTTP 429 responses.

**Rationale**: Hostaway enforces two concurrent rate limits:

- 15 requests per 10 seconds per IP address
- 20 requests per 10 seconds per account ID

The stricter limit (15/10s = 1.5 req/s) governs single-instance deployments.
HTTP 429 response indicates rate limit exceeded.

**Alternatives considered**:

- Client-side token bucket: Considered for proactive limiting, but polling
  intervals (2-5 min) naturally keep well under limits. Reactive backoff on
  429 is simpler and sufficient.
- Leaky bucket: Over-engineered for the expected request volume.

**Implementation notes**:

- Exponential backoff: initial=1s, multiplier=2, max=30s, jitter=random(0,1)
- Max retries: 3
- No proactive token bucket needed — polling intervals keep requests sparse
- Service calls (set_door_code) may burst slightly but stay within limits

## R-003: Hostaway API Pagination

**Decision**: Use `afterId` cursor-based pagination for reservations; standard
offset/limit pagination for listings.

**Rationale**: Per Hostaway API changelog (2026-03-31), cursor-based pagination
via `afterId` is available and recommended for reservations. Offset-based
pagination is deprecated for reservations. Listings still use standard
offset/limit pagination.

**Alternatives considered**:

- Offset-based pagination for reservations: Deprecated by Hostaway, has
  performance issues with large datasets.

**Implementation notes**:

- Listings: `GET /v1/listings?limit=100&offset=0` (iterate pages)
- Reservations: `GET /v1/reservations?limit=100&afterId=<last_id>` (cursor)
- Standard response includes `count`, `totalPages`, `page` for listings
- Reservations: continue until response result is empty or count < limit

## R-004: Hostaway Reservation Door Code Fields

**Decision**: Use PUT on the reservation update endpoint to set door code fields.

**Rationale**: The Hostaway reservation object includes `doorCode`,
`doorCodeVendor`, and `doorCodeInstruction` fields (added 2017-10-28).
These are updatable via the standard reservation update endpoint. The API
uses camelCase; HA-facing interfaces use snake_case per FR-014.

**Alternatives considered**:

- Custom fields for door codes: Unnecessary — native fields exist.
- Webhook-based updates: Not applicable for write operations.

**Implementation notes**:

- Endpoint: `PUT /v1/reservations/<reservation_id>`
- Payload:
  `{ "doorCode": "...", "doorCodeVendor": "...",
  "doorCodeInstruction": "..." }`
- Only send non-null fields to avoid overwriting existing data
- Validate reservation exists before update (GET first or handle 404)

## R-005: Entity Naming and Unique IDs

**Decision**: Entity IDs use slugified listing names; unique_ids use immutable
account ID + listing/reservation ID + attribute key.

**Rationale**: Per FR-007 and the constitution (VII. User Experience
Consistency), entity_ids follow `sensor.hostaway_<listing>_<attribute>` with
slugified listing names for readability. Unique_ids are derived from immutable
identifiers to ensure stability across renames and restarts.

**Alternatives considered**:

- Using listing ID in entity_id: Less readable in UI and automations.
- Using listing name in unique_id: Breaks if listing is renamed.

**Implementation notes**:

- Entity ID: `sensor.hostaway_{slugify(listing_name)}_{attribute}`
- Unique ID: `{account_id}_{listing_id}_{attribute_key}` (listings)
- Unique ID: `{account_id}_{reservation_id}_{attribute_key}` (reservations)
- Account ID from config entry data (client_id = account ID per spec)

## R-006: Hostaway API Response Format

**Decision**: Parse standard Hostaway response envelope and validate structure.

**Rationale**: All Hostaway API endpoints return a standard envelope:

```json
{
  "status": "success"|"fail",
  "result": <data or error message>,
  "limit": null|int,
  "offset": null|int,
  "count": int,
  "page": int,
  "totalPages": int
}
```

Boolean values are integers (0/1). All times are UTC except listing-local
check-in/out times. Country codes use ISO 3166-2.

**Implementation notes**:

- Always check `status` field first — "fail" means error
- `result` contains the data array or error message string
- Parse booleans as int (0/1) → Python bool conversion
- All timestamps in UTC; convert listing check-in/out from local timezone
- Field mapping from camelCase to snake_case in model constructors

## R-007: Config Flow Multi-Step Design

**Decision**: Two-step config flow: credentials → listing selection.
Options flow: polling intervals.

**Rationale**: Per FR-004, the config flow requires credentials entry
followed by listing selection. Following the Guesty pattern, the options
flow handles interval adjustments post-setup. The Hostaway API requires
only client_id (account ID) and client_secret — simpler than Guesty's
optional tag-based pre-filtering.

**Alternatives considered**:

- Single-step flow: Cannot show listing selection without authenticated
  API access first.
- Three-step flow with intervals: Over-complex for initial setup;
  intervals are better as options flow.

**Implementation notes**:

- Step 1 (user): client_id + client_secret → validate via token acquisition
- Step 2 (select_listings): fetch all listings → multi-select
- Options flow: listing_scan_interval, reservation_scan_interval
- Unique ID: client_id (prevents duplicate entries for same account)

## R-008: Sensor Architecture

**Decision**: Two coordinators (listings, reservations) with sensor entity
descriptions following the Guesty pattern.

**Rationale**: Separating listing and reservation polling allows different
intervals (5 min vs 2 min defaults) matching their volatility. The
DataUpdateCoordinator pattern handles caching, error recovery, and update
scheduling automatically.

**Alternatives considered**:

- Single coordinator: Forces same interval for both data types;
  reservations change more frequently than listings.
- Direct polling in sensors: Violates HA patterns, no caching benefit.

**Implementation notes**:

- ListingsCoordinator: polls all selected listings, returns dict[int, Listing]
- ReservationsCoordinator: polls reservations for known listings,
  returns dict[int, list[Reservation]] grouped by listing_id
- Listing sensors: status, name, pricing attributes
- Reservation sensors: per-reservation entities with guest/status/dates
- Disappeared listings → sensor becomes unavailable
