# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Constants for the Hostaway integration."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from homeassistant.const import Platform

DOMAIN: str = "hostaway"
VERSION: str = "0.0.0"
CONF_CLIENT_ID: str = "client_id"
CONF_CLIENT_SECRET: str = "client_secret"
CONF_SELECTED_LISTINGS: str = "selected_listings"
CONF_SCAN_INTERVAL: str = "scan_interval"
CONF_RESERVATION_SCAN_INTERVAL: str = "reservation_scan_interval"
CONF_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: str = (
    "custom_field_definitions_scan_interval"
)
DEFAULT_SCAN_INTERVAL: int = 5  # minutes
MIN_SCAN_INTERVAL: int = 1  # minutes
DEFAULT_RESERVATION_SCAN_INTERVAL: int = 2  # minutes
DEFAULT_CUSTOM_FIELD_DEFINITIONS_SCAN_INTERVAL: int = 15  # minutes
PLATFORMS: list[Platform] = [Platform.SENSOR]
CONF_CACHED_TOKEN: str = "cached_token"
CONF_RATE_LIMIT_BUDGET: str = "rate_limit_budget"
#: The options form groups the budget under a collapsed section so the
#: operators who never need it never see it.
OPTIONS_SECTION_ADVANCED: str = "advanced"
#: Deliberately a top-level ``hass.data`` key rather than a member of
#: ``hass.data[DOMAIN]``: unload consults that dict to decide whether the
#: last entry has gone, and a limiter living there would never let it empty.
DATA_RATE_LIMITERS: str = "hostaway_rate_limiters"
CONF_FILTER_CANCELLED: str = "filter_cancelled"
DEFAULT_FILTER_CANCELLED: bool = True
CONF_CUSTOM_FIELD_WRITE_ACCOUNT_ID: str = "custom_field_write_account_id"
CONF_LISTING_CUSTOM_FIELD_WRITES_ENABLED: str = "listing_custom_field_writes_enabled"
CONF_RESERVATION_CUSTOM_FIELD_WRITES_ENABLED: str = (
    "reservation_custom_field_writes_enabled"
)
CONF_RESERVATION_CUSTOM_FIELD_RESIDUAL_RISK_ACCEPTED: str = (
    "reservation_custom_field_residual_risk_accepted"
)
CONF_RESERVATION_CUSTOM_FIELD_RISK_ACCEPTED_ACCOUNT_ID: str = (
    "reservation_custom_field_risk_accepted_account_id"
)
