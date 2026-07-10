"""M10 P2 阶段 C：6 个 POST /api/v1/ 写端点测试。

覆盖：
  - POST /api/v1/topics/{id}/promote        topic SCORED → SELECTED
  - POST /api/v1/topics/{id}/reject         topic SCORED → REJECTED
  - POST /api/v1/review/{content_id}        approve/reject + reason
  - POST /api/v1/publications/{id}/reschedule  queued 改时间
  - POST /api/v1/publications/{id}/cancel   QUEUED → CANCELLED
  - POST /api/v1/publications/{id}/retry    FAILED → QUEUED

每个端点覆盖：
  - 成功（200 + envelope 形状 + DB 状态正确）
  - wrong_status（400）
  - not_found（404）
  - 错误 envelope 形状 {detail:{error:{code,message}}}

旧 htmx POST 路由（pipeline/webui/app.py）保留不动；其测试在
tests/test_webui.py（TestTopicsWrite/TestReviewWrite/TestPublicationWrite）
保持原样。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.sources.dedup import content_hash
from pipeline.webui import deps


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """临时 state.db + minimal config。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Shanghai\n"
        "pillars:\n"
        "  - id: ai_daily\n"
        "    name: AI/科技日报解读\n"
        "    description: d\n"
        "    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    return tmp_path


@pytest.fixture
def client(tmp_env):
    from pipeline.webui.app import create_app
    return TestClient(create_app())


def _seed_topic(
    conn: sqlite3.Connection,
    *,
    id: str = "t_seed01",
    title: str = "Test Topic",
    status: str = TopicStatus.SCORED.value,
) -> Topic:
    now = "2026-07-05T00:00:00+00:00"
    t = Topic(
        id=id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar="ai_daily", score=8.0, score_reason="ok",
        status=status, created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    return t


def _seed_content(
    conn: sqlite3.Connection,
    *,
    id: str = "c_seed01",
    status: str = ContentStatus.GATED.value,
) -> Content:
    """种一条 content（topic 1:1 绑定）。"""
    topic_id = f"t_{id.removeprefix('c_')}"
    _seed_topic(conn, id=topic_id, title=f"Topic for {id}")
    now = "2026-07-05T00:00:00+00:00"
    c = Content(
        id=id, topic_id=topic_id, pillar="ai_daily", title="Test",
        canonical_path=f"output/2026-07-05/{id}/canonical.md",
        formats=(), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="通过",
        status=status, created_at=now, updated_at=now,
    )
    db.insert_content(conn, c)
    return c


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    id: str = "p_seed01",
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = "2026-07-08T10:00:00+00:00",
) -> Publication:
    """种一条 publication（先种一条 content）。"""
    content_id = f"c_{id.removeprefix('p_')}"
    _seed_content(conn, id=content_id, status=ContentStatus.APPROVED.value)
    now = "2026-07-05T00:00:00+00:00"
    p = Publication(
        id=id, content_id=content_id,
        platform="x", account_id="main",
        scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None, platform_url=None,
        error=None, retry_count=0,
        status=status, created_at=now, updated_at=now,
    )
    db.insert_publication(conn, p)
    return p


def _assert_error_envelope(body: dict, *, code: str) -> None:
    """FastAPI HTTPException 的 detail 包装：{detail:{error:{code,message}}}"""
    assert "detail" in body, f"missing detail: {body}"
    assert "error" in body["detail"], f"missing error: {body}"
    assert body["detail"]["error"]["code"] == code
    assert "message" in body["detail"]["error"]


# ── POST /api/v1/topics/{id}/promote ────────────────────────


class TestPromoteTopic:
    def test_200_moves_scored_to_selected(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_promo01", status=TopicStatus.SCORED.value)
        conn.close()

        r = client.post("/api/v1/topics/t_promo01/promote")
        assert r.status_code == 200, r.text
        body = r.json()
        # 返回 updated topic dict
        assert body["id"] == "t_promo01"
        assert body["status"] == TopicStatus.SELECTED.value

        # DB 落库
        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?",
            ("t_promo01",),
        ).fetchone()
        assert row["status"] == TopicStatus.SELECTED.value
        conn.close()

    def test_404_topic_not_found(self, client):
        r = client.post("/api/v1/topics/t_nope0001/promote")
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="topic_not_found")
        assert "t_nope0001" in body["detail"]["error"]["message"]

    def test_400_wrong_status(self, client, tmp_env):
        """status=raw 不能 promote → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_promo02", status=TopicStatus.RAW.value)
        conn.close()

        r = client.post("/api/v1/topics/t_promo02/promote")
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="topic_wrong_status")
        # DB 状态未变
        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?",
            ("t_promo02",),
        ).fetchone()
        assert row["status"] == TopicStatus.RAW.value
        conn.close()


# ── POST /api/v1/topics/{id}/reject ─────────────────────────


class TestRejectTopic:
    def test_200_moves_scored_to_rejected(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_rejt01", status=TopicStatus.SCORED.value)
        conn.close()

        r = client.post("/api/v1/topics/t_rejt01/reject")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "t_rejt01"
        assert body["status"] == TopicStatus.REJECTED.value

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?",
            ("t_rejt01",),
        ).fetchone()
        assert row["status"] == TopicStatus.REJECTED.value
        conn.close()

    def test_404_topic_not_found(self, client):
        r = client.post("/api/v1/topics/t_nope_rej/reject")
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="topic_not_found")

    def test_400_wrong_status(self, client, tmp_env):
        """status=selected 不能 reject → 400（reject 只允许 scored→rejected）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_rejt02",
                    status=TopicStatus.SELECTED.value)
        conn.close()

        r = client.post("/api/v1/topics/t_rejt02/reject")
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="topic_wrong_status")


# ── POST /api/v1/review/{content_id} ────────────────────────


class TestReviewDecide:
    def test_200_approve_moves_gated_to_approved(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_appv01", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_appv01",
            json={"decision": "approve"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "c_appv01"
        assert body["status"] == ContentStatus.APPROVED.value

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM contents WHERE id=?",
            ("c_appv01",),
        ).fetchone()
        assert row["status"] == ContentStatus.APPROVED.value
        conn.close()

    def test_200_reject_with_reason_writes_gate_verdict(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_rej001", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_rej001",
            json={"decision": "reject", "reason": "内容空洞"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "c_rej001"
        assert body["status"] == ContentStatus.REJECTED_BY_HUMAN.value
        assert "内容空洞" in body["gate_verdict"]

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status, gate_verdict FROM contents WHERE id=?",
            ("c_rej001",),
        ).fetchone()
        assert row["status"] == ContentStatus.REJECTED_BY_HUMAN.value
        assert "内容空洞" in row["gate_verdict"]
        conn.close()

    def test_200_reject_without_reason_still_works(
        self, client, tmp_env,
    ):
        """reject 不传 reason 也 OK（reason 可选）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_rej002", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_rej002",
            json={"decision": "reject"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == ContentStatus.REJECTED_BY_HUMAN.value

    def test_404_content_not_found(self, client):
        r = client.post(
            "/api/v1/review/c_nope_rv",
            json={"decision": "approve"},
        )
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="content_not_found")

    def test_400_invalid_decision(self, client, tmp_env):
        """decision 不是 approve/reject → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_rej003", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_rej003",
            json={"decision": "maybe"},
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_decision")

    def test_400_invalid_decision_when_missing(self, client, tmp_env):
        """缺 decision 字段 → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_rej004", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_rej004",
            json={},
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_decision")

    def test_409_status_changed_on_already_approved(
        self, client, tmp_env,
    ):
        """status=approved 不能再 approve → 409 (status_changed)。

        与旧 htmx 路由不同：旧版返 400 alert；新版显式 409 让前端
        可区分「非法决策」（400）vs「状态已变」（409）。
        """
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_rej005",
                     status=ContentStatus.APPROVED.value)
        conn.close()

        r = client.post(
            "/api/v1/review/c_rej005",
            json={"decision": "approve"},
        )
        assert r.status_code == 409
        body = r.json()
        _assert_error_envelope(body, code="status_changed")


# ── POST /api/v1/publications/{id}/reschedule ───────────────


class TestReschedulePublication:
    def test_200_updates_scheduled_at(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_rs0001",
                          scheduled_at="2026-07-08T10:00:00+00:00")
        conn.close()

        new_time = "2026-07-09T18:30:00+00:00"
        r = client.post(
            "/api/v1/publications/p_rs0001/reschedule",
            json={"scheduled_at": new_time},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "p_rs0001"
        assert body["scheduled_at"].startswith("2026-07-09T18:30")

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT scheduled_at, status FROM publications WHERE id=?",
            ("p_rs0001",),
        ).fetchone()
        assert row["scheduled_at"].startswith("2026-07-09T18:30")
        assert row["status"] == PublicationStatus.QUEUED.value  # 不改 status
        conn.close()

    def test_404_publication_not_found(self, client):
        r = client.post(
            "/api/v1/publications/p_nope/reschedule",
            json={"scheduled_at": "2026-07-09T18:30:00+00:00"},
        )
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="publication_not_found")

    def test_400_invalid_time(self, client, tmp_env):
        """scheduled_at 非 ISO8601 字符串 → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_rs0002")
        conn.close()

        r = client.post(
            "/api/v1/publications/p_rs0002/reschedule",
            json={"scheduled_at": "not-a-date"},
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_time")

    def test_409_not_queued(self, client, tmp_env):
        """status=published 不能 reschedule → 409。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_rs0003",
                          status=PublicationStatus.PUBLISHED.value)
        conn.close()

        r = client.post(
            "/api/v1/publications/p_rs0003/reschedule",
            json={"scheduled_at": "2026-07-09T18:30:00+00:00"},
        )
        assert r.status_code == 409
        body = r.json()
        _assert_error_envelope(body, code="not_queued")


# ── POST /api/v1/publications/{id}/cancel ────────────────────


class TestCancelPublication:
    def test_200_moves_queued_to_cancelled(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_can001",
                          status=PublicationStatus.QUEUED.value)
        conn.close()

        r = client.post("/api/v1/publications/p_can001/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "p_can001"
        assert body["status"] == PublicationStatus.CANCELLED.value

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM publications WHERE id=?",
            ("p_can001",),
        ).fetchone()
        assert row["status"] == PublicationStatus.CANCELLED.value
        conn.close()

    def test_404_publication_not_found(self, client):
        r = client.post("/api/v1/publications/p_nope_cancel/cancel")
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="publication_not_found")

    def test_409_status_changed(self, client, tmp_env):
        """status=published 不能再 cancel → 409。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_can002",
                          status=PublicationStatus.PUBLISHED.value)
        conn.close()

        r = client.post("/api/v1/publications/p_can002/cancel")
        assert r.status_code == 409
        body = r.json()
        _assert_error_envelope(body, code="status_changed")


# ── POST /api/v1/publications/{id}/retry ─────────────────────


class TestRetryPublication:
    def test_200_moves_failed_to_queued(
        self, client, tmp_env,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_ret001",
                          status=PublicationStatus.FAILED.value)
        conn.close()

        r = client.post("/api/v1/publications/p_ret001/retry")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "p_ret001"
        assert body["status"] == PublicationStatus.QUEUED.value

        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM publications WHERE id=?",
            ("p_ret001",),
        ).fetchone()
        assert row["status"] == PublicationStatus.QUEUED.value
        conn.close()

    def test_404_publication_not_found(self, client):
        r = client.post("/api/v1/publications/p_nope_retry/retry")
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="publication_not_found")

    def test_409_status_changed(self, client, tmp_env):
        """status=queued 不能再 retry（只能 failed→queued）→ 409。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_publication(conn, id="p_ret002",
                          status=PublicationStatus.QUEUED.value)
        conn.close()

        r = client.post("/api/v1/publications/p_ret002/retry")
        assert r.status_code == 409
        body = r.json()
        _assert_error_envelope(body, code="status_changed")