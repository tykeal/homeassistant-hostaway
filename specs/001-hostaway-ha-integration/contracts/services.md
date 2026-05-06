# Service Contracts: Hostaway Integration

## Service: hostaway.set_door_code

**Purpose**: Update door code fields on a Hostaway reservation.

**Schema** (`services.yaml`):

```yaml
set_door_code:
  name: Set door code
  description: Update the door code on a Hostaway reservation.
  fields:
    reservation_id:
      name: Reservation ID
      description: The Hostaway reservation ID to update.
      required: true
      selector:
        number:
          min: 1
          mode: box
    door_code:
      name: Door code
      description: The door access code to assign.
      required: true
      selector:
        text:
    door_code_vendor:
      name: Door code vendor
      description: The door lock system vendor name.
      required: false
      selector:
        text:
    door_code_instruction:
      name: Door code instruction
      description: Instructions for the guest on how to use the code.
      required: false
      selector:
        text:
          multiline: true
```

**Behavior**:

1. Validate `reservation_id` is a positive integer.
2. Validate `door_code` is non-empty string.
3. Call Hostaway API: `PUT /v1/reservations/{reservation_id}` with payload:

   ```json
   {
     "doorCode": "<door_code>",
     "doorCodeVendor": "<door_code_vendor>",
     "doorCodeInstruction": "<door_code_instruction>"
   }
   ```

4. Only include optional fields in payload if provided (non-null).
5. On success: return silently (void service).
6. On 404: raise `ServiceValidationError` with "Reservation not found".
7. On 429: retry with exponential backoff (up to 3 retries).
8. On 403 (expired token): refresh token transparently, retry once.

---

## Service: hostaway.get_reservations

**Purpose**: Retrieve all reservations for a specific listing and fire an event.

**Schema** (`services.yaml`):

```yaml
get_reservations:
  name: Get reservations
  description: Retrieve reservations for a listing and fire an event.
  fields:
    listing_id:
      name: Listing ID
      description: The Hostaway listing ID to query reservations for.
      required: true
      selector:
        number:
          min: 1
          mode: box
```

**Behavior**:

1. Validate `listing_id` is a positive integer.
2. Call Hostaway API: `GET /v1/reservations?listingId={listing_id}` with
   cursor-based pagination (`afterId`) until all results fetched.
3. Map each reservation to snake_case payload object.
4. Fire event `hostaway_reservations_retrieved` with payload:

   ```json
   {
     "listing_id": 12345,
     "listing_name": "Beach House",
     "reservations": [
       {
         "id": 67890,
         "guest_name": "John Doe",
         "check_in": "2025-08-01",
         "check_out": "2025-08-05",
         "status": "confirmed",
         "door_code": "1234"
       }
     ]
   }
   ```

5. On 404/invalid listing: fire event with empty reservations list.
6. `listing_name` resolved from ListingsCoordinator cached data.

---

## Config Flow Contract

### Step 1: User Credentials

**Input fields**:

- `client_id` (str, required): Hostaway account ID
- `client_secret` (str, required): API secret

**Validation**:

- Acquire token via `POST /v1/accessTokens`
- Test connection with authenticated request
- On auth failure: show `invalid_auth` error
- On network failure: show `cannot_connect` error

**Output**: Proceeds to Step 2 on success.

### Step 2: Listing Selection

**Input fields**:

- `selected_listings` (list[int], required): Multi-select of listings

**Population**: Fetches all active listings via authenticated API call.

**Validation**: At least one listing must be selected.

**Output**: Creates config entry with credentials + selected listings.

### Options Flow

**Input fields**:

- `scan_interval` (int, default=5, min=1): Listing poll minutes
- `reservation_scan_interval` (int, default=2, min=1): Reservation poll minutes

**Output**: Updates config entry options, triggers coordinator interval reload.
