"""M10-5 publish router（GET）+ M10 P2 阶段 C 写端点。

GET  /api/v1/publish/calendar?week=YYYY-MM-DD   周视图（复用 bucket_week）
GET  /api/v1/publish/records?status=&platform=&limit=&offset=
                                              列表 + 可选带最新 metric
POST /api/v1/publications/{id}/reschedule    queued 改时间
POST /api/v1/publications/{id}/cancel        queued → cancelled
POST /api/v1/publications/{id}/retry         failed → queued（不调真实 publish）
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from pipeline import db, db_reads
from pipeline.webui import deps, write_action_bridge
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


# ── M10 P2 阶段 C：写端点 ─────────────────────────────────


@router.post("/publications/{pub_id}/reschedule")
def reschedule_publication_endpoint(
    pub_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """queued 改 scheduled_at（不 transition，只 update）。

    body: {scheduled_at: ISO8601 str}
    → 200 + pub_dict
    → 400 invalid_time (ISO8601 解析失败) /
      404 publication_not_found /
      400 wrong_status (status 非 queued) /
      409 status_changed (乐观锁失败)
    """
    raw = body.get("scheduled_at")
    with deps._db() as conn:
        try:
            p = write_action_bridge.reschedule_pub(
                conn, pub_id, raw if isinstance(raw, str) else "",
            )
        except write_action_bridge.InvalidTimeError as e:
            raise HTTPException(status_code=400, detail={"error": {
                "code": "invalid_time", "message": str(e),
            }})
        except write_action_bridge.PublicationNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "publication_not_found", "message": str(e),
            }})
        except write_action_bridge.PublicationWrongStatusError as e:
            # 状态不匹配 → 409（前端可区分「非法请求」vs「状态已变」）
            raise HTTPException(status_code=409, detail={"error": {
                "code": "not_queued", "message": str(e),
            }})
        except write_action_bridge.PublicationStatusChangedError as e:
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
    return pub_dict(p)


@router.post("/publications/{pub_id}/cancel")
def cancel_publication_endpoint(pub_id: str) -> dict[str, Any]:
    """queued → cancelled。body: 无。

    → 200 + pub_dict
    → 404 publication_not_found /
      400 wrong_status (status 非 queued) /
      409 status_changed (乐观锁失败)
    """
    with deps._db() as conn:
        try:
            p = write_action_bridge.cancel_pub(conn, pub_id)
        except write_action_bridge.PublicationNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "publication_not_found", "message": str(e),
            }})
        except write_action_bridge.PublicationWrongStatusError as e:
            # 状态不匹配 → 409（与 reschedule 区分：错误码=「状态已变」）
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
        except write_action_bridge.PublicationStatusChangedError as e:
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
    return pub_dict(p)


@router.post("/publications/{pub_id}/retry")
def retry_publication_endpoint(pub_id: str) -> dict[str, Any]:
    """failed → queued（只改状态，不调真实 publish）。

    三重锁天然生效：实际发布由 `pipeline.run publish` 触发，
    publish.enabled=false 时整体阻断。

    → 200 + pub_dict
    → 404 publication_not_found /
      400 wrong_status (status 非 failed) /
      409 status_changed (乐观锁失败)
    """
    with deps._db() as conn:
        try:
            p = write_action_bridge.retry_pub(conn, pub_id)
        except write_action_bridge.PublicationNotFoundError as e:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "publication_not_found", "message": str(e),
            }})
        except write_action_bridge.PublicationWrongStatusError as e:
            # 状态不匹配 → 409
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
        except write_action_bridge.PublicationStatusChangedError as e:
            raise HTTPException(status_code=409, detail={"error": {
                "code": "status_changed", "message": str(e),
            }})
    return pub_dict(p)
