"""Validation for identifiers used as filesystem sidecar path components."""
from __future__ import annotations

import re
from typing import Any


_TAIL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def valid_sidecar_id(value: Any, prefix: str) -> bool:
    """Return whether *value* is one portable, traversal-safe component."""
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and _TAIL.fullmatch(value[len(prefix):]) is not None
    )


__all__ = ["valid_sidecar_id"]
