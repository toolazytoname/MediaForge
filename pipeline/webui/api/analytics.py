"""M10-5 analytics router.

GET /api/v1/analytics/weekly — 周报数据（WeeklyReport dataclass 序列化）
GET /api/v1/analytics/cost?group=stage|day — LLM 成本分组
GET /api/v1/analytics/publications/{id}/metrics — 单条 publication 的 metric 序列
GET /api/v1/analytics/platforms — 平台汇总
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from pipeline import db, db_reads
from pipeline.webui import deps
from pipeline.webui.serialize import metric_dict

router = APIRouter(tags=["analytics"])


@router.get("/analytics/weekly")
def analytics_weekly(
    window_days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """周报数据。"""
    with deps._db() as conn:
        report = __import__(
            "pipeline.report.weekly", fromlist=["collect_weekly_report"]
        ).collect_weekly_report(conn, window_days=window_days)
    # WeeklyReport dataclass → dict（手工字段映射，因为 dataclass 嵌套）
    return {
        "window_days": window_days,
        "overview": report.overview,
        "top_by_platform": report.top_by_platform,
        "bottom_by_platform": report.bottom_by_platform,
        "costs": report.costs,
        "gate_histogram": report.gate_histogram,
        "correlation_gate_to_views": report.correlation_gate_to_views,
    }


@router.get("/analytics/cost")
def analytics_cost(
    group: str = Query("stage", pattern="^(stage|day)$"),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """LLM 成本分组。"""
    now = datetime.now(timezone.utc)
    with deps._db() as conn:
        if group == "stage":
            data = db_reads.llm_cost_by_stage(conn)
        else:
            data = db_reads.llm_cost_by_day(conn, days=days, now=now)
    return {"group": group, "items": data}


@router.get("/analytics/publications/{pub_id}/metrics")
def analytics_publication_metrics(pub_id: str) -> dict[str, Any]:
    """一条 publication 的 metric 时间序列（含最新一条）。"""
    with deps._db() as conn:
        series = db_reads.get_metrics_series(conn, pub_id)
    return {
        "publication_id": pub_id,
        "metrics": [metric_dict(m) for m in series],
        "count": len(series),
    }


@router.get("/analytics/platforms")
def analytics_platforms() -> dict[str, Any]:
    """平台汇总：publications 数 + 最新 metric 聚合。"""
    with deps._db() as conn:
        data = db_reads.platform_metric_totals(conn)
    return {"items": data}
