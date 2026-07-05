"""score 阶段编排（M1-4）。

score_all(conn, pillars, quota, min_score, now) → ScoreRunResult

行为：
  1. 注入 llm 模块级状态（db conn + tier_map）
  2. 取所有 status=raw topic
  3. 逐条 score_topic（解析失败 → rejected；其他异常上抛）
  4. select_daily 转 top N → selected
  5. 返回 processed / selected / rejected 计数给 CLI 打摘要
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.models import TopicStatus
from pipeline.topics.scorer import score_topic
from pipeline.topics.selector import select_daily


@dataclass(frozen=True)
class ScoreRunResult:
    processed: int
    selected: int
    rejected: int


# 默认 tier 映射（M1-3 已留口，可由外部 set_tier_map 覆盖）
_DEFAULT_TIER_MAP = {
    "cheap": "claude-haiku-4-5-20251001",
    "creative": "claude-sonnet-5",
    "critical": "claude-sonnet-5",
}


def score_all(
    conn: sqlite3.Connection,
    *,
    pillars: list[Pillar],
    quota: int,
    min_score: float,
    now: str,
) -> ScoreRunResult:
    """执行一次 score 编排。

    处理流程：
      raw → (scored | rejected) → (selected 每日 top N)

    Returns:
        ScoreRunResult 不可变统计
    """
    llm_mod.init_db_conn(conn)
    llm_mod.set_tier_map(_DEFAULT_TIER_MAP)

    raw_topics = db.get_topics_by_status(conn, TopicStatus.RAW.value)

    rejected = 0
    for topic in raw_topics:
        result = score_topic(
            conn, topic, pillars=pillars, now=now
        )
        if not result.accepted:
            rejected += 1

    select_result = select_daily(
        conn, quota=quota, min_score=min_score, now=now
    )

    return ScoreRunResult(
        processed=len(raw_topics),
        selected=len(select_result.selected),
        rejected=rejected,
    )