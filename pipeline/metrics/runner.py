"""collect 编排（M6-1 + HARD_PARTS §5 幂等）。

流程：
1. 查 publications 表中 status=published 且 published_at 距今 ≥ 24h 的记录
2. 按 platform 构造对应 collector
3. 调 collector.collect() → 拿 MetricsSnapshot
4. insert metrics 表（metrics 表允许时间序列多次快照，天然幂等）
5. 单条失败 → log warning + 继续（明日 cron 再试）

**只读自己的数据**（HARD_PARTS §9 决策 5）—— cookie / OAuth token
限定为本人账号。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pipeline import db
from pipeline.metrics.collectors import (
    MetricsCollector,
    MetricsSnapshot,
    build_collector,
)
from pipeline.models import Publication, PublicationStatus
from pipeline.utils.log import get_logger


logger = get_logger(__name__)


# ── 最小 age：published 距今 ≥ 24h 才抓（M6-1 验收） ─


MIN_PUBLISH_AGE_HOURS = 24


@dataclass(frozen=True)
class CollectResult:
    """一次 collect 命令的摘要。"""
    examined: int           # 候选 publications 数
    collected: int          # 成功拿 metrics 数
    failed: int             # collector 失败数（None / 异常）
    skipped: int            # 跳过（platform 无 collector / cookie 缺失 等）


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _select_candidates(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    min_age_hours: int = MIN_PUBLISH_AGE_HOURS,
) -> list[Publication]:
    """取 published 且 published_at 距今 ≥ min_age_hours 的 publications。

    不限定 min_age 上限：cron 每日跑 → 同一 publication 每天产生一条快照。
    """
    cutoff = (now - timedelta(hours=min_age_hours)).isoformat()
    rows = conn.execute(
        "SELECT id, content_id, platform, account_id, scheduled_at, "
        "published_at, platform_post_id, platform_url, error, retry_count, "
        "status, created_at, updated_at "
        "FROM publications WHERE status=? AND published_at IS NOT NULL "
        "AND published_at <= ?",
        (PublicationStatus.PUBLISHED.value, cutoff),
    ).fetchall()
    return [Publication(*row) for row in rows]


def _insert_snapshot(conn: sqlite3.Connection, snap: MetricsSnapshot) -> None:
    conn.execute(
        "INSERT INTO metrics "
        "(publication_id, collected_at, views, likes, comments, shares, "
        "followers_delta, raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snap.publication_id, snap.collected_at,
            snap.views, snap.likes, snap.comments, snap.shares,
            snap.followers_delta, snap.raw,
        ),
    )
    conn.commit()


def run_collect(
    conn: sqlite3.Connection,
    *,
    config,
    now: datetime | None = None,
) -> CollectResult:
    """collect 主入口：候选 → collector → insert metrics。

    单条 publication 失败 → log warning + 继续（不阻断其他条）。
    """
    now = now or datetime.now(timezone.utc)
    candidates = _select_candidates(conn, now=now)
    examined = len(candidates)

    collected = 0
    failed = 0
    skipped = 0

    # 按 platform 分组：避免同一 platform 重复构造 collector
    by_platform: dict[str, MetricsCollector | None] = {}

    for pub in candidates:
        if pub.platform not in by_platform:
            by_platform[pub.platform] = build_collector(
                pub.platform, config=config,
            )
        collector = by_platform[pub.platform]
        if collector is None:
            skipped += 1
            continue
        try:
            snap = collector.collect(pub)
        except Exception as e:
            # 编排层不阻断（metrics 是非关键路径）
            logger.warning(
                f"collect call failed: {e!r}",
                extra={"stage": "collect", "ref_id": pub.id},
            )
            failed += 1
            continue
        if snap is None:
            failed += 1
            continue
        try:
            _insert_snapshot(conn, snap)
            collected += 1
        except Exception as e:
            logger.warning(
                f"insert snapshot failed: {e!r}",
                extra={"stage": "collect", "ref_id": pub.id},
            )
            failed += 1
            continue

    return CollectResult(
        examined=examined,
        collected=collected,
        failed=failed,
        skipped=skipped,
    )


__all__ = [
    "run_collect",
    "CollectResult",
    "MIN_PUBLISH_AGE_HOURS",
]