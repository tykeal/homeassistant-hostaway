# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Proactive pacing of outbound Hostaway API requests.

Hostaway counts requests in a sliding 10-second window against both the
account id and the originating IP address. This module keeps the
integration's own traffic under those counters rather than discovering the
limit by being refused, which is what the retry layer alone would do.

The design is deliberately free of Home Assistant imports so the whole
admission model can be tested with an injected clock and no event-loop
waiting at all. Three ideas carry the weight:

* A **gate** owns one sliding window and answers whether it can admit now.
* A **classifier** maps a request to the symbolic buckets it is charged to,
  which the limiter then resolves to its own gate objects.
* A **pump** is the single place an admission decision is made, so there is
  no window in which two callers can both believe they have capacity.

Admission is a *rate* reservation, not a concurrency slot: there is no
release, because a finished request does not give anything back.
"""

from .buckets import (
    ENDPOINT_BUCKET_CATALOG,
    GENERAL_BUCKETS,
    BucketId,
    EndpointBucketSpec,
    GateScope,
    classify_request,
    register_endpoint_bucket,
    registered_endpoint_buckets,
    unregister_endpoint_bucket,
)
from .context import (
    FIRST_REFRESH_POLICY,
    INTERACTIVE_POLICY,
    SCHEDULED_POLICY,
    RequestContext,
    RequestPriority,
    WaitPolicy,
    current_request_context,
    request_context,
    start_first_refresh_context,
    start_interactive_context,
    start_scheduled_context,
)
from .gates import BudgetGate, SlidingWindowGate
from .limiter import (
    AGING_ADMISSIONS,
    AGING_SECONDS,
    AccountRateLimiter,
    TimerHandle,
    TimerScheduler,
    reset_shared_state,
    shared_ip_gate,
    shared_provider_suppression,
)
from .queueing import GateSnapshot, LimiterSnapshot, LimiterStats, Waiter
from .suppression import ProviderSuppression

__all__ = [
    "AGING_ADMISSIONS",
    "AGING_SECONDS",
    "ENDPOINT_BUCKET_CATALOG",
    "FIRST_REFRESH_POLICY",
    "GENERAL_BUCKETS",
    "INTERACTIVE_POLICY",
    "SCHEDULED_POLICY",
    "AccountRateLimiter",
    "BucketId",
    "BudgetGate",
    "EndpointBucketSpec",
    "GateScope",
    "GateSnapshot",
    "LimiterSnapshot",
    "LimiterStats",
    "ProviderSuppression",
    "RequestContext",
    "RequestPriority",
    "SlidingWindowGate",
    "TimerHandle",
    "TimerScheduler",
    "WaitPolicy",
    "Waiter",
    "classify_request",
    "current_request_context",
    "register_endpoint_bucket",
    "registered_endpoint_buckets",
    "request_context",
    "reset_shared_state",
    "shared_ip_gate",
    "shared_provider_suppression",
    "start_first_refresh_context",
    "start_interactive_context",
    "start_scheduled_context",
    "unregister_endpoint_bucket",
]
