"""score 阶段编排（M1-4 + M1-6 跨源 URL 去重 + M1-7 AI 语义主题去重）。

score_all(conn, pillars, quota, min_score, now) → ScoreRunResult

行为：
  1. 注入 llm 模块级状态（db conn + tier_map）
  2. 取所有 status=raw topic
  3. **M1-6：URL 合并去重**（同 URL 多源转载 → 只保留代表条参与评分）
  4. **M1-7：AI 语义去重**（不同 URL/不同 title 但同事件 → 只保留代表条）
  5. 逐条 score_topic（解析失败 → rejected；其他异常上抛）
  6. select_daily 转 top N → selected
  7. 返回 processed / selected / rejected / duplicates_merged /
     duplicates_semantic_merged 计数给 CLI 打摘要

M1-6/M1-7 已知限制：merge_by_url + dedup_topics 只在内存里合并；DB 中
重复条目仍占 raw 状态，下次 cron score 会再合并一次（少量 LLM 浪费）。
彻底解决需 schema 加 merged_into_topic_id 字段（动契约，留 TODO）。
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.models import TopicStatus
from pipeline.topics.scorer import score_topic
from pipeline.topics.selector import select_daily
from pipeline.topics.topic_dedup import dedup_topics
from pipeline.topics.url_dedup import merge_by_url


@dataclass(frozen=True)
class ScoreRunResult:
    processed: int
    selected: int
    rejected: int
    # M1-6：URL 合并去重丢弃的条数；duplicate 不参与评分/不参与选 quota
    duplicates_merged: int = 0
    # M1-7：AI 语义去重丢弃的条数（同事件不同 URL/title）；在 URL dedup
    # 之后跑，drop 数独立计数（不与 M1-6 合并）
    duplicates_semantic_merged: int = 0


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
      raw → URL 合并去重（M1-6） → score (representatives only) →
      select (top N) → selected

    Returns:
        ScoreRunResult 不可变统计
    """
    llm_mod.init_db_conn(conn)
    llm_mod.set_tier_map(_DEFAULT_TIER_MAP)

    raw_topics = db.get_topics_by_status(conn, TopicStatus.RAW.value)

    # M1-6：跨源 URL 合并去重
    # 代表条进入 score；duplicate 不参与评分（避免同主题多次占 quota）
    reps, dups = merge_by_url(raw_topics)
    if dups:
        print(
            f"score: M1-6 merged {len(dups)} duplicate(s) by URL "
            f"(representatives={len(reps)})",
            file=sys.stderr,
        )

    # M1-7：AI 语义去重（在 URL dedup 之后、score 之前）
    # 顺序：URL dedup → 语义 dedup → score
    # 失败静默 fallback（best-effort），不影响主流程
    reps, sem_dups = dedup_topics(reps)
    if sem_dups:
        print(
            f"score: M1-7 merged {len(sem_dups)} duplicate(s) by semantic "
            f"(representatives={len(reps)})",
            file=sys.stderr,
        )

    rejected = 0
    for topic in reps:
        result = score_topic(
            conn, topic, pillars=pillars, now=now
        )
        if not result.accepted:
            rejected += 1

    select_result = select_daily(
        conn, quota=quota, min_score=min_score, now=now
    )

    return ScoreRunResult(
        # processed = 实际进入 score 的条数 = representatives (URL+语义 dedup 后)
        # 不用 raw_topics 数（含 dup），避免"processed 看着像做了实际没做"的误会
        processed=len(reps),
        selected=len(select_result.selected),
        rejected=rejected,
        duplicates_merged=len(dups),
        duplicates_semantic_merged=len(sem_dups),
    )