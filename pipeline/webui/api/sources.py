"""M10-4 sources router.

GET /api/v1/sources — config 声明的选题数据源（来自 cfg.sources）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pipeline.webui import deps

router = APIRouter(tags=["sources"])


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    cfg, err = deps.get_config()
    if cfg is None:
        return {"items": [], "config_error": err}
    sources = []
    for s in cfg.sources:
        sources.append({
            "name": s.name,
            "type": s.type,
            "enabled": s.enabled,
        })
    return {"items": sources}
