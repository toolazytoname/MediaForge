"""M10-5 publish router（GET）+ M10 P2 阶段 C 写端点 + M10-12 阶段 E dry-run 预演。

GET  /api/v1/publish/calendar?week=YYYY-MM-DD   周视图（复用 bucket_week）
GET  /api/v1/publish/records?status=&platform=&limit=&offset=
                                              列表 + 可选带最新 metric
POST /api/v1/publications/{id}/reschedule    queued 改时间
POST /api/v1/publications/{id}/cancel        queued → cancelled
POST /api/v1/publications/{id}/retry         failed → queued（不调真实 publish）
POST /api/v1/publications/{publication_id}/publish/preview
                                              dry-run 预演（绝不真发）
POST /api/v1/publications/{publication_id}/publish
                                              真实发布（M10 Phase D，需
                                              config.publish.enabled=true +
                                              allowed_platforms 白名单，UI
                                              二次确认后触发，与命令行
                                              `pipeline.run publish` 走同一套
                                              safe_publish 三重锁）
"""
from __future__ import annotations

import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query

from pipeline import db, db_reads
from pipeline.webui import deps, preview_bridge, publish_bridge, write_action_bridge
from pipeline.webui.api.runs import register_run
from pipeline.webui.calendar import bucket_week
from pipeline.webui.serialize import metric_dict, pub_dict

logger = logging.getLogger("mediaforge.webui.preview")

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
    account_id: Optional[str] = Query(None, description="M11-B: 按发布账号过滤"),
    pending_only: bool = Query(False, description="M11-B: 仅未成功发布（published_at IS NULL）"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    with_metric: bool = Query(False, description="每条带最新 metric"),
) -> dict[str, Any]:
    with deps._db() as conn:
        pubs = db.list_publications(
            conn, status=status, platform=platform, account_id=account_id,
            pending_only=pending_only, limit=limit, offset=offset,
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


# ── M10-12 阶段 E：dry-run 预演端点（绝不真发） ─────────────


_PREVIEW_ERROR_CODES = {
    preview_bridge.PublicationNotFoundError: "publication_not_found",
    preview_bridge.PublicationWrongStatusError: "wrong_status",
    preview_bridge.ConfigLoadError: "config_load_error",
    preview_bridge.PlatformNotConfiguredError: "platform_not_configured",
    preview_bridge.AccountNotFoundError: "account_not_found",
    preview_bridge.AdapterInitError: "adapter_init_error",
    preview_bridge.ContentNotFoundError: "content_not_found",
}


def _execute_preview(
    run_id: str,
    publication_id: str,
    started_at: str,
) -> None:
    """后台任务体：调 preview_bridge._run_preview，把结果写进 run registry。

    任何异常都被分类映射到 error_code，但绝不向 publication DB 写状态。
    """
    try:
        with deps._db() as conn:
            result = preview_bridge._run_preview(
                conn, publication_id, run_id, started_at,
            )
        register_run(
            run_id,
            status="succeeded",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
        )
    except preview_bridge.PreviewError as e:
        code = _PREVIEW_ERROR_CODES.get(type(e), "preview_error")
        register_run(
            run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error_code=code,
            error=str(e),
        )
        logger.warning("preview failed run_id=%s code=%s err=%s", run_id, code, e)
    except Exception as e:  # noqa: BLE001 — 不让后台任务静默死掉
        register_run(
            run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error_code="unexpected",
            error=str(e),
        )
        logger.error(
            "preview unexpected run_id=%s err=%s\n%s",
            run_id, e, traceback.format_exc(),
        )


@router.post(
    "/publications/{publication_id}/publish/preview",
    status_code=202,
)
def preview_publication_endpoint(
    publication_id: str,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """dry-run 预演：对一条 queued publication 走真实 validate + safe_publish(dry_run=True)。

    立即返回 202 + run_id；后台通过 FastAPI BackgroundTasks 执行，结果写内存
    run registry。前端轮询 GET /api/v1/runs/{run_id} 拿到结果。

    关键护栏：
      - 永远只调 safe_publish(..., dry_run=True)；
      - 给 safe_publish 的 adapter 是 preview_bridge 内的防真发包装器；
      - 真实 state.db 不会被改动（safe_publish 在内存 DB 副本上跑）；
      - 路径含 /publish/preview，命名上明示「非真发」。
    """
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"run_{uuid.uuid4().hex[:8]}_{int(time.time() * 1000) % 1_000_000}"
    register_run(
        run_id,
        status="queued",
        started_at=now,
        publication_id=publication_id,
    )
    background.add_task(_execute_preview, run_id, publication_id, now)
    return {"run_id": run_id, "status": "queued"}


# ── M10 Phase D：真实发布端点（用户已授权修改 TECH_SPEC §7 契约） ──


_REAL_PUBLISH_ERROR_CODES = {
    publish_bridge.PublicationNotFoundError: "publication_not_found",
    publish_bridge.PublicationWrongStatusError: "wrong_status",
    publish_bridge.ConfigLoadError: "config_load_error",
    publish_bridge.PlatformNotConfiguredError: "platform_not_configured",
    publish_bridge.AccountNotFoundError: "account_not_found",
    publish_bridge.AdapterInitError: "adapter_init_error",
}


def _execute_real_publish(
    run_id: str,
    publication_id: str,
    started_at: str,
) -> None:
    """后台任务体：调 publish_bridge._run_real_publish，把结果写进 run registry。

    真实 conn + 真实 adapter + dry_run=False；state.db 会被 safe_publish
    内部真实改动。任何异常都被分类映射到 error_code。
    """
    try:
        with deps._db() as conn:
            result = publish_bridge._run_real_publish(
                conn, publication_id, run_id, started_at,
            )
        register_run(
            run_id,
            status="succeeded",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
        )
    except publish_bridge.PreviewError as e:
        code = _REAL_PUBLISH_ERROR_CODES.get(type(e), "publish_error")
        register_run(
            run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error_code=code,
            error=str(e),
        )
        logger.warning("real publish failed run_id=%s code=%s err=%s", run_id, code, e)
    except Exception as e:  # noqa: BLE001 — 不让后台任务静默死掉
        register_run(
            run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error_code="unexpected",
            error=str(e),
        )
        logger.error(
            "real publish unexpected run_id=%s err=%s\n%s",
            run_id, e, traceback.format_exc(),
        )


@router.post(
    "/publications/{publication_id}/publish",
    status_code=202,
)
def real_publish_endpoint(
    publication_id: str,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """真实发布：对一条 queued publication 走真实 validate + safe_publish(dry_run=False)。

    立即返回 202 + run_id；后台通过 FastAPI BackgroundTasks 执行，结果写内存
    run registry。前端复用与 /publish/preview 相同的 GET /api/v1/runs/{run_id}
    轮询基础设施。

    关键护栏（与命令行 `pipeline.run publish` 完全一致，UI 只是多一个触发
    入口，不降低任何安全门槛）：
      - 直接调 safe_publish(..., dry_run=False)，真实 conn，真实 adapter；
      - safe_publish 内部仍强制 config.publish.enabled + allowed_platforms
        两道锁 + 乐观锁 + INTENT 日志，本端点不做任何绕过；
      - 前端必须在调用前完成显式二次确认（危险操作，不可撤销）。
    """
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"run_{uuid.uuid4().hex[:8]}_{int(time.time() * 1000) % 1_000_000}"
    register_run(
        run_id,
        status="queued",
        started_at=now,
        publication_id=publication_id,
    )
    background.add_task(_execute_real_publish, run_id, publication_id, now)
    return {"run_id": run_id, "status": "queued"}
