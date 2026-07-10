"""M10-4 topics router（只读 GET）+ M10 P2 阶段 C 写端点。

GET  /api/v1/topics                       选题列表（status/pillar/source 过滤 + 分页）
GET  /api/v1/topics/{id}                  单条选题详情
POST /api/v1/topics/{id}/promote          scored → selected
POST /api/v1/topics/{id}/reject           scored → rejected
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from pipeline import db
from pipeline.webui import deps, write_action_bridge
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


# ── M10 P2 阶段 C：写端点 ─────────────────────────────────


@router.post("/topics/{topic_id}/promote")
def promote_topic_endpoint(topic_id: str) -> dict[str, Any]:
    """scored → selected。

    → 200 + topic_dict
    → 404 topic_not_found / 400 topic_wrong_status
    """
    with deps._db() as conn:
        try:
            t = write_action_bridge.promote_topic(conn, topic_id)
        except write_action_bridge.TopicNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "topic_not_found", "message": str(e),
            }})
        except write_action_bridge.TopicWrongStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "topic_wrong_status", "message": str(e),
            }})
    return topic_dict(t)


@router.post("/topics/{topic_id}/reject")
def reject_topic_endpoint(topic_id: str) -> dict[str, Any]:
    """scored → rejected。

    → 200 + topic_dict
    → 404 / 400
    """
    with deps._db() as conn:
        try:
            t = write_action_bridge.reject_topic(conn, topic_id)
        except write_action_bridge.TopicNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "topic_not_found", "message": str(e),
            }})
        except write_action_bridge.TopicWrongStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "topic_wrong_status", "message": str(e),
            }})
    return topic_dict(t)
