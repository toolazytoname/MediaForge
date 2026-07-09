"""M10-4 topics router.

GET /api/v1/topics — 选题列表（status/pillar/source 过滤 + 分页）
GET /api/v1/topics/{id} — 单条选题详情（暂未实现详情页，统一返回 dict）
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from pipeline import db
from pipeline.webui import deps
from pipeline.webui.serialize import topic_dict

router = APIRouter(tags=["topics"])


@router.get("/topics")
def list_topics(
    status: Optional[str] = Query(None),
    pillar: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with deps._db() as conn:
        rows = db.list_topics(
            conn, status=status, pillar=pillar, source=source,
            limit=limit, offset=offset,
        )
        total = db.count_topics(conn, status=status, pillar=pillar, source=source)
    return {
        "items": [topic_dict(t) for t in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/topics/{topic_id}")
def get_topic(topic_id: str) -> dict[str, Any]:
    with deps._db() as conn:
        t = db.get_topic(conn, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"error": {
            "code": "topic_not_found", "message": f"topic {topic_id} not found"
        }})
    return topic_dict(t)
