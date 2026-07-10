"""M10-4 + M10 P2 阶段 A contents router。

GET  /api/v1/contents           内容列表（status/pillar 过滤 + 分页）
GET  /api/v1/contents/{id}      内容详情：canonical.md → HTML + 派生文件 +
                                图卡 URL + 关联 publications
POST /api/v1/contents           阶段 A：selected topic → canonical 长文
                                body: {topic_id: str} → 201 + content_dict
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from pipeline import db
from pipeline.utils.errors import BudgetExceeded
from pipeline.webui import creation_bridge, deps
from pipeline.webui.mdrender import md_to_html
from pipeline.webui.serialize import (
    content_dict,
    content_image_urls,
    list_content_files,
    pub_dict,
)

router = APIRouter(tags=["contents"])


@router.get("/contents")
def list_contents(
    status: Optional[str] = Query(None),
    pillar: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with deps._db() as conn:
        rows = db.list_contents(
            conn, status=status, pillar=pillar, limit=limit, offset=offset,
        )
        total = db.count_contents(conn, status=status, pillar=pillar)
    return {
        "items": [content_dict(c) for c in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/contents/{content_id}")
def get_content_detail(content_id: str) -> dict[str, Any]:
    with deps._db() as conn:
        c = db.get_content(conn, content_id)
        if c is None:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found",
                "message": f"content {content_id} not found",
            }})
        pubs = db.get_publications_by_content(conn, content_id)
    # canonical.md → HTML（文件不存在时返回空串）
    canonical_html = ""
    try:
        cp = Path(c.canonical_path)
        if cp.exists():
            canonical_html = md_to_html(cp.read_text(encoding="utf-8"))
    except Exception:
        canonical_html = ""
    return {
        **content_dict(c),
        "canonical_html": canonical_html,
        "files": list_content_files(c),
        "images": content_image_urls(c),
        "publications": [pub_dict(p) for p in pubs],
    }


@router.post("/contents", status_code=201)
def create_content(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """阶段 A：selected topic → canonical 长文创作。

    body: {"topic_id": "t_xxxx"}
    → 201 + content_dict
    → 400/404/500/503 envelope on error
    """
    topic_id = body.get("topic_id")
    if not topic_id or not isinstance(topic_id, str):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "missing_topic_id",
            "message": "body must contain non-empty 'topic_id' string",
        }})

    # cfg.pillars 必填（create_one 内部留作未来按 pillar 调 prompt）
    cfg, err = deps.get_config()
    if err is not None:
        # config 加载失败 → 500（系统性）
        raise HTTPException(status_code=500, detail={"error": {
            "code": "config_load_failed",
            "message": f"failed to load config: {err}",
        }})
    pillars = list(cfg.pillars) if cfg is not None else []

    with deps._db() as conn:
        try:
            content = creation_bridge.create_for_topic(
                conn, topic_id, pillars=pillars,
            )
        except creation_bridge.TopicNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "topic_not_found", "message": str(e),
            }})
        except creation_bridge.TopicStatusError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "topic_wrong_status", "message": str(e),
            }})
        except BudgetExceeded as e:
            # 系统级 → 503（HARD_PARTS §4 成本护栏）
            raise HTTPException(status_code=503, detail={"error": {
                "code": "budget_exceeded",
                "message": str(e),
            }})
        except Exception as e:
            # 兜底：单条失败 → 500（编排层本应不阻断，但单端点场景下
            # 直接返 500 让前端展示错误信息；与 CREATE 阶段「单条 skip
            # 继续」不同——UI 一次只跑一条，不存在「批次」概念）
            raise HTTPException(status_code=500, detail={"error": {
                "code": "create_failed",
                "message": f"{type(e).__name__}: {e}",
            }})

    return content_dict(content)
