"""M10 P2 阶段 C：写端点桥层（webui → db 状态机）。

薄封装层，不重写业务逻辑，只做：
  - 内容/选题/发布存在性 + 状态校验
  - 调用 db.transition / db.set_gate_verdict / db.reschedule_publication
  - 错误分类 → 上层 API 路由映射到 HTTP 码（404 / 400 / 409 / 503）

设计要点（与 creation_bridge.py / derivative_bridge.py 同构）：
  - 不重写 db.transition / db.set_gate_verdict / db.reschedule_publication
  - 错误映射：
    * TopicNotFoundError / ContentNotFoundError / PublicationNotFoundError → 404
    * TopicWrongStatusError / ContentWrongStatusError / PublicationWrongStatusError
      → 400（明确从 A 状态转 B 状态非法）
    * StatusChangedError → 409（乐观锁失败：行 status 已不是期望值）
    * InvalidDecisionError / InvalidTimeError → 400（请求体非法）
  - 不调 LLM（不引入 anthropic import）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)


# ── Exceptions（API 层映射：404 / 400 / 409）────────────────


class TopicNotFoundError(ValueError):
    """topic_id 不存在 → 404。"""


class TopicWrongStatusError(ValueError):
    """topic 状态不允许该操作 → 400（如 promote raw → selected）。"""


class ContentNotFoundError(ValueError):
    """content_id 不存在 → 404。"""


class ContentWrongStatusError(ValueError):
    """content 状态不允许该操作 → 400。"""


class ContentStatusChangedError(ValueError):
    """content 状态已被另一流程改变（乐观锁）→ 409。

    与 ContentWrongStatusError 区别：
    - WrongStatusError：调用方明确传了一个非法的目标状态
      （如 approve draft → approved 跳过 gated）
    - StatusChangedError：调用方传的是合法目标，但当前行状态已不是
      期望的 from_status（如期望 gated→approved 但已经是 approved）
    """
    current_status: str | None

    def __init__(self, message: str, current_status: str | None = None):
        super().__init__(message)
        self.current_status = current_status


class PublicationNotFoundError(ValueError):
    """publication_id 不存在 → 404。"""


class PublicationWrongStatusError(ValueError):
    """publication 状态不允许该操作 → 400。"""


class PublicationStatusChangedError(ValueError):
    """publication 状态已变（乐观锁）→ 409。"""

    current_status: str | None

    def __init__(self, message: str, current_status: str | None = None):
        super().__init__(message)
        self.current_status = current_status


class InvalidDecisionError(ValueError):
    """review decision 不是 approve/reject → 400。"""


class InvalidTimeError(ValueError):
    """reschedule scheduled_at 不是 ISO8601 字符串 → 400。"""


# ── topics ──────────────────────────────────────────────────


def promote_topic(conn: sqlite3.Connection, topic_id: str) -> Topic:
    """topic SCORED → SELECTED。

    Returns:
        更新后的 Topic（status=selected）。

    Raises:
        TopicNotFoundError: topic_id 不存在。
        TopicWrongStatusError: topic 当前状态非 scored（无法转移）。
    """
    t = db.get_topic(conn, topic_id)
    if t is None:
        raise TopicNotFoundError(f"topic {topic_id} not found")
    if t.status != TopicStatus.SCORED.value:
        raise TopicWrongStatusError(
            f"topic {topic_id} not in scored status "
            f"(current: {t.status})"
        )
    db.transition(
        conn, "topics", topic_id,
        TopicStatus.SCORED.value,
        TopicStatus.SELECTED.value,
    )
    # transition 乐观锁：再读一次拿到最新 updated_at
    refreshed = db.get_topic(conn, topic_id)
    assert refreshed is not None  # 不可能消失
    return refreshed


def reject_topic(conn: sqlite3.Connection, topic_id: str) -> Topic:
    """topic SCORED → REJECTED。"""
    t = db.get_topic(conn, topic_id)
    if t is None:
        raise TopicNotFoundError(f"topic {topic_id} not found")
    if t.status != TopicStatus.SCORED.value:
        raise TopicWrongStatusError(
            f"topic {topic_id} not in scored status "
            f"(current: {t.status})"
        )
    db.transition(
        conn, "topics", topic_id,
        TopicStatus.SCORED.value,
        TopicStatus.REJECTED.value,
    )
    refreshed = db.get_topic(conn, topic_id)
    assert refreshed is not None
    return refreshed


# ── review ──────────────────────────────────────────────────


def decide_review(
    conn: sqlite3.Connection,
    content_id: str,
    decision: str,
    reason: str = "",
) -> Content:
    """人审决策：approve gated→approved / reject gated→rejected_by_human。

    Args:
        conn: SQLite 连接。
        content_id: 内容 id。
        decision: "approve" 或 "reject"。
        reason: reject 时的理由（写入 gate_verdict 字段）。

    Returns:
        更新后的 Content。

    Raises:
        InvalidDecisionError: decision 不是 approve/reject。
        ContentNotFoundError: content_id 不存在。
        ContentStatusChangedError: content 状态已不是 gated（乐观锁失败）。
    """
    if decision not in ("approve", "reject"):
        raise InvalidDecisionError(
            f"decision must be 'approve' or 'reject', got {decision!r}"
        )

    c = db.get_content(conn, content_id)
    if c is None:
        raise ContentNotFoundError(f"content {content_id} not found")

    if decision == "approve":
        try:
            db.transition(
                conn, "contents", content_id,
                ContentStatus.GATED.value,
                ContentStatus.APPROVED.value,
            )
        except Exception as e:
            # StaleState: 当前 status 不是 gated
            current = db.get_content(conn, content_id)
            current_status = current.status if current else None
            raise ContentStatusChangedError(
                f"content {content_id} status changed: "
                f"expected={ContentStatus.GATED.value} "
                f"current={current_status}",
                current_status=current_status,
            ) from e
    else:  # reject
        # reject 流程：先写 gate_verdict（带 expect_status=gated 乐观锁），
        # 再 transition gated→rejected_by_human
        verdict = f"REJECTED_BY_HUMAN: {reason}".strip()
        n = db.set_gate_verdict(
            conn, content_id, verdict,
            expect_status=ContentStatus.GATED.value,
        )
        if n != 1:
            # 行不存在或 status 不匹配
            current = db.get_content(conn, content_id)
            current_status = current.status if current else None
            raise ContentStatusChangedError(
                f"content {content_id} status changed: "
                f"expected={ContentStatus.GATED.value} "
                f"current={current_status}",
                current_status=current_status,
            )
        try:
            db.transition(
                conn, "contents", content_id,
                ContentStatus.GATED.value,
                ContentStatus.REJECTED_BY_HUMAN.value,
            )
        except Exception as e:
            current = db.get_content(conn, content_id)
            current_status = current.status if current else None
            raise ContentStatusChangedError(
                f"content {content_id} status changed during reject transition: "
                f"current={current_status}",
                current_status=current_status,
            ) from e

    refreshed = db.get_content(conn, content_id)
    assert refreshed is not None
    return refreshed


# ── publications ────────────────────────────────────────────


def _parse_iso_utc(s: str) -> str:
    """校验 s 是合法 ISO8601 字符串；返回规范化的 ISO8601（带时区）。

    Raises:
        InvalidTimeError: 解析失败。
    """
    if not isinstance(s, str) or not s.strip():
        raise InvalidTimeError(f"scheduled_at must be non-empty string, got {s!r}")
    raw = s.strip()
    # 接受带或不带 tz：fromisoformat 在 Python 3.11+ 支持末尾 'Z'
    try:
        # 把 'Z' 替换为 '+00:00' 兼容
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError) as e:
        raise InvalidTimeError(
            f"scheduled_at must be ISO8601, got {s!r}: {e}"
        ) from e
    # 始终返回带时区的 ISO8601
    if dt.tzinfo is None:
        # naive → 视为 UTC（与库内其它字段约定一致）
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def reschedule_pub(
    conn: sqlite3.Connection,
    pub_id: str,
    scheduled_at: str,
) -> Publication:
    """queued 改 scheduled_at（不 transition）。

    Args:
        conn: SQLite 连接。
        pub_id: publication id。
        scheduled_at: ISO8601 UTC 字符串。

    Returns:
        更新后的 Publication（status 仍为 queued）。

    Raises:
        InvalidTimeError: scheduled_at 不是 ISO8601。
        PublicationNotFoundError: publication_id 不存在。
        PublicationWrongStatusError: publication 状态不是 queued。
        PublicationStatusChangedError: 乐观锁失败（DB 视角；区别于
            WrongStatusError——此处调用方已读 status=queued 但 UPDATE
            时行 status 已不是 queued，说明并发修改）。
    """
    normalized_iso = _parse_iso_utc(scheduled_at)

    # 先读现状：决定走 WrongStatusError 还是 StatusChangedError
    p = db.get_publication(conn, pub_id)
    if p is None:
        raise PublicationNotFoundError(f"publication {pub_id} not found")
    if p.status != PublicationStatus.QUEUED.value:
        raise PublicationWrongStatusError(
            f"publication {pub_id} not in queued status "
            f"(current: {p.status})"
        )

    n = db.reschedule_publication(
        conn, pub_id, normalized_iso,
        expect_status=PublicationStatus.QUEUED.value,
    )
    if n != 1:
        # 乐观锁失败：并发修改。读最新 status 用于错误信息
        refreshed = db.get_publication(conn, pub_id)
        current = refreshed.status if refreshed else None
        raise PublicationStatusChangedError(
            f"publication {pub_id} status changed: "
            f"expected={PublicationStatus.QUEUED.value} "
            f"current={current}",
            current_status=current,
        )
    refreshed = db.get_publication(conn, pub_id)
    assert refreshed is not None
    return refreshed


def cancel_pub(conn: sqlite3.Connection, pub_id: str) -> Publication:
    """publication QUEUED → CANCELLED。"""
    p = db.get_publication(conn, pub_id)
    if p is None:
        raise PublicationNotFoundError(f"publication {pub_id} not found")
    if p.status != PublicationStatus.QUEUED.value:
        raise PublicationWrongStatusError(
            f"publication {pub_id} not in queued status "
            f"(current: {p.status})"
        )
    try:
        db.transition(
            conn, "publications", pub_id,
            PublicationStatus.QUEUED.value,
            PublicationStatus.CANCELLED.value,
        )
    except Exception as e:
        refreshed = db.get_publication(conn, pub_id)
        current = refreshed.status if refreshed else None
        raise PublicationStatusChangedError(
            f"publication {pub_id} status changed: "
            f"expected={PublicationStatus.QUEUED.value} "
            f"current={current}",
            current_status=current,
        ) from e
    refreshed = db.get_publication(conn, pub_id)
    assert refreshed is not None
    return refreshed


def retry_pub(conn: sqlite3.Connection, pub_id: str) -> Publication:
    """publication FAILED → QUEUED（只改状态，不调真实 publish）。"""
    p = db.get_publication(conn, pub_id)
    if p is None:
        raise PublicationNotFoundError(f"publication {pub_id} not found")
    if p.status != PublicationStatus.FAILED.value:
        raise PublicationWrongStatusError(
            f"publication {pub_id} not in failed status "
            f"(current: {p.status})"
        )
    try:
        db.transition(
            conn, "publications", pub_id,
            PublicationStatus.FAILED.value,
            PublicationStatus.QUEUED.value,
        )
    except Exception as e:
        refreshed = db.get_publication(conn, pub_id)
        current = refreshed.status if refreshed else None
        raise PublicationStatusChangedError(
            f"publication {pub_id} status changed: "
            f"expected={PublicationStatus.FAILED.value} "
            f"current={current}",
            current_status=current,
        ) from e
    refreshed = db.get_publication(conn, pub_id)
    assert refreshed is not None
    return refreshed


__all__ = [
    # topics
    "promote_topic",
    "reject_topic",
    "TopicNotFoundError",
    "TopicWrongStatusError",
    # review
    "decide_review",
    "ContentNotFoundError",
    "ContentWrongStatusError",
    "ContentStatusChangedError",
    "InvalidDecisionError",
    # publications
    "reschedule_pub",
    "cancel_pub",
    "retry_pub",
    "PublicationNotFoundError",
    "PublicationWrongStatusError",
    "PublicationStatusChangedError",
    "InvalidTimeError",
]