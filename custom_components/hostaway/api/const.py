# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""API constants for the Hostaway client library."""

TOKEN_URL: str = "https://api.hostaway.com/v1/accessTokens"
BASE_URL: str = "https://api.hostaway.com"
DEFAULT_TIMEOUT: int = 30
TOKEN_READY_DELAY: float = 1.0
MAX_RETRIES: int = 3
INITIAL_BACKOFF: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
MAX_BACKOFF: float = 30.0
RATE_LIMIT_PER_IP: int = 15  # per 10 seconds
RATE_LIMIT_PER_ACCOUNT: int = 20  # per 10 seconds
DEFAULT_PAGE_LIMIT: int = 100
GRANT_TYPE: str = "client_credentials"
SCOPE: str = "general"
