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
reservations, plus services for managing door codes, tasks,
and retrieving reservation data.

**Features:**

- OAuth 2.0 client credentials with automatic token refresh
- Multi-step config flow with listing selection
- Options flow for polling interval configuration
- Listing sensors for property details
- Reservation sensors with guest and door code info
- Services for door code management and reservation queries
- Task management services (create, update, delete, list)
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
| Rate limit budget (advanced) | 180 | 1 |

## Rate limits

### How Hostaway counts requests

Hostaway applies **two** counters to ordinary endpoint calls, and a
request is charged to both of them:

- an **account** counter — 200 requests per 10 seconds, shared by
  everything using your Client ID, including other software you run
- an **IP** counter — 200 requests per 10 seconds, shared by everything
  calling Hostaway from the same address, including other Home Assistant
  integrations and other households behind the same connection

The integration tracks both counters itself and paces its own outbound
traffic to stay underneath them, rather than sending requests and
reacting to refusals afterwards.

### The budget, and why it is 180

The default budget is **180 requests per 10 seconds**, which is a
deliberate safety margin of roughly ten percent below the 200 Hostaway
allows. **180 is not a Hostaway-published figure.** It exists because
the integration cannot see traffic it did not send: the margin is the
room left for everything else sharing your account or your address.

The budget is in the **advanced** section of the integration's options.
Lower it if you share the account or the address with other Hostaway
software; there is rarely a reason to raise it. The 10-second window is
Hostaway's and is not configurable.

Even at a conservative budget, sharing an account or an IP address with
other Hostaway clients can still produce server-side pushback that this
integration cannot predict, because it can only count what it sends. It
handles that pushback when it arrives.

### When entities go stale

If a scheduled refresh cannot get capacity within a couple of seconds,
the integration **drops that cycle** rather than failing it. Entities
stay *available* and keep showing the values from the previous
successful poll, and the next scheduled refresh tries again. Only
actions you trigger yourself — a service call, a config flow — are
allowed to wait longer, up to 30 seconds.

So **stale-but-available entities with no errors in the log are a
capacity symptom, not a fault.** The log says so explicitly, naming the
refresh that was dropped and how long it waited.

The remedy is to ask for less, not to file a bug:

- lengthen the listing and reservation scan intervals
- reduce the number of selected listings, since reservations are
  fetched per listing
- lower the rate limit budget if something else shares the account or
  the address, so that the two paced clients add up to less than 200

There is one case you cannot tune away. When Hostaway refuses a request
it may send an `X-RateLimit-Retry-After` telling us to stop for a
while. The integration honours it exactly. If that suppression runs
longer than an interactive call's own 30-second deadline, the call fails
with a wait timeout even though nothing is wrong with it and even though
you have asked for very little — the integration is not permitted to
send during the suppression, and the deadline expires inside it. Retry
after the suppression ends.

### Diagnostics

Download diagnostics from the integration's entry menu to see which
counter is actually holding things up: each counter is reported
separately, along with how many refresh cycles each coordinator dropped
and which counter Hostaway named when it last refused a request. The
payload contains no credentials and nothing identifying the account.

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

### `hostaway.create_task`

Create a new task in Hostaway. Supports `response_variable` for
accessing the created task data. Supported `status` values are
`pending`, `confirmed`, `inProgress`, `completed`, and
`cancelled`.

| Parameter | Required | Description |
| --- | --- | --- |
| `title` | Yes | Task title |
| `description` | No | Detailed task description |
| `listing_id` | No | Associated listing ID |
| `listing_name` | No | Listing by internal name (resolved from cache) |
| `reservation_id` | No | Associated reservation ID |
| `status` | No | Task status |
| `priority` | No | Priority level (positive integer) |
| `assignee_user_id` | No | Assigned user ID |
| `can_be_picked_by_group_id` | No | Group ID that can pick up the task |
| `supervisor_user_id` | No | Supervisor user ID |
| `categories_map` | No | List of category IDs |
| `can_start_from` | No | Earliest start date (ISO format) |
| `should_end_by` | No | Deadline date (ISO format) |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.update_task`

Update an existing task. At least one field besides `task_id`
must be provided. Supports `response_variable`.

| Parameter | Required | Description |
| --- | --- | --- |
| `task_id` | Yes | Task ID to update |
| `title` | No | New task title |
| `description` | No | New description |
| `listing_id` | No | New listing ID |
| `listing_name` | No | New listing by internal name |
| `reservation_id` | No | New reservation ID |
| `status` | No | New status |
| `priority` | No | New priority level |
| `assignee_user_id` | No | New assignee user ID |
| `can_be_picked_by_group_id` | No | New pickup group ID |
| `supervisor_user_id` | No | New supervisor user ID |
| `categories_map` | No | New list of category IDs |
| `can_start_from` | No | New earliest start date |
| `should_end_by` | No | New deadline date |
| `resolution_note` | No | Note for resolving/completing |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.delete_task`

Delete a task from Hostaway.

| Parameter | Required | Description |
| --- | --- | --- |
| `task_id` | Yes | Task ID to delete |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.get_tasks`

Retrieve tasks with optional filters. Supports
`response_variable`; the response is a mapping with a
`tasks` list.

| Parameter | Required | Description |
| --- | --- | --- |
| `listing_id` | No | Filter by listing ID |
| `listing_name` | No | Filter by listing internal name |
| `reservation_id` | No | Filter by reservation ID |
| `status` | No | Filter by status |
| `can_start_from_start` | No | Date range start filter |
| `can_start_from_end` | No | Date range end filter |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.get_users`

Retrieve all users from the Hostaway account. Use this to
look up user IDs for task assignee and supervisor fields.
Supports `response_variable`.

| Parameter | Required | Description |
| --- | --- | --- |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.get_groups`

Retrieve all user groups from the Hostaway account. Use
this to look up group IDs for the
`can_be_picked_by_group_id` task field. Supports
`response_variable`.

| Parameter | Required | Description |
| --- | --- | --- |
| `config_entry_id` | No | Required if multiple entries |

### `hostaway.find_reservation`

Look up a reservation by guest name and dates. Supports
`response_variable`.

| Parameter | Required | Description |
| --- | --- | --- |
| `guest_name` | Yes | Guest name (case-insensitive substring) |
| `check_in` | Yes | Arrival date (exact match) |
| `check_out` | Yes | Departure date (exact match) |
| `listing_id` | No | Limit to a specific listing |
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

Reservation sensors are named `Reservation <id>` and grouped
under the listing device. Home Assistant combines the device
name with the entity name to produce entity IDs like
`sensor.beach_villa_reservation_1234`.

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

### Create task on low battery

This example uses `action:` instead of `service:` because
`response_variable` requires the newer action syntax.

```yaml
automation:
  - alias: "Create maintenance task on low battery"
    trigger:
      - platform: numeric_state
        entity_id: sensor.smoke_detector_battery
        below: 20
    action:
      - action: hostaway.create_task
        data:
          title: "Replace smoke detector batteries"
          description: >-
            Battery level at
            {{ states('sensor.smoke_detector_battery') }}%
          listing_name: "ocean-suite-1"
          status: "pending"
          priority: 1
        response_variable: task_result
```

## License

This project is licensed under the
[Apache License 2.0](LICENSE).
