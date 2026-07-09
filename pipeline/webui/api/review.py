"""M10-4 review router.

GET /api/v1/review — 审核台：gated 状态内容列表（含 canonical.md HTML 预览
  + 派生文件 + 图卡 URL）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from pipeline import db
from pipeline.models import ContentStatus
from pipeline.webui import deps
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
