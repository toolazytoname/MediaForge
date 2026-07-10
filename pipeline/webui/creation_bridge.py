"""M10 P2 阶段 A: 图文创作桥（webui → creators.canonical）。

为一条 selected topic 触发 canonical 长文创作。这是 M10 P1 之后
的第一个写端点，遵循「不重写业务逻辑，只做接缝」的薄封装原则。

设计要点：
  - 不重写 create_one 业务逻辑（TECH_SPEC §5.3 + M2-1）；
    只做「读 topic → 校验状态 → 注入 config.pillars → 调 create_one」
  - 错误处理分三类，前端按 HTTP 码分流：
    * topic 不存在 → ValueError("topic not found") → 404
    * 状态非 selected → ValueError("topic not in selected status") → 400
    * BudgetExceeded → 原样上抛 → 503（HARD_PARTS §4 成本护栏）
    * CreateError 等其他异常 → 原样上抛 → 500
  - now 参数显式注入（单测可控，不调 datetime.now）；
    缺省 = db.now_utc()（与既有调用方一致）
  - 走 db 层（db.get_topic / db.transition 内部用），不裸 SQL
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import db
from pipeline.creators.canonical import create_one
from pipeline.models import Content, Topic, TopicStatus


class TopicNotFoundError(ValueError):
    """选题 id 不存在 → 404。前端按 error code 区分。"""


class TopicStatusError(ValueError):
    """选题状态非 selected → 400。"""


def create_for_topic(
    conn: sqlite3.Connection,
    topic_id: str,
    *,
    now: str | None = None,
    pillars: list[Any] | None = None,
    output_root: str | Path = "output",
) -> Content:
    """为一条 selected topic 调 canonical.create_one。

    Args:
        conn: SQLite 连接（由调用方管理生命周期）。
        topic_id: 选题 id（'t_' 前缀）。
        now: ISO8601 UTC 字符串。缺省 = db.now_utc()。测试可注入固定值。
        pillars: 内容支柱列表。必填——M2-1 create_one 需要（score 阶段
            也用，但 create 阶段目前留作「未来按 pillar 调 prompt 模板」的备用）。
            若 None → 抛 ValueError（前端调用方须传 config.pillars）。
        output_root: 输出根目录。默认 "output"。

    Returns:
        Content 已落库的不可变记录（status=draft）。

    Raises:
        TopicNotFoundError: topic_id 不存在。
        TopicStatusError: topic 状态非 'selected'。
        BudgetExceeded: LLM 预算超限（系统级，原样上抛）。
        CreateError: LLM/写盘失败。
    """
    if pillars is None:
        raise ValueError("pillars is required (load from config.pillars)")

    if now is None:
        now = db.now_utc()

    # 1. 读 topic
    topic: Topic | None = db.get_topic(conn, topic_id)
    if topic is None:
        raise TopicNotFoundError(f"topic {topic_id} not found")

    # 2. 状态校验（不在 create_one 内部校验——前端可读 msg 区分 400 vs 500）
    if topic.status != TopicStatus.SELECTED.value:
        raise TopicStatusError(
            f"topic {topic_id} not in selected status "
            f"(current: {topic.status})"
        )

    # 3. 调 create_one（BudgetExceeded / CreateError 原样上抛，不吞）
    content = create_one(
        conn, topic, pillars=pillars,
        output_root=output_root, now=now,
    )
    return content


__all__ = [
    "create_for_topic",
    "TopicNotFoundError",
    "TopicStatusError",
]
