# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Hostaway integration test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx2 as httpx
import pytest
import respx

from custom_components.hostaway.api.rate_limit import reset_shared_state


@pytest.fixture(autouse=True)
def reset_rate_limit_state() -> Iterator[None]:
    """Keep each test out of the previous test's rate-limit window.

    The IP gate and the provider suppression are process-wide by design,
    which is right in production and poison between tests.

    Yields:
        None.
    """
    reset_shared_state()
    yield
    reset_shared_state()


@pytest.fixture
async def mock_httpx_client() -> AsyncIterator[httpx.AsyncClient]:
    """Provide an httpx.AsyncClient with respx mocking enabled.

    Yields:
        An httpx.AsyncClient suitable for testing API interactions.
    """
    async with respx.mock, httpx.AsyncClient() as client:
        yield client
