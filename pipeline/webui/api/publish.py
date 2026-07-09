"""M10-5 publish router.

GET /api/v1/publish/calendar?week=YYYY-MM-DD — 周视图（复用 bucket_week）
GET /api/v1/publish/records?status=&platform=&limit=&offset= — 列表 +
  可选带最新 metric
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query

from pipeline import db, db_reads
from pipeline.webui import deps
from pipeline.webui.calendar import bucket_week
from pipeline.webui.serialize import metric_dict, pub_dict

router = APIRouter(tags=["publish"])


@router.get("/publish/calendar")
def publish_calendar(
    week: Optional[str] = Query(None),
) -> dict[str, Any]:
    """周视图日历。"""
    with deps._db() as conn:
        rows = []
        for st in __import__("pipeline.models", fromlist=["PublicationStatus"]).PublicationStatus:
            rows.extend(db.get_publications_by_status(conn, st.value))
    bucket = bucket_week(rows, anchor_iso=week)
    # bucket 是 WeekBucket（by_day: dict[date, list[Publication]]）
    days = sorted(bucket.by_day.keys())
    return {
        "week_start": bucket.week_start.isoformat(),
        "week_end": bucket.week_end.isoformat(),
        "this_week": bucket.this_week,
        "prev_week": bucket.prev_week,
        "next_week": bucket.next_week,
        "days": [
            {
                "date": d.isoformat(),
                "publications": [pub_dict(p) for p in bucket.by_day.get(d, [])],
            }
            for d in days
        ],
    }


@router.get("/publish/records")
def publish_records(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    with_metric: bool = Query(False, description="每条带最新 metric"),
) -> dict[str, Any]:
    with deps._db() as conn:
        pubs = db.list_publications(
            conn, status=status, platform=platform, limit=limit, offset=offset,
        )
        items = []
        for p in pubs:
            d = pub_dict(p)
            if with_metric:
                m = db_reads.get_latest_metric(conn, p.id)
                d["latest_metric"] = metric_dict(m) if m else None
            items.append(d)
    return {"items": items, "limit": limit, "offset": offset}
