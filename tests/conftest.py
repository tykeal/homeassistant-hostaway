# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Hostaway integration test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx


@pytest.fixture
async def mock_httpx_client() -> AsyncIterator[httpx.AsyncClient]:
    """Provide an httpx.AsyncClient with respx mocking enabled.

    Yields:
        An httpx.AsyncClient suitable for testing API interactions.
    """
    async with respx.mock, httpx.AsyncClient() as client:
        yield client
