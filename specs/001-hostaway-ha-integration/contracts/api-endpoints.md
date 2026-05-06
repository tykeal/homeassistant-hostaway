# API Endpoints Contract: Hostaway Integration

This document defines the Hostaway API endpoints consumed by the integration,
their parameters, and expected response formats.

## Authentication

### POST /v1/accessTokens

**Purpose**: Acquire OAuth 2.0 access token.

**Request**:

```text
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=<account_id>
&client_secret=<secret>
&scope=general
```

**Response** (200 OK):

```json
{
  "status": "success",
  "result": {
    "access_token": "eyJ...",
    "token_type": "Bearer",
    "expires_in": 63072000
  }
}
```

**Error responses**:

- 401: Invalid credentials → `{ "status": "fail", "result": "..." }`
- 429: Rate limited

**Post-acquisition**: Wait ≥1 second before any subsequent API call.

---

## Listings

### GET /v1/listings

**Purpose**: Retrieve all listings for the authenticated account.

**Parameters**:

| Param  | Type | Default | Description                |
| ------ | ---- | ------- | -------------------------- |
| limit  | int  | 20      | Results per page (max 100) |
| offset | int  | 0       | Pagination offset          |

**Headers**: `Authorization: Bearer <token>`

**Response** (200 OK):

```json
{
  "status": "success",
  "result": [
    {
      "id": 12345,
      "name": "Beach House",
      "internalName": "BH-01",
      "isActive": 1,
      "address": "123 Ocean Dr, Miami, FL",
      "city": "Miami",
      "countryCode": "US",
      "propertyType": "house",
      "bedroomsNumber": 3,
      "bathroomsNumber": 2.0,
      "personCapacity": 6,
      "price": 250.00,
      "currencyCode": "USD",
      "checkInTimeStart": "15:00",
      "checkInTimeEnd": "20:00",
      "checkOutTime": "11:00",
      "isListed": 1
    }
  ],
  "count": 1,
  "limit": 100,
  "offset": 0,
  "page": 1,
  "totalPages": 1
}
```

**Pagination**: Iterate with increasing `offset` until `offset >= count`.

---

## Reservations

### GET /v1/reservations

**Purpose**: Retrieve reservations, optionally filtered by listing.

**Parameters**:

| Param            | Type | Default | Description                |
| ---------------- | ---- | ------- | -------------------------- |
| listingId        | int  | —       | Filter by listing ID       |
| limit            | int  | 20      | Results per page (max 100) |
| afterId          | int  | —       | Cursor: after this ID      |
| includeResources | int  | 0       | 1 = include customFields   |

**Headers**: `Authorization: Bearer <token>`

**Response** (200 OK):

```json
{
  "status": "success",
  "result": [
    {
      "id": 67890,
      "listingMapId": 12345,
      "guestName": "John Doe",
      "arrivalDate": "2025-08-01",
      "departureDate": "2025-08-05",
      "status": "confirmed",
      "channelName": "airbnb",
      "numberOfGuests": 4,
      "totalPrice": 1000.00,
      "currency": "USD",
      "doorCode": "1234",
      "doorCodeVendor": "August",
      "doorCodeInstruction": "Enter via front door",
      "confirmationCode": "ABC123",
      "nights": 4
    }
  ],
  "count": 1,
  "limit": 100,
  "offset": 0
}
```

**Pagination**: Use `afterId=<last_result_id>` for cursor-based pagination.
Continue until response `result` is empty or `result.length < limit`.

### PUT /v1/reservations/{reservationId}

**Purpose**: Update reservation fields (used for door code assignment).

**Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`

**Request body** (partial update):

```json
{
  "doorCode": "5678",
  "doorCodeVendor": "Yale",
  "doorCodeInstruction": "Use keypad on front door"
}
```

**Response** (200 OK):

```json
{
  "status": "success",
  "result": { /* updated reservation object */ }
}
```

**Error responses**:

- 404: Reservation not found
- 403: Token expired → refresh and retry
- 429: Rate limited → exponential backoff

---

## Error Handling Summary

| HTTP Code     | Meaning               | Action                    |
| ------------- | --------------------- | ------------------------- |
| 200           | Success               | Parse response            |
| 403           | Token expired/invalid | Refresh token, retry once |
| 404           | Resource not found    | Raise specific error      |
| 429           | Rate limited          | Backoff (1s,2s,4s) max 3  |
| 5xx           | Server error          | Retry with backoff        |
| Network error | Connection failed     | Raise connection error    |
