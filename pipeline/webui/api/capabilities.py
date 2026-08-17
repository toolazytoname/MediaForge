"""Read-only CapabilityRegistry for Project UI."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline.publishers.capability_registry import capabilities_payload

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
def list_capabilities() -> dict[str, Any]:
    return {"items": capabilities_payload()}
