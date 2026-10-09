# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""API constants for the Hostaway client library."""

# aislop-ignore-next-line ai-slop/hardcoded-url -- intentional token endpoint
TOKEN_URL: str = "https://api.hostaway.com/v1/accessTokens"
# aislop-ignore-next-line ai-slop/hardcoded-url -- intentional API base URL
BASE_URL: str = "https://api.hostaway.com"
DEFAULT_TIMEOUT: int = 30
TOKEN_READY_DELAY: float = 1.0
MAX_RETRIES: int = 3
INITIAL_BACKOFF: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
MAX_BACKOFF: float = 30.0
# Hostaway's documented general limit: 200 requests per 10 seconds, counted
# separately against the account id and the originating IP address. Both
# counters apply to every request governed by the general limit. The
# documented endpoint-specific buckets keep their own counters and do not
# draw from this pool, so a request to one of those endpoints is charged to
# its bucket instead of these, never to both. Hostaway corrected these
# published figures on 2026-08-20; earlier documentation showed far lower
# values. See specs/008-proactive-rate-limiting/research.md.
RATE_LIMIT_WINDOW_SECONDS: float = 10.0
RATE_LIMIT_CEILING: int = 200

# The budget the integration actually spends per window. The 20-request gap
# below the ceiling is a deliberate safety margin of ours. 180 is
# not a Hostaway-documented value: it absorbs requests the integration cannot
# see, such as other tooling sharing the same account or public IP.
DEFAULT_RATE_LIMIT_BUDGET: int = 180

# How long a gate stays suppressed after a 429 that carried no usable
# X-RateLimit-Retry-After header. One full documented window is the shortest
# interval after which our own in-window accounting is certainly empty.
DEFAULT_SUPPRESSION_SECONDS: float = 10.0

# Upper bound on suppression derived from a server Retry-After timestamp.
# Hostaway documents no bucket remotely near an hour, so this only ever
# catches a corrupt or wrong-unit header: a value mistakenly sent as a raw
# seconds delay reads as a timestamp decades ahead and would otherwise wedge
# the integration for the life of the process.
MAX_SUPPRESSION_SECONDS: float = 3600.0
DEFAULT_PAGE_LIMIT: int = 100
GRANT_TYPE: str = "client_credentials"
SCOPE: str = "general"
