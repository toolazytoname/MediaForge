"""M10-4 dashboard router.

GET /api/v1/dashboard — 综合驾驶舱数据：
  - 三表 status 计数（topics/contents/publications）
  - 本月 LLM 花费 + 月预算（百分比）
  - 待办（gated 待审 / queued 待发布 / failed 待重试）
  - 近期活动（topics/contents/publications UNION）
  - 门禁分直方图（来自 weekly report）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pipeline import db, db_reads
from pipeline.report import weekly as weekly_report
from pipeline.webui import deps

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard() -> dict[str, Any]:
    """驾驶舱聚合数据。"""
    cfg, err = deps.get_config()
    with deps._db() as conn:
        counts = {
            "topics": db.count_by_status(conn, "topics"),
            "contents": db.count_by_status(conn, "contents"),
            "publications": db.count_by_status(conn, "publications"),
        }
        used_usd = db.sum_llm_cost_this_month(conn)
        # 待办：gated 待审 + queued 待发布 + failed 待重试
        todos = {
            "to_review": counts["contents"].get("gated", 0),
            "to_publish": counts["publications"].get("queued", 0),
            "publish_failed": counts["publications"].get("failed", 0),
        }
        activity = db.recent_activity(conn, limit=20)
        # 门禁直方图（复用 weekly report）
        report = weekly_report.collect_weekly_report(conn)
        histogram = report.gate_histogram
        correlation = report.correlation_gate_to_views

    # 预算
    monthly_usd = 0.0
    if cfg is not None:
        try:
            monthly_usd = float(cfg.budget.monthly_usd)
        except Exception:
            monthly_usd = 0.0
    used_ratio = (used_usd / monthly_usd) if monthly_usd > 0 else 0.0

    return {
        "counts": counts,
        "todos": todos,
        "budget": {
            "monthly_usd": monthly_usd,
            "used_usd": round(used_usd, 4),
            "used_ratio": round(used_ratio, 4),
        },
        "activity": activity,
        "gate_histogram": histogram,
        "gate_correlation": correlation,
        "config_error": err,
    }
