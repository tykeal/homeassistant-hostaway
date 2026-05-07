# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Hostaway API client library (HA-independent).

Public API surface for the Hostaway API client package. All symbols
exported here are part of the stable public interface.
"""

from custom_components.hostaway.api.const import (
    BASE_URL,
    TOKEN_URL,
)
from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
    HostawayResponseError,
)
from custom_components.hostaway.api.models import (
    AccessToken,
    HostawayListing,
    HostawayReservation,
)

__all__ = [
    "BASE_URL",
    "TOKEN_URL",
    "AccessToken",
    "HostawayApiError",
    "HostawayAuthError",
    "HostawayConnectionError",
    "HostawayListing",
    "HostawayRateLimitError",
    "HostawayReservation",
    "HostawayResponseError",
]
