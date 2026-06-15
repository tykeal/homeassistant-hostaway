# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Redaction helpers for API-client logging and error messages."""

# aislop-ignore-file ai-slop/hallucinated-import -- HA runtime provides these packages

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_MAX_RESPONSE_BODY_LOG = 500
_REDACTED = "<redacted>"
_SENSITIVE_KEY_TOKENS = (
    "doorcode",
    "password",
    "secret",
    "token",
    "apikey",
    "authorization",
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SENSITIVE_KEY_PATTERN = "|".join(_SENSITIVE_KEY_TOKENS)
_TEXT_REDACT_RE = re.compile(
    r"(?ix)"
    rf"(\"?[A-Za-z0-9_\-]*(?:{_SENSITIVE_KEY_PATTERN})[A-Za-z0-9_\-]*\"?"
    r"\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^,&\s}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+\S+")
_AUTH_403_PHRASES: tuple[str, ...] = (
    "invalid_token",
    "invalid token",
    "expired",
    "token has expired",
    "unauthorized",
    "authentication failed",
    "no scope",
)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a key name may hold secrets."""
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def _redact_sensitive(value: Any) -> Any:
    """Recursively redact sensitive values in JSON-like data."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_sensitive_key(str(key)) else _redact_sensitive(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_plain_text(value)
    return value


def _redact_plain_text(text: str) -> str:
    """Redact bearer tokens and sensitive key/value pairs in text."""
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} {_REDACTED}", text)
    return _TEXT_REDACT_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)


def _sanitize_for_log(text: str) -> str:
    """Escape newlines and strip control characters for log safety."""
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return _CONTROL_CHAR_RE.sub("", text)


def _safe_response_body(
    response: httpx.Response,
    max_len: int = _MAX_RESPONSE_BODY_LOG,
    *,
    redact_plain_text: Any = None,
    redact_sensitive: Any = None,
    sanitize_for_log: Any = None,
) -> str:
    """Return a sanitized, redacted response-body excerpt."""
    if redact_plain_text is None:
        redact_plain_text = _redact_plain_text
    if redact_sensitive is None:
        redact_sensitive = _redact_sensitive
    if sanitize_for_log is None:
        sanitize_for_log = _sanitize_for_log
    try:
        body = response.text
    except Exception:
        return "<unavailable>"
    try:
        try:
            parsed = json.loads(body)
        except ValueError:
            redacted = str(redact_plain_text(body))
        else:
            redacted = json.dumps(redact_sensitive(parsed))
        sanitized = str(sanitize_for_log(redacted))
    except Exception:
        return "<unavailable>"
    return sanitized[:max_len] + "..." if len(sanitized) > max_len else sanitized


def _is_auth_403_body(body: str) -> bool:
    """Return whether a 403 body should be treated as auth-related."""
    if not body or body == "<unavailable>":
        return True
    lowered = body.lower()
    return any(phrase in lowered for phrase in _AUTH_403_PHRASES)
