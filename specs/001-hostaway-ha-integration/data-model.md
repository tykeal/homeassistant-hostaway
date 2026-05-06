# Data Model: Hostaway Home Assistant Integration

## Entities

### AccessToken

Represents an OAuth 2.0 access token for the Hostaway API.

| Field        | Type           | Description                 | Validation     |
| ------------ | -------------- | --------------------------- | -------------- |
| access_token | str            | Bearer token value          | Non-empty      |
| token_type   | str            | Always "Bearer"             | Non-empty      |
| expires_in   | int            | Token lifetime in seconds   | Positive       |
| issued_at    | datetime (UTC) | When the token was acquired | Timezone-aware |

**Relationships**: None (standalone credential)

**State transitions**: Valid → Expired (time-based) → Refreshed (new token acquired)

**Derived properties**:

- `expires_at`: `issued_at + timedelta(seconds=expires_in)`
- `is_expired(buffer_seconds)`: `now >= expires_at - buffer`
- `seconds_until_ready`: max(0, 1.0 - elapsed since issued_at)
  — enforces 1s post-generation delay

---

### HostawayListing

Represents a property listing in the Hostaway platform.

| Field               | Type          | Description         | Validation      |
| ------------------- | ------------- | ------------------- | --------------- |
| id                  | int           | Unique listing ID   | Positive        |
| name                | str           | Display name        | Non-empty       |
| internal_name       | str \| None   | Internal ref name   | —               |
| status              | str           | Active/inactive     | active/inactive |
| address             | str \| None   | Full address string | —               |
| city                | str \| None   | City name           | —               |
| country_code        | str \| None   | ISO country code    | —               |
| property_type       | str \| None   | Type of property    | —               |
| bedrooms            | int \| None   | Number of bedrooms  | Non-neg if set  |
| bathrooms           | float \| None | Number of bathrooms | Non-neg if set  |
| max_guests          | int \| None   | Maximum occupancy   | Positive if set |
| base_price          | float \| None | Base nightly price  | Non-neg if set  |
| currency            | str \| None   | Currency code       | —               |
| check_in_time_start | str \| None   | Earliest check-in   | —               |
| check_in_time_end   | str \| None   | Latest check-in     | —               |
| check_out_time      | str \| None   | Check-out time      | —               |
| is_listed           | bool          | Publicly visible    | —               |

**Relationships**:

- One-to-many with Reservation (listing_id foreign key)

**API field mapping** (camelCase → snake_case):

- `id` → `id`
- `name` → `name`
- `internalName` → `internal_name`
- `isActive` → `status` (1="active", 0="inactive")
- `address` → `address` (nested object: `full` field)
- `city` → `city`
- `countryCode` → `country_code`
- `propertyType` → `property_type`
- `bedroomsNumber` → `bedrooms`
- `bathroomsNumber` → `bathrooms`
- `personCapacity` → `max_guests`
- `price` → `base_price`
- `currencyCode` → `currency`
- `checkInTimeStart` → `check_in_time_start`
- `checkInTimeEnd` → `check_in_time_end`
- `checkOutTime` → `check_out_time`
- `isListed` → `is_listed`

---

### HostawayReservation

Represents a guest booking/reservation.

| Field                 | Type          | Description        | Validation      |
| --------------------- | ------------- | ------------------ | --------------- |
| id                    | int           | Reservation ID     | Positive        |
| listing_id            | int           | Parent listing ID  | Positive        |
| guest_name            | str           | Guest full name    | Non-empty       |
| check_in              | str           | Check-in date      | Valid date      |
| check_out             | str           | Check-out date     | Valid date      |
| status                | str           | Reservation status | See statuses    |
| channel               | str \| None   | Booking channel    | —               |
| num_guests            | int \| None   | Number of guests   | Positive if set |
| total_price           | float \| None | Total price        | Non-neg if set  |
| currency              | str \| None   | Currency code      | —               |
| door_code             | str \| None   | Door access code   | —               |
| door_code_vendor      | str \| None   | Door code vendor   | —               |
| door_code_instruction | str \| None   | Guest instructions | —               |
| confirmation_code     | str \| None   | Confirm code       | —               |
| nights                | int \| None   | Number of nights   | Positive if set |

**Valid statuses**: `new`, `pending`, `confirmed`, `cancelled`, `modified`,
`declined`, `expired`, `inquired`, `ownerStay`

**Relationships**:

- Many-to-one with Listing (via listing_id)

**State transitions**:

```text
new → pending → confirmed → cancelled
                         → modified
                         → declined
                         → expired
inquired → confirmed
ownerStay (independent)
```

**API field mapping** (camelCase → snake_case):

- `id` → `id`
- `listingMapId` → `listing_id`
- `guestName` → `guest_name`
- `arrivalDate` → `check_in`
- `departureDate` → `check_out`
- `status` → `status`
- `channelName` → `channel`
- `numberOfGuests` → `num_guests`
- `totalPrice` → `total_price`
- `currency` → `currency`
- `doorCode` → `door_code`
- `doorCodeVendor` → `door_code_vendor`
- `doorCodeInstruction` → `door_code_instruction`
- `confirmationCode` → `confirmation_code`
- `nights` → `nights`

---

## Coordinator Data Structures

### ListingsCoordinator.data

```python
dict[int, HostawayListing]  # listing_id → listing object
```

### ReservationsCoordinator.data

```python
dict[int, list[HostawayReservation]]  # listing_id → sorted reservations
```

Reservations sorted by check_in date (ascending) within each listing group.

---

## Config Entry Storage

### config_entry.data (immutable after setup)

| Key                | Type         | Description                      |
| ------------------ | ------------ | -------------------------------- |
| client_id          | str          | Hostaway account ID              |
| client_secret      | str          | API secret                       |
| cached_token       | dict \| None | Serialized CachedToken           |
| token_generated_at | str \| None  | ISO timestamp of last generation |

### config_entry.options (mutable via options flow)

| Key                       | Type      | Default | Description              |
| ------------------------- | --------- | ------- | ------------------------ |
| selected_listings         | list[int] | all     | IDs of listings to watch |
| scan_interval             | int       | 5       | Listing poll (minutes)   |
| reservation_scan_interval | int       | 2       | Reservation poll (min)   |
