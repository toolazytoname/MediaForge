"""M10-4 contents router.

GET /api/v1/contents — 内容列表（status/pillar 过滤 + 分页）
GET /api/v1/contents/{id} — 内容详情：canonical.md → HTML + 派生文件 +
  图卡 URL + 关联 publications
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from pipeline import db
from pipeline.webui import deps
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
