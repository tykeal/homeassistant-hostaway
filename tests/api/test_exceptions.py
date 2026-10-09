# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Hostaway API exception hierarchy."""

from __future__ import annotations

import pytest

from custom_components.hostaway.api.exceptions import (
    HostawayApiError,
    HostawayAuthError,
    HostawayConnectionError,
    HostawayRateLimitError,
    HostawayRateLimitShedError,
    HostawayRateLimitWaitTimeout,
    HostawayReservationLockedError,
    HostawayResponseError,
)


class TestHostawayApiError:
    """Tests for the HostawayApiError base exception."""

    def test_is_exception(self) -> None:
        """HostawayApiError inherits from Exception."""
        assert issubclass(HostawayApiError, Exception)

    def test_message_attribute(self) -> None:
        """HostawayApiError stores the message attribute."""
        error = HostawayApiError("something failed")
        assert error.message == "something failed"

    def test_str_representation(self) -> None:
        """HostawayApiError string representation is the message."""
        error = HostawayApiError("test error")
        assert str(error) == "test error"

    def test_can_be_raised_and_caught(self) -> None:
        """HostawayApiError can be raised and caught."""
        with pytest.raises(HostawayApiError, match="boom"):
            raise HostawayApiError("boom")


class TestHostawayAuthError:
    """Tests for the HostawayAuthError exception."""

    def test_inherits_from_base(self) -> None:
        """HostawayAuthError is a subclass of HostawayApiError."""
        assert issubclass(HostawayAuthError, HostawayApiError)

    def test_message_attribute(self) -> None:
        """HostawayAuthError stores the message attribute."""
        error = HostawayAuthError("invalid credentials")
        assert error.message == "invalid credentials"

    def test_caught_as_base(self) -> None:
        """HostawayAuthError can be caught as HostawayApiError."""
        with pytest.raises(HostawayApiError):
            raise HostawayAuthError("auth failed")


class TestHostawayReservationLockedError:
    """Tests for the HostawayReservationLockedError exception."""

    def test_inherits_from_base(self) -> None:
        """HostawayReservationLockedError is a subclass of HostawayApiError."""
        assert issubclass(HostawayReservationLockedError, HostawayApiError)

    def test_not_an_auth_error(self) -> None:
        """HostawayReservationLockedError is NOT a HostawayAuthError."""
        assert not issubclass(HostawayReservationLockedError, HostawayAuthError)

    def test_message_attribute(self) -> None:
        """HostawayReservationLockedError stores the message attribute."""
        error = HostawayReservationLockedError("reservation locked")
        assert error.message == "reservation locked"

    def test_caught_as_base(self) -> None:
        """HostawayReservationLockedError can be caught as HostawayApiError."""
        with pytest.raises(HostawayApiError):
            raise HostawayReservationLockedError("locked")


class TestHostawayRateLimitError:
    """Tests for the HostawayRateLimitError exception."""

    def test_inherits_from_base(self) -> None:
        """HostawayRateLimitError is a subclass of HostawayApiError."""
        assert issubclass(HostawayRateLimitError, HostawayApiError)

    def test_retry_after_attribute(self) -> None:
        """HostawayRateLimitError stores retry_after."""
        error = HostawayRateLimitError("rate limited", retry_after=60.0)
        assert error.retry_after == 60.0

    def test_defaults_to_none(self) -> None:
        """HostawayRateLimitError defaults retry_after to None."""
        error = HostawayRateLimitError("rate limited")
        assert error.retry_after is None

    def test_message_attribute(self) -> None:
        """HostawayRateLimitError stores message from base."""
        error = HostawayRateLimitError("too many requests")
        assert error.message == "too many requests"

    def test_caught_as_base(self) -> None:
        """HostawayRateLimitError can be caught as HostawayApiError."""
        with pytest.raises(HostawayApiError):
            raise HostawayRateLimitError("rate limited", retry_after=10.0)


class TestHostawayConnectionError:
    """Tests for the HostawayConnectionError exception."""

    def test_inherits_from_base(self) -> None:
        """HostawayConnectionError is a subclass of HostawayApiError."""
        assert issubclass(HostawayConnectionError, HostawayApiError)

    def test_message_attribute(self) -> None:
        """HostawayConnectionError stores the message attribute."""
        error = HostawayConnectionError("connection refused")
        assert error.message == "connection refused"

    def test_caught_as_base(self) -> None:
        """HostawayConnectionError can be caught as HostawayApiError."""
        with pytest.raises(HostawayApiError):
            raise HostawayConnectionError("timeout")


class TestHostawayResponseError:
    """Tests for the HostawayResponseError exception."""

    def test_inherits_from_base(self) -> None:
        """HostawayResponseError is a subclass of HostawayApiError."""
        assert issubclass(HostawayResponseError, HostawayApiError)

    def test_message_attribute(self) -> None:
        """HostawayResponseError stores the message attribute."""
        error = HostawayResponseError("unexpected format")
        assert error.message == "unexpected format"

    def test_caught_as_base(self) -> None:
        """HostawayResponseError can be caught as HostawayApiError."""
        with pytest.raises(HostawayApiError):
            raise HostawayResponseError("invalid json")


class TestHostawayRateLimitShedError:
    """Tests for the coordinator-shed signal."""

    def test_is_not_an_api_error(self) -> None:
        """Structural guard for SC-005 and SC-015 — never relax this.

        All three coordinators convert HostawayApiError into UpdateFailed,
        which marks the entity unavailable. Shedding a scheduled refresh is
        a deliberate, healthy outcome that must keep the last good data, so
        this signal must stay outside that hierarchy.
        """
        assert not issubclass(HostawayRateLimitShedError, HostawayApiError)

    def test_carries_the_time_waited(self) -> None:
        """Diagnostics and logs report how long the caller queued."""
        error = HostawayRateLimitShedError("shed refresh", waited=1.5)
        assert error.waited == 1.5

    def test_is_catchable_on_its_own(self) -> None:
        """Coordinators catch it explicitly, before any broad handler."""
        with pytest.raises(HostawayRateLimitShedError):
            raise HostawayRateLimitShedError("shed refresh", waited=0.0)


class TestHostawayRateLimitWaitTimeout:
    """Tests for the local queue-timeout signal."""

    def test_inherits_from_rate_limit_error(self) -> None:
        """Structural guard for SC-005 and SC-015 — never relax this.

        A local wait timeout is a rate-limit outcome and must surface as a
        genuine failure, unlike a shed refresh.
        """
        assert issubclass(HostawayRateLimitWaitTimeout, HostawayRateLimitError)

    def test_is_an_api_error(self) -> None:
        """It must reach the coordinators' UpdateFailed conversion."""
        assert issubclass(HostawayRateLimitWaitTimeout, HostawayApiError)

    def test_carries_the_time_waited(self) -> None:
        """The deadline that expired is reported to the caller."""
        error = HostawayRateLimitWaitTimeout("deadline expired", waited=30.0)
        assert error.waited == 30.0

    def test_distinguishable_from_a_server_429(self) -> None:
        """A local timeout is not a server refusal.

        services/custom_fields.py catches HostawayRateLimitError and
        replays the whole read/merge/write. Replaying on a local timeout
        would be wrong, so callers must be able to tell the two apart.
        """
        server = HostawayRateLimitError("429 from Hostaway", retry_after=5.0)
        local = HostawayRateLimitWaitTimeout("deadline expired", waited=30.0)
        assert not isinstance(server, HostawayRateLimitWaitTimeout)
        assert isinstance(local, HostawayRateLimitError)
