"""try_insert_topic 单元测试（TECH_SPEC §3 content_hash UNIQUE + HARD_PARTS §5 幂等）。

行为契约：
  - 新 (title, domain) → 插入新行，返回 (Topic, True)
  - 同 content_hash → 不插入，返回 (existing Topic, False)
  - 同一 source 内重复 → 第二条判 dup
  - 不同 source 的同内容 → 第二条仍判 dup（hash 全局唯一）
"""
from __future__ import annotations

import sqlite3

import pytest

from pipeline import db
from pipeline.models import Topic, TopicStatus
from pipeline.sources.base import RawItem
from pipeline.sources.dedup import content_hash
from pipeline.utils.errors import IllegalTransition


# ── helpers ──────────────────────────────────────────────

def _raw(title: str, url: str | None = None) -> RawItem:
    return RawItem(
        title=title,
        url=url,
        summary=None,
        published_at=None,
    )


def _open_db(tmp_path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    return conn


# ── tests ────────────────────────────────────────────────

def test_inserts_new_topic(tmp_path) -> None:
    """新条目：插入并返回 is_new=True。"""
    conn = _open_db(tmp_path)
    raw = _raw("Hello World", "https://example.com/x")

    topic, is_new = db.try_insert_topic(conn, raw, source="rss:a", now="2026-07-05T00:00:00+00:00")

    assert is_new is True
    assert topic.title == "Hello World"
    assert topic.source == "rss:a"
    assert topic.status == TopicStatus.RAW.value
    assert topic.content_hash == content_hash("Hello World", "https://example.com/x")
    assert topic.created_at == "2026-07-05T00:00:00+00:00"
    assert topic.id.startswith("t_") and len(topic.id) == 10


def test_duplicate_returns_existing_and_is_new_false(tmp_path) -> None:
    """同 content_hash 第二次插入：返回已有 Topic，is_new=False。"""
    conn = _open_db(tmp_path)
    raw = _raw("Hello World", "https://example.com/x")

    first, is_new1 = db.try_insert_topic(conn, raw, "rss:a", "2026-07-05T00:00:00+00:00")
    second, is_new2 = db.try_insert_topic(conn, raw, "rss:b", "2026-07-05T00:01:00+00:00")

    assert is_new1 is True
    assert is_new2 is False
    assert first.id == second.id  # 同一条记录
    # 第二条不被覆盖 source（保留首次入库的 source）
    assert second.source == "rss:a"


def test_normalized_duplicate_detected(tmp_path) -> None:
    """大小写/标点不同的同标题判重。"""
    conn = _open_db(tmp_path)
    raw1 = _raw("Hello, World!", "https://example.com/x")
    raw2 = _raw("  hello world  ", "https://example.com/x")

    _, is_new1 = db.try_insert_topic(conn, raw1, "rss:a", "2026-07-05T00:00:00+00:00")
    _, is_new2 = db.try_insert_topic(conn, raw2, "rss:b", "2026-07-05T00:01:00+00:00")

    assert is_new1 is True
    assert is_new2 is False


def test_different_domain_not_deduped(tmp_path) -> None:
    """同标题不同域名 → 不同 hash → 两条都入库。"""
    conn = _open_db(tmp_path)
    raw1 = _raw("Same Title", "https://example.com/x")
    raw2 = _raw("Same Title", "https://other.com/x")

    _, is_new1 = db.try_insert_topic(conn, raw1, "rss:a", "2026-07-05T00:00:00+00:00")
    _, is_new2 = db.try_insert_topic(conn, raw2, "rss:b", "2026-07-05T00:01:00+00:00")

    assert is_new1 is True
    assert is_new2 is True


def test_summary_truncated_to_2000_chars(tmp_path) -> None:
    """summary 入库前截断至 2000 字符（与 topics 表 schema 对齐）。"""
    conn = _open_db(tmp_path)
    raw = RawItem(
        title="t", url=None, summary="x" * 5000, published_at=None
    )

    topic, _ = db.try_insert_topic(conn, raw, "rss:a", "2026-07-05T00:00:00+00:00")

    assert topic.summary is not None
    assert len(topic.summary) == 2000


def test_insert_topic_original_still_raises_on_dup(tmp_path) -> None:
    """既有 insert_topic 保持 IntegrityError 行为不变（向后兼容 M0-2 测试）。"""
    conn = _open_db(tmp_path)
    raw = _raw("Dup Test", "https://example.com/x")

    from pipeline.utils.ids import new_id
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    t1 = Topic(
        id=new_id("t"),
        source="rss:a",
        title="Dup Test",
        url="https://example.com/x",
        summary=None,
        content_hash=content_hash("Dup Test", "https://example.com/x"),
        pillar=None, score=None, score_reason=None,
        status="raw",
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t1)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_topic(conn, t1)