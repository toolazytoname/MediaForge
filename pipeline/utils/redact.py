"""Recursive secret redaction for persisted JSON (receipts, request_json)."""
from __future__ import annotations

from typing import Any

SECRET_MARKERS = (
    "token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "app_secret",
    "access_token",
    "api_key",
    "apikey",
    "pexels",
)

REDACTED = "[redacted]"


def is_secret_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def redact_value(value: Any) -> Any:
    """Walk dict/list trees and replace secret-looking keys at any depth."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(key):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact_value(item)
        return out
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value


def token_last4(token: str | None) -> str | None:
    if not token:
        return None
    text = str(token)
    return text[-4:] if len(text) >= 4 else text


__all__ = [
    "REDACTED",
    "SECRET_MARKERS",
    "is_secret_key",
    "redact_value",
    "token_last4",
]
