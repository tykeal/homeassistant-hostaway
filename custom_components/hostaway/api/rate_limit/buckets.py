# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Which Hostaway counter a given request is charged against.

Classification is pure and returns symbolic bucket names. Resolving a symbol
to a live gate is the limiter's job: the account gate belongs to one limiter
while the IP gate is shared process-wide, so a classifier that returned gate
objects would need hidden global state to do it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class GateScope(StrEnum):
    """What a gate's counter is keyed on."""

    ACCOUNT = "account"
    IP = "ip"
    ENDPOINT = "endpoint"


class BucketId(StrEnum):
    """Symbolic name of a Hostaway counter.

    Classification returns these rather than gate objects. The
    account-general gate belongs to one limiter while the IP gate is shared
    process-wide, so only a limiter can resolve a symbol to the right
    instance; doing it in the classifier would need hidden global state.
    """

    ACCOUNT_GENERAL = "account_general"
    IP_GENERAL = "ip_general"
    CONVERSATION_MESSAGES = "conversation_messages"
    PRICE_DETAILS = "price_details"
    CREATE_RESERVATION = "create_reservation"


GENERAL_BUCKETS: tuple[BucketId, ...] = (
    BucketId.ACCOUNT_GENERAL,
    BucketId.IP_GENERAL,
)


@dataclass(frozen=True, slots=True)
class EndpointBucketSpec:
    """A documented endpoint-specific counter.

    These counters keep their own tallies and do not draw from the general
    pool, so a request matching one is charged to it *instead of* the
    account and IP gates.
    """

    bucket: BucketId
    method: str
    path_pattern: str
    window_seconds: float
    max_budget: int

    def matches(self, method: str, path: str) -> bool:
        """Report whether a request belongs to this bucket.

        Args:
            method: HTTP method, case-insensitive.
            path: Request path.

        Returns:
            True when both the method and the path pattern match.
        """
        if method.upper() != self.method.upper():
            return False
        return re.fullmatch(_pattern_to_regex(self.path_pattern), path) is not None


def _pattern_to_regex(pattern: str) -> str:
    """Translate a ``{placeholder}`` path pattern into a regex.

    Args:
        pattern: Path pattern such as ``/v1/listings/{id}/calendar``.

    Returns:
        An anchored regular expression source string.
    """
    parts = re.split(r"(\{[^/}]*\})", pattern)
    return "".join(
        "[^/]+" if part.startswith("{") and part.endswith("}") else re.escape(part)
        for part in parts
    )


#: The endpoint-specific counters Hostaway documents. They are recorded here
#: so the shape is known, but none is registered: the integration calls none
#: of these endpoints today. Activating one is a single
#: ``register_endpoint_bucket`` call and needs no change to any coordinator,
#: service, or public API method.
ENDPOINT_BUCKET_CATALOG: tuple[EndpointBucketSpec, ...] = (
    EndpointBucketSpec(
        bucket=BucketId.CONVERSATION_MESSAGES,
        method="POST",
        path_pattern="/v1/conversations/{id}/messages",
        window_seconds=60.0,
        max_budget=30,
    ),
    EndpointBucketSpec(
        bucket=BucketId.PRICE_DETAILS,
        method="POST",
        path_pattern="/v1/listings/{id}/calendar/priceDetails",
        window_seconds=10.0,
        max_budget=400,
    ),
    EndpointBucketSpec(
        bucket=BucketId.CREATE_RESERVATION,
        method="POST",
        path_pattern="/v1/reservations",
        window_seconds=10.0,
        max_budget=200,
    ),
)

_ENDPOINT_REGISTRY: dict[BucketId, EndpointBucketSpec] = {}


def register_endpoint_bucket(spec: EndpointBucketSpec) -> None:
    """Activate an endpoint-specific counter.

    Args:
        spec: The bucket to start classifying against.
    """
    _ENDPOINT_REGISTRY[spec.bucket] = spec


def unregister_endpoint_bucket(bucket: BucketId) -> None:
    """Deactivate an endpoint-specific counter.

    Args:
        bucket: The bucket to stop classifying against.
    """
    _ENDPOINT_REGISTRY.pop(bucket, None)


def registered_endpoint_buckets() -> dict[BucketId, EndpointBucketSpec]:
    """Return a copy of the active endpoint bucket registry.

    Returns:
        Mapping of bucket id to its specification.
    """
    return dict(_ENDPOINT_REGISTRY)


def classify_request(method: str, path: str) -> tuple[BucketId, ...]:
    """Map a request to the counters it is charged against.

    Pure and stateless. A request matching a registered endpoint bucket is
    charged to that bucket alone; everything else is charged to both general
    counters.

    Args:
        method: HTTP method.
        path: Request path.

    Returns:
        The symbolic buckets this request consumes.
    """
    for spec in _ENDPOINT_REGISTRY.values():
        if spec.matches(method, path):
            return (spec.bucket,)
    return GENERAL_BUCKETS
