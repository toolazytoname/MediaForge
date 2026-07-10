"""M10-4 review router（GET）+ M10 P2 阶段 C 写端点。

GET  /api/v1/review             审核台：gated 状态内容列表（含 canonical.md HTML 预览
                                + 派生文件 + 图卡 URL）
POST /api/v1/review/{content_id}  人审决策：approve gated→approved /
                                reject gated→rejected_by_human（带 reason 写 gate_verdict）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import db
from pipeline.models import ContentStatus
from pipeline.webui import deps, write_action_bridge
from pipeline.webui.mdrender import md_to_html
from pipeline.webui.serialize import (
    content_dict,
    content_image_urls,
    list_content_files,
)

router = APIRouter(tags=["review"])


@router.get("/review")
def list_review_queue() -> dict[str, Any]:
    """审核台：所有 gated 状态内容。"""
    with deps._db() as conn:
        rows = db.get_contents_by_status(conn, ContentStatus.GATED.value)
    items = []
    for c in rows:
        canonical_html = ""
        try:
            cp = Path(c.canonical_path)
            if cp.exists():
                canonical_html = md_to_html(cp.read_text(encoding="utf-8"))
        except Exception:
            pass
        d = content_dict(c)
        d["canonical_html"] = canonical_html
        d["files"] = list_content_files(c)
        d["images"] = content_image_urls(c)
        items.append(d)
    return {"items": items, "total": len(items)}


# ── M10 P2 阶段 C：人审决策写端点 ─────────────────────────


@router.post("/review/{content_id}")
def review_decide_endpoint(
    content_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """人审决策：approve gated→approved / reject gated→rejected_by_human。

    body: {decision: "approve"|"reject", reason?: str}
    → 200 + content_dict
    → 400 invalid_decision (决策非法) /
      404 content_not_found /
      409 status_changed (乐观锁：行 status 已不是 gated)
    """
    decision = body.get("decision")
    reason = body.get("reason", "") or ""

    with deps._db() as conn:
        try:
            c = write_action_bridge.decide_review(
                conn, content_id, decision, reason,
            )
        except write_action_bridge.InvalidDecisionError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "invalid_decision", "message": str(e),
            }})
        except write_action_bridge.ContentNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "content_not_found", "message": str(e),
            }})
        except write_action_bridge.ContentStatusChangedError as e:
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
    return content_dict(c)
