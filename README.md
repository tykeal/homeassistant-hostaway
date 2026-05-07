<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Hostaway

A [Home Assistant](https://www.home-assistant.io/) custom
integration for
[Hostaway](https://www.hostaway.com/) property management.

## Overview

This integration connects Home Assistant to your Hostaway
account using OAuth 2.0 client credentials authentication.
It provides sensor entities for your property listings and
reservations, plus services for managing door codes and
retrieving reservation data.

**Features:**

- OAuth 2.0 client credentials with automatic token refresh
- Multi-step config flow with listing selection
- Options flow for polling interval configuration
- Listing sensors for property details
- Reservation sensors with guest and door code info
- Services for door code management and reservation queries
- Automatic retry with exponential backoff on API errors
- HACS compatible

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "Hostaway" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/hostaway/` to your
   `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Prerequisites

Obtain API credentials from the
[Hostaway Dashboard](https://dashboard.hostaway.com/):

1. Navigate to **Settings → API**
2. Note your **Client ID** and **Client Secret**

### Config Flow

1. Go to **Settings → Devices & Services**
2. Click **Add Integration** → search for **Hostaway**
3. Enter your Client ID and Client Secret
4. Select listings to monitor
5. Configure polling intervals (optional)

### Options

| Option | Default | Minimum |
| --- | --- | --- |
| Listing scan interval | 5 min | 1 min |
| Reservation scan interval | 2 min | 1 min |

## Entities

### Listing Sensors

For each selected listing, these sensors are created:

| Sensor | Description | Example |
| --- | --- | --- |
| Status | Listing active/inactive | `active` |
| Base price | Default nightly rate | `150.0` |
| Bedrooms | Number of bedrooms | `2` |
| Bathrooms | Number of bathrooms | `1.5` |
| Max guests | Guest capacity | `4` |

Entity IDs follow the pattern:
`sensor.hostaway_<listing_slug>_<attribute>`

### Reservation Sensors

Each reservation creates a sensor with:

- **State**: Guest name
- **Attributes**:
  - `check_in` — arrival date
  - `check_out` — departure date
  - `status` — reservation status
  - `door_code` — access code
  - `door_code_vendor` — lock system vendor
  - `door_code_instruction` — access instructions
  - `num_guests` — guest count

## Services

### `hostaway.set_door_code`

Update a reservation's door access code.

| Parameter | Required | Description |
| --- | --- | --- |
| `reservation_id` | Yes | Reservation ID |
| `door_code` | Yes | New door code |
| `door_code_vendor` | No | Lock vendor name |
| `door_code_instruction` | No | Access instructions |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.get_reservations`

Fetch reservations for a listing and fire a
`hostaway_reservations_retrieved` event.

| Parameter | Required | Description |
| --- | --- | --- |
| `listing_id` | Yes | Listing ID |
| `config_entry_id` | No | Required if multiple entries |

## Automation Examples

### Set door code on reservation confirmation

```yaml
automation:
  - alias: "Set door code for new reservation"
    trigger:
      - platform: state
        entity_id: sensor.beach_villa_reservation_1234
        attribute: status
        to: "confirmed"
    action:
      - service: hostaway.set_door_code
        data:
          reservation_id: 1234
          door_code: >-
            {{ range(1000, 9999) | random }}
          door_code_vendor: "smartlock"
          door_code_instruction: "Use the keypad"
```

Reservation entity IDs follow the pattern
`sensor.<listing_device_name>_reservation_<id>`, where
the listing device name is the slugified listing name
(e.g., `sensor.beach_villa_reservation_1234`).

### Notify on new reservations

```yaml
automation:
  - alias: "Check reservations hourly"
    trigger:
      - platform: time_pattern
        hours: "/1"
    action:
      - service: hostaway.get_reservations
        data:
          listing_id: 12345
  - alias: "Notify on reservation data"
    trigger:
      - platform: event
        event_type: hostaway_reservations_retrieved
    action:
      - service: notify.mobile_app
        data:
          title: "Hostaway Update"
          message: >-
            {{ trigger.event.data.listing_name }}:
            {{ trigger.event.data.reservations | length }}
            reservations
```

## License

This project is licensed under the
[Apache License 2.0](LICENSE).
