# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Load Home Assistant 2026.11 compatibility before older pytest plugins."""

import importlib
from typing import Any, cast

import httpx2

httpx2.alias_httpx()

logger = cast(Any, importlib.import_module("homeassistant.components.logger"))
logger_helpers = importlib.import_module("homeassistant.components.logger.helpers")

if not hasattr(logger, "_clear_logger_overwrites"):
    # The current test plugin still imports this helper from its old location.
    logger._clear_logger_overwrites = logger_helpers._clear_logger_overwrites
