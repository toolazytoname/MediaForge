"""M7 R7-3 db 层测试：把 webui 裸 SQL 抽到 db.py 助手函数。

覆盖：
  - set_gate_verdict(conn, content_id, verdict, *, expect_status) -> int
      状态匹配（gated）→ rowcount=1 + gate_verdict 真更新 + updated_at 更新
      状态不匹配（draft/approved）→ rowcount=0 + 字段未变
  - reschedule_publication(conn, pub_id, scheduled_at, *, expect_status) -> int
      状态匹配（queued）→ rowcount=1 + scheduled_at 真更新 + updated_at 更新
      状态不匹配（published/failed）→ rowcount=0 + 字段未变

只测 db 层函数本身——webui 路由行为在 test_webui_r7_3.py 测。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.utils.ids import new_id


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """每个测试独立的临时 SQLite（与 test_db.py 同模式）。"""
    p = tmp_path / "r7_3.db"
    c = db.connect(p)
    db.init_db(c)
    yield c
    c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_topic(conn: sqlite3.Connection, *, content_hash: str) -> str:
    """插入一条 topic，返回其 id。"""
    now = _now()
    tid = new_id("t")
    db.insert_topic(conn, Topic(
        id=tid, source="rss:test", title="T", url=None,
        summary=None, content_hash=content_hash, pillar="ai_daily",
        score=7.0, score_reason=None,
        status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    ))
    return tid


def _seed_content(
    conn: sqlite3.Connection,
    *,
    topic_id: str | None = None,
    status: str = ContentStatus.GATED.value,
) -> str:
    """插入一条 content（自动 seed topic 满足 FK），返回 content_id。"""
    if topic_id is None:
        topic_id = _seed_topic(conn, content_hash="h_r7_3_" + status)
    now = _now()
    cid = new_id("c")
    db.insert_content(conn, Content(
        id=cid, topic_id=topic_id, pillar="ai_daily",
        title="T", canonical_path=f"output/{cid}/canonical.md",
        formats='["x"]', gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict=None,
        status=status,
        created_at=now, updated_at=now,
    ))
    return cid


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    content_id: str | None = None,
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = "2026-07-07T10:00:00+00:00",
) -> str:
    """插入一条 publication，返回 pub_id。"""
    if content_id is None:
        content_id = _seed_content(conn)
    now = _now()
    pid = new_id("p")
    db.insert_publication(conn, Publication(
        id=pid, content_id=content_id, platform="x",
        account_id="main", scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    ))
    return pid


# ── set_gate_verdict ────────────────────────────────────────


class TestSetGateVerdict:
    def test_status_match_gated_returns_rowcount_1(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=gated 时调用 → rowcount=1。"""
        cid = _seed_content(conn, status=ContentStatus.GATED.value)
        verdict = "REJECTED_BY_HUMAN: 内容空洞"
        n = db.set_gate_verdict(
            conn, cid, verdict, expect_status=ContentStatus.GATED.value,
        )
        assert n == 1

    def test_status_match_gated_updates_field(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=gated 时调用 → gate_verdict 字段真更新。"""
        cid = _seed_content(conn, status=ContentStatus.GATED.value)
        verdict = "REJECTED_BY_HUMAN: 内容空洞"
        db.set_gate_verdict(
            conn, cid, verdict, expect_status=ContentStatus.GATED.value,
        )
        row = conn.execute(
            "SELECT gate_verdict FROM contents WHERE id=?", (cid,),
        ).fetchone()
        assert row["gate_verdict"] == verdict

    def test_status_match_gated_updates_updated_at(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=gated 时调用 → updated_at 自动刷新到 now_utc()。"""
        cid = _seed_content(conn, status=ContentStatus.GATED.value)
        sentinel = "2020-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE contents SET updated_at=? WHERE id=?",
            (sentinel, cid),
        )
        conn.commit()

        db.set_gate_verdict(
            conn, cid, "REJECTED_BY_HUMAN: x",
            expect_status=ContentStatus.GATED.value,
        )
        row = conn.execute(
            "SELECT updated_at FROM contents WHERE id=?", (cid,),
        ).fetchone()
        assert row["updated_at"] != sentinel
        # updated_at 应是 ISO8601 with timezone offset（now_utc 风格）
        assert "+" in row["updated_at"] or "Z" in row["updated_at"]

    def test_status_mismatch_draft_returns_rowcount_0(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=draft 时调用 → rowcount=0。"""
        cid = _seed_content(conn, status=ContentStatus.DRAFT.value)
        n = db.set_gate_verdict(
            conn, cid, "REJECTED_BY_HUMAN: nope",
            expect_status=ContentStatus.GATED.value,
        )
        assert n == 0

    def test_status_mismatch_draft_field_unchanged(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=draft 时调用 → gate_verdict 字段未变。"""
        cid = _seed_content(conn, status=ContentStatus.DRAFT.value)
        original = conn.execute(
            "SELECT gate_verdict, updated_at FROM contents WHERE id=?",
            (cid,),
        ).fetchone()
        db.set_gate_verdict(
            conn, cid, "REJECTED_BY_HUMAN: nope",
            expect_status=ContentStatus.GATED.value,
        )
        after = conn.execute(
            "SELECT gate_verdict, updated_at FROM contents WHERE id=?",
            (cid,),
        ).fetchone()
        assert after["gate_verdict"] == original["gate_verdict"]
        assert after["updated_at"] == original["updated_at"]

    def test_status_mismatch_approved_returns_rowcount_0(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=approved 时调用 → rowcount=0（只有 gated 才允许改 verdict）。"""
        cid = _seed_content(conn, status=ContentStatus.APPROVED.value)
        n = db.set_gate_verdict(
            conn, cid, "REJECTED_BY_HUMAN: nope",
            expect_status=ContentStatus.GATED.value,
        )
        assert n == 0

    def test_nonexistent_content_returns_rowcount_0(
        self, conn: sqlite3.Connection,
    ) -> None:
        """不存在的 content_id → rowcount=0（SQL UPDATE 静默 0 行）。"""
        n = db.set_gate_verdict(
            conn, new_id("c"), "REJECTED_BY_HUMAN: x",
            expect_status=ContentStatus.GATED.value,
        )
        assert n == 0


# ── reschedule_publication ──────────────────────────────────


class TestReschedulePublication:
    def test_status_match_queued_returns_rowcount_1(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=queued 时调用 → rowcount=1。"""
        pid = _seed_publication(
            conn, status=PublicationStatus.QUEUED.value,
            scheduled_at="2026-07-07T10:00:00+00:00",
        )
        new_time = "2026-07-08T18:30:00+00:00"
        n = db.reschedule_publication(
            conn, pid, new_time,
            expect_status=PublicationStatus.QUEUED.value,
        )
        assert n == 1

    def test_status_match_queued_updates_field(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=queued 时调用 → scheduled_at 真更新。"""
        pid = _seed_publication(
            conn, status=PublicationStatus.QUEUED.value,
            scheduled_at="2026-07-07T10:00:00+00:00",
        )
        new_time = "2026-07-08T18:30:00+00:00"
        db.reschedule_publication(
            conn, pid, new_time,
            expect_status=PublicationStatus.QUEUED.value,
        )
        row = conn.execute(
            "SELECT scheduled_at FROM publications WHERE id=?",
            (pid,),
        ).fetchone()
        assert row["scheduled_at"] == new_time

    def test_status_match_queued_updates_updated_at(
        self, conn: sqlite3.Connection,
    ) -> None:
        """status=queued 时调用 → updated_at 自动刷新。"""
        pid = _seed_publication(
            conn, status=PublicationStatus.QUEUED.value,
        )
        sentinel = "2020-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE publications SET updated_at=? WHERE id=?",
            (sentinel, pid),
        )
        conn.commit()

        db.reschedule_publication(
            conn, pid, "2026-07-09T08:00:00+00:00",
            expect_status=PublicationStatus.QUEUED.value,
        )
        row = conn.execute(
            "SELECT updated_at FROM publications WHERE id=?",
            (pid,),
        ).fetchone()
        assert row["updated_at"] != sentinel

    @pytest.mark.parametrize("bad_status", [
        PublicationStatus.PUBLISHED.value,
        PublicationStatus.FAILED.value,
        PublicationStatus.CANCELLED.value,
        PublicationStatus.PUBLISHING.value,
    ])
    def test_status_mismatch_returns_rowcount_0(
        self, conn: sqlite3.Connection, bad_status: str,
    ) -> None:
        """非 queued 状态 → rowcount=0（只允许 queued 改时间）。"""
        pid = _seed_publication(conn, status=bad_status)
        n = db.reschedule_publication(
            conn, pid, "2026-07-09T08:00:00+00:00",
            expect_status=PublicationStatus.QUEUED.value,
        )
        assert n == 0

    def test_status_mismatch_field_unchanged(
        self, conn: sqlite3.Connection,
    ) -> None:
        """非 queued 状态 → scheduled_at / updated_at 都不变。"""
        pid = _seed_publication(
            conn, status=PublicationStatus.PUBLISHED.value,
            scheduled_at="2026-07-07T10:00:00+00:00",
        )
        original = conn.execute(
            "SELECT scheduled_at, updated_at FROM publications WHERE id=?",
            (pid,),
        ).fetchone()
        db.reschedule_publication(
            conn, pid, "2026-07-09T08:00:00+00:00",
            expect_status=PublicationStatus.QUEUED.value,
        )
        after = conn.execute(
            "SELECT scheduled_at, updated_at FROM publications WHERE id=?",
            (pid,),
        ).fetchone()
        assert after["scheduled_at"] == original["scheduled_at"]
        assert after["updated_at"] == original["updated_at"]

    def test_nonexistent_publication_returns_rowcount_0(
        self, conn: sqlite3.Connection,
    ) -> None:
        """不存在的 pub_id → rowcount=0。"""
        n = db.reschedule_publication(
            conn, new_id("p"), "2026-07-09T08:00:00+00:00",
            expect_status=PublicationStatus.QUEUED.value,
        )
        assert n == 0

    def test_only_scheduled_at_and_updated_at_change(
        self, conn: sqlite3.Connection,
    ) -> None:
        """回归保护：函数只改 scheduled_at + updated_at，不动其他字段。

        防止未来 refactor 把 status / platform / error 等也一并改了。
        """
        pid = _seed_publication(
            conn, status=PublicationStatus.QUEUED.value,
        )
        before = conn.execute(
            "SELECT * FROM publications WHERE id=?", (pid,),
        ).fetchone()
        new_time = "2026-07-09T08:00:00+00:00"
        db.reschedule_publication(
            conn, pid, new_time,
            expect_status=PublicationStatus.QUEUED.value,
        )
        after = conn.execute(
            "SELECT * FROM publications WHERE id=?", (pid,),
        ).fetchone()
        # 只允许改 scheduled_at + updated_at
        for key in before.keys():
            if key in ("scheduled_at", "updated_at"):
                continue
            assert before[key] == after[key], (
                f"field {key} 不应被修改: before={before[key]!r}, "
                f"after={after[key]!r}"
            )