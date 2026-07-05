"""每日精选（M1-4）。

select_daily(scored_topics + quota + min_score) → top N 转 selected。
余下 scored 保留到明日（M1-4 范围内不做过期→rejected，留给后续任务）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pipeline import db
from pipeline.models import Topic, TopicStatus


@dataclass(frozen=True)
class SelectResult:
    """一次 select_daily 的结果。"""
    selected: tuple[Topic, ...]   # 新转 selected 的 topic
    kept_scored: int             # 仍保持 scored 的条数


def select_daily(
    conn: sqlite3.Connection,
    *,
    quota: int,
    min_score: float,
    now: str,
) -> SelectResult:
    """从 scored 中按 score desc 取 score ≥ min_score 的前 quota 个 → selected。

    幂等：二次运行时已 selected 的不在 scored 范围，结果为空。
    """
    scored = db.get_topics_by_status(conn, TopicStatus.SCORED.value)
    candidates = sorted(
        (t for t in scored if t.score is not None and t.score >= min_score),
        key=lambda t: t.score,  # type: ignore[arg-type,return-value]
        reverse=True,
    )[:quota]

    selected: list[Topic] = []
    for t in candidates:
        try:
            db.transition(
                conn, "topics", t.id,
                from_status=TopicStatus.SCORED.value,
                to_status=TopicStatus.SELECTED.value,
            )
            selected.append(t)
        except Exception:
            # 并发情况下已被其他进程转走——跳过（幂等）
            continue

    return SelectResult(
        selected=tuple(selected),
        kept_scored=len(scored) - len(selected),
    )