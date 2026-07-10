"""M10-2 增量只读查询层：metrics / llm_calls / platform 汇总。

背景：`pipeline/db.py` 已承载 topics/contents/publications 状态表读写与
基础只读助手（count_by_status / sum_llm_cost_this_month）。metrics 与
llm_calls **无状态机**（每条都是不可变快照），但需要的查询维度更多
（按 publication 序列、按 stage/day 分组、JOIN publications 算平台汇总）。
db.py 已经 600+ 行，继续塞会让文件膨胀到难读；本文件专门收口 metrics 与
llm 的「读视角」查询，serialize / api / webui 共用。

设计要点：
  - 全部仅 SELECT，不写库
  - 接受 keyword-only 过滤参数（since_iso / until_iso / now）
  - 行 mapper `row_to_metric` 公开放出（db.py 里私有 _row_to_* 风格
    在本文件放宽——本文件没有对应 `insert_*`，暴露 public helper 即可）
  - Metric dataclass 无 `id` 字段（frozen，TECH_SPEC §4 锁定），
    `get_latest_metric` 返回不带 id 的 Metric；调用方按 publication_id
    + collected_at 去重即可

测试：`tests/test_db_reads.py` 覆盖每个函数（含空表、无匹配、过滤、
排序、now/since 注入）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from pipeline import db
from pipeline.models import Metric


def row_to_metric(row: sqlite3.Row) -> Metric:
    """sqlite3.Row → Metric dataclass。

    Metric 无 `id` 字段（TECH_SPEC §4 锁定 frozen dataclass）；行 id 不映射。
    raw 列原文（JSON 字符串）保留，调用方按需解析。
    """
    return Metric(
        publication_id=row["publication_id"],
        collected_at=row["collected_at"],
        views=row["views"],
        likes=row["likes"],
        comments=row["comments"],
        shares=row["shares"],
        followers_delta=row["followers_delta"],
        raw=row["raw"],
    )


def get_latest_metric(
    conn: sqlite3.Connection, publication_id: str,
) -> Metric | None:
    """返回 publication 最近一条 metric 快照（按 collected_at DESC）。

    无 metric → None。供 webui 发布记录行展示「最近 views/likes」用。
    """
    row = conn.execute(
        "SELECT * FROM metrics WHERE publication_id=? "
        "ORDER BY collected_at DESC, id DESC LIMIT 1",
        (publication_id,),
    ).fetchone()
    return row_to_metric(row) if row else None


def get_metrics_series(
    conn: sqlite3.Connection, publication_id: str,
) -> list[Metric]:
    """返回 publication 全部 metric 快照（按 collected_at ASC）。

    用于折线图（views/comments 趋势）。空 list 若无快照。
    """
    rows = conn.execute(
        "SELECT * FROM metrics WHERE publication_id=? "
        "ORDER BY collected_at ASC, id ASC",
        (publication_id,),
    ).fetchall()
    return [row_to_metric(r) for r in rows]


def llm_cost_by_stage(
    conn: sqlite3.Connection,
    *,
    since_iso: str | None = None,
    until_iso: str | None = None,
) -> list[dict]:
    """按 stage 分组聚合 LLM 成本。

    返回 list[dict]，每项：
        {stage, calls, input_tokens, output_tokens, cost_usd}
    ORDER BY cost_usd DESC。

    since_iso/until_iso 为 ISO8601 UTC 字符串（与 llm_calls.created_at
    同格式）；None → 不限该边界。半开区间 [since_iso, until_iso)。
    """
    clauses = []
    vals: list[Any] = []
    if since_iso is not None:
        clauses.append("created_at >= ?")
        vals.append(since_iso)
    if until_iso is not None:
        clauses.append("created_at < ?")
        vals.append(until_iso)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT stage,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM llm_calls
        {where}
        GROUP BY stage
        ORDER BY cost_usd DESC
        """,
        vals,
    ).fetchall()
    return [
        {
            "stage": r["stage"],
            "calls": int(r["calls"]),
            "input_tokens": int(r["input_tokens"]),
            "output_tokens": int(r["output_tokens"]),
            "cost_usd": float(r["cost_usd"]),
        }
        for r in rows
    ]


def llm_cost_by_day(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    now: datetime | None = None,
) -> list[dict]:
    """按日期（YYYY-MM-DD）分组聚合 LLM 成本。

    返回 list[dict]，每项：
        {date, calls, cost_usd}
    ORDER BY date ASC。

    覆盖窗口：[now - days 天, now]；now 缺省 = `datetime.now(timezone.utc)`。
    `created_at` 是 ISO8601 UTC，用 `substr(created_at, 1, 10)` 切日期段。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # since_iso = now - days 天（半开区间 [now-days, now] 的起点）
    from datetime import timedelta
    since_dt = now - timedelta(days=days)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        """
        SELECT substr(created_at, 1, 10) AS date,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM llm_calls
        WHERE created_at >= ?
        GROUP BY date
        ORDER BY date ASC
        """,
        (since_iso,),
    ).fetchall()
    # 截断到 days 天（防止 GROUP BY 返回更多——理论上 since_iso 已控制）
    return [
        {
            "date": r["date"],
            "calls": int(r["calls"]),
            "cost_usd": float(r["cost_usd"]),
        }
        for r in rows
    ][:days]


def platform_metric_totals(conn: sqlite3.Connection) -> list[dict]:
    """按 platform 汇总 publications + 最新 metric。

    返回 list[dict]，每项：
        {platform, publications, latest_views, latest_likes,
         latest_comments, latest_shares}
    ORDER BY publications DESC。

    只统计 published 状态的 publications（避免 queued/failed 混入）；
    LEFT JOIN 允许某 publication 还没 collect 过 metrics（值 NULL）。
    """
    rows = conn.execute(
        """
        SELECT p.platform,
               COUNT(*) AS publications,
               SUM(COALESCE(m.views, 0)) AS latest_views,
               SUM(COALESCE(m.likes, 0)) AS latest_likes,
               SUM(COALESCE(m.comments, 0)) AS latest_comments,
               SUM(COALESCE(m.shares, 0)) AS latest_shares
        FROM publications p
        LEFT JOIN (
            SELECT m1.publication_id, m1.views, m1.likes,
                   m1.comments, m1.shares
            FROM metrics m1
            INNER JOIN (
                SELECT publication_id,
                       MAX(collected_at) AS max_collected
                FROM metrics
                GROUP BY publication_id
            ) m2
              ON m1.publication_id = m2.publication_id
             AND m1.collected_at = m2.max_collected
        ) m ON m.publication_id = p.id
        WHERE p.status = 'published'
        GROUP BY p.platform
        ORDER BY publications DESC
        """,
    ).fetchall()
    return [
        {
            "platform": r["platform"],
            "publications": int(r["publications"]),
            "latest_views": int(r["latest_views"] or 0),
            "latest_likes": int(r["latest_likes"] or 0),
            "latest_comments": int(r["latest_comments"] or 0),
            "latest_shares": int(r["latest_shares"] or 0),
        }
        for r in rows
    ]


def account_metric_totals(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """M11-D：按 account_id 汇总 publications + 最新 metric。

    返回 list[dict]，每项：
        {platform, account, publications, latest_views, latest_likes,
         latest_comments, latest_shares}
    ORDER BY publications DESC, account ASC。

    只统计 published 状态；LEFT JOIN metrics 拿最新一条。

    可选时间窗过滤：`days=N` 表示只看最近 N 天内 published 的 publication
    （按 published_at 过滤）。`days=None` 即全量。
    """
    where_extra = ""
    params: tuple[Any, ...] = ()
    if days is not None:
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        where_extra = " AND p.published_at >= ?"
        params = (cutoff.isoformat(),)

    rows = conn.execute(
        f"""
        SELECT p.platform, p.account_id,
               COUNT(*) AS publications,
               SUM(COALESCE(m.views, 0)) AS latest_views,
               SUM(COALESCE(m.likes, 0)) AS latest_likes,
               SUM(COALESCE(m.comments, 0)) AS latest_comments,
               SUM(COALESCE(m.shares, 0)) AS latest_shares
        FROM publications p
        LEFT JOIN (
            SELECT m1.publication_id, m1.views, m1.likes,
                   m1.comments, m1.shares
            FROM metrics m1
            INNER JOIN (
                SELECT publication_id,
                       MAX(collected_at) AS max_collected
                FROM metrics
                GROUP BY publication_id
            ) m2
              ON m1.publication_id = m2.publication_id
             AND m1.collected_at = m2.max_collected
        ) m ON m.publication_id = p.id
        WHERE p.status = 'published'{where_extra}
        GROUP BY p.platform, p.account_id
        ORDER BY publications DESC, p.account_id ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "platform": r["platform"],
            "account": r["account_id"],
            "publications": int(r["publications"]),
            "latest_views": int(r["latest_views"] or 0),
            "latest_likes": int(r["latest_likes"] or 0),
            "latest_comments": int(r["latest_comments"] or 0),
            "latest_shares": int(r["latest_shares"] or 0),
        }
        for r in rows
    ]


def content_metric_totals(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """M11-D：按 content_id 汇总 publications + 最新 metric + 内容标题。

    返回 list[dict]，每项：
        {content_id, title, publications, latest_views, latest_likes,
         latest_comments, latest_shares}
    ORDER BY latest_views DESC, content_id ASC。

    JOIN contents 拿 title；LEFT JOIN metrics 拿最新一条；可选 days 时间窗。
    """
    where_extra = ""
    params: tuple[Any, ...] = ()
    if days is not None:
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        where_extra = " AND p.published_at >= ?"
        params = (cutoff.isoformat(),)

    rows = conn.execute(
        f"""
        SELECT p.content_id, c.title,
               COUNT(*) AS publications,
               SUM(COALESCE(m.views, 0)) AS latest_views,
               SUM(COALESCE(m.likes, 0)) AS latest_likes,
               SUM(COALESCE(m.comments, 0)) AS latest_comments,
               SUM(COALESCE(m.shares, 0)) AS latest_shares
        FROM publications p
        LEFT JOIN contents c ON c.id = p.content_id
        LEFT JOIN (
            SELECT m1.publication_id, m1.views, m1.likes,
                   m1.comments, m1.shares
            FROM metrics m1
            INNER JOIN (
                SELECT publication_id,
                       MAX(collected_at) AS max_collected
                FROM metrics
                GROUP BY publication_id
            ) m2
              ON m1.publication_id = m2.publication_id
             AND m1.collected_at = m2.max_collected
        ) m ON m.publication_id = p.id
        WHERE p.status = 'published'{where_extra}
        GROUP BY p.content_id, c.title
        ORDER BY latest_views DESC, p.content_id ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "content_id": r["content_id"],
            "title": r["title"],
            "publications": int(r["publications"]),
            "latest_views": int(r["latest_views"] or 0),
            "latest_likes": int(r["latest_likes"] or 0),
            "latest_comments": int(r["latest_comments"] or 0),
            "latest_shares": int(r["latest_shares"] or 0),
        }
        for r in rows
    ]


__all__ = [
    "row_to_metric",
    "get_latest_metric",
    "get_metrics_series",
    "llm_cost_by_stage",
    "llm_cost_by_day",
    "platform_metric_totals",
    "account_metric_totals",
    "content_metric_totals",
]
