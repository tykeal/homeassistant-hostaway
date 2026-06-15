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
DEFAULT_SCAN_INTERVAL: int = 5  # minutes
MIN_SCAN_INTERVAL: int = 1  # minutes
DEFAULT_RESERVATION_SCAN_INTERVAL: int = 2  # minutes
PLATFORMS: list[Platform] = [Platform.SENSOR]
CONF_CACHED_TOKEN: str = "cached_token"
CONF_FILTER_CANCELLED: str = "filter_cancelled"
DEFAULT_FILTER_CANCELLED: bool = True
