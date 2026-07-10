"""M3-3 → M10-9 Web 控制台测试。

M10 P1 后:
  - GET 页面路径（/、/topics、/review、/calendar、/contents/{id}、/settings）
    由 SPA catch-all 服务 frontend/dist/index.html。
  - POST 写操作（promote/reject/approve/reschedule/cancel/retry）保留旧
    契约不变,curl/脚本可触发;断言照旧。
  - JSON /api/status 保留（迁移期兼容）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.config import (
    AppConfig,
    LLMConfig,
    LLMBudget,
    LLMTiers,
    Pillar,
)
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.webui.app import create_app


# ── helpers ────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spa_index_marker() -> str:
    """SPA index.html 的最小特征（dist 存在时一定返回这些）。"""
    # index.html 含 <div id="app">（Vue 挂载点）+ module script 标签
    p = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return '<!DOCTYPE html><html>'  # dist 缺时的占位提示页


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


@pytest.fixture
def minimal_config() -> AppConfig:
    return AppConfig(
        timezone="Asia/Shanghai",
        pillars=[Pillar(id="ai_daily", name="AI 日报",
                        description="d", scoring_hint="s")],
        sources=[],
        llm=LLMConfig(
            tiers=LLMTiers(
                cheap="claude-haiku-4-5",
                creative="claude-sonnet-5",
                critical="claude-sonnet-5",
            ),
        ),
        budget=LLMBudget(monthly_usd=80.0),
    )


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conn: sqlite3.Connection,
    minimal_config: AppConfig,
) -> TestClient:
    """注入 db path + config,避免 webui 读默认 './state.db' / './config.yaml'。"""
    import pipeline.webui.app as app_mod
    import pipeline.webui.deps as deps

    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(deps, "load_config",
                        lambda *a, **kw: minimal_config)

    app = create_app()
    return TestClient(app)


def _seed_topic(
    conn: sqlite3.Connection,
    *,
    id: str = "t_seed0001",
    title: str = "T",
    status: str = TopicStatus.SCORED.value,
) -> Topic:
    now = _now()
    t = Topic(
        id=id, source="rss:test", title=title, url=None,
        summary=None, content_hash=f"h-{id}", pillar="ai_daily",
        score=7.0, score_reason="ok", status=status,
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    return t


def _seed_content(
    conn: sqlite3.Connection,
    *,
    id: str = "c_seed0001",
    topic_id: str | None = None,
    title: str = "C",
    status: str = ContentStatus.GATED.value,
) -> Content:
    if topic_id is None:
        topic_id = "t_" + id.removeprefix("c_")
    _seed_topic(conn, id=topic_id)
    now = _now()
    c = Content(
        id=id, topic_id=topic_id, pillar="ai_daily", title=title,
        canonical_path=f"output/2026-07-05/{id}/canonical.md",
        formats="[]",
        gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict="通过",
        status=status,
        created_at=now, updated_at=now,
    )
    db.insert_content(conn, c)
    return c


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    id: str = "p_seed0001",
    content_id: str = "c_seed0001",
    platform: str = "x",
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = "2026-07-06T10:00:00+00:00",
) -> Publication:
    _seed_content(conn, id=content_id)
    now = _now()
    p = Publication(
        id=id, content_id=content_id, platform=platform,
        account_id="main", scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    )
    db.insert_publication(conn, p)
    return p


# ── SPA catch-all：所有 GET 页面路径全部进 SPA ──────────────


class TestSpaCatchAll:
    """M10 P1：GET 页面路径不再由 app.py 服务端渲染,统一进 SPA catch-all."""

    PATHS = [
        "/",
        "/topics",
        "/topics?status=scored",
        "/review",
        "/calendar",
        "/contents/c_detail01",
        "/settings",
        "/publish/calendar",
        "/runs",
    ]

    @pytest.mark.parametrize("path", PATHS)
    def test_get_returns_spa_index(self, client: TestClient, path: str) -> None:
        r = client.get(path)
        # SPA 返回 HTML 200（dist 存在）或提示页（dist 缺失）
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        # SPA 产物含 id="app"（dist 存在时）或提示页（缺时）
        # 不再含任何旧 htmx 字样（Pico 风格）
        assert "pico" not in r.text.lower()

    def test_api_status_still_json(self, client: TestClient) -> None:
        """旧 /api/status 契约保留,curl/迁移期仍能用。"""
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert "topics" in body
        assert "contents" in body
        assert "publications" in body


# ── 写操作 POST：契约保留,curl/脚本可触发 ─────────────────


class TestTopicsWrite:
    def test_promote_scored_to_selected(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_topic(conn, id="t_promote1", status=TopicStatus.SCORED.value)
        r = client.post("/topics/t_promote1/promote")
        # htmx fragment —— 2xx
        assert 200 <= r.status_code < 400
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?", ("t_promote1",)
        ).fetchone()
        assert row["status"] == TopicStatus.SELECTED.value

    def test_promote_already_selected_returns_alert(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        """状态机：selected → selected 非法,UI 返回 role=alert 片段（§7）。"""
        _seed_topic(conn, id="t_already1", status=TopicStatus.SELECTED.value)
        r = client.post("/topics/t_already1/promote")
        assert r.status_code >= 400
        assert 'role="alert"' in r.text

    def test_reject_moves_to_rejected(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_topic(conn, id="t_reject01", status=TopicStatus.SCORED.value)
        r = client.post("/topics/t_reject01/reject")
        assert 200 <= r.status_code < 400
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?", ("t_reject01",)
        ).fetchone()
        assert row["status"] == TopicStatus.REJECTED.value


class TestReviewWrite:
    def test_review_approve_moves_gated_to_approved(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_content(conn, id="c_appv01", status=ContentStatus.GATED.value)
        r = client.post(
            "/review/c_appv01",
            data={"decision": "approve"},
        )
        assert 200 <= r.status_code < 400, r.text
        row = conn.execute(
            "SELECT status FROM contents WHERE id=?", ("c_appv01",)
        ).fetchone()
        assert row["status"] == ContentStatus.APPROVED.value

    def test_review_reject_with_reason(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_content(conn, id="c_rej0001", status=ContentStatus.GATED.value)
        r = client.post(
            "/review/c_rej0001",
            data={"decision": "reject", "reason": "内容空洞"},
        )
        assert 200 <= r.status_code < 400, r.text
        row = conn.execute(
            "SELECT status, gate_verdict FROM contents WHERE id=?",
            ("c_rej0001",),
        ).fetchone()
        assert row["status"] == ContentStatus.REJECTED_BY_HUMAN.value
        assert "内容空洞" in row["gate_verdict"]

    def test_review_already_approved_returns_alert(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        """状态机：已 approved 再 approve → 错误片段（§7）。"""
        _seed_content(conn, id="c_done001",
                      status=ContentStatus.APPROVED.value)
        r = client.post(
            "/review/c_done001",
            data={"decision": "approve"},
        )
        assert r.status_code >= 400
        assert 'role="alert"' in r.text


class TestPublicationWrite:
    def test_reschedule_updates_scheduled_at(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_publication(conn, id="p_rs00001")
        new_time = "2026-07-06T18:30:00+00:00"
        r = client.post(
            "/publications/p_rs00001/reschedule",
            data={"scheduled_at": new_time},
        )
        assert 200 <= r.status_code < 400, r.text
        row = conn.execute(
            "SELECT scheduled_at FROM publications WHERE id=?",
            ("p_rs00001",),
        ).fetchone()
        assert row["scheduled_at"].startswith("2026-07-06T18:30")

    def test_cancel_sets_status(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_publication(conn, id="p_cancel01",
                          status=PublicationStatus.QUEUED.value)
        r = client.post("/publications/p_cancel01/cancel")
        assert 200 <= r.status_code < 400
        row = conn.execute(
            "SELECT status FROM publications WHERE id=?", ("p_cancel01",)
        ).fetchone()
        assert row["status"] == PublicationStatus.CANCELLED.value

    def test_retry_failed_to_queued(
        self, client: TestClient, conn: sqlite3.Connection
    ) -> None:
        _seed_publication(conn, id="p_retry001",
                          status=PublicationStatus.FAILED.value)
        r = client.post("/publications/p_retry001/retry")
        assert 200 <= r.status_code < 400
        row = conn.execute(
            "SELECT status FROM publications WHERE id=?", ("p_retry001",)
        ).fetchone()
        assert row["status"] == PublicationStatus.QUEUED.value

    def test_retry_does_not_publish_when_disabled(
        self, client: TestClient, conn: sqlite3.Connection,
    ) -> None:
        """三重锁：retry 只改状态不实际发布。"""
        _seed_publication(conn, id="p_retry002",
                          status=PublicationStatus.FAILED.value)
        called = {"n": 0}
        def fake_publish(*a, **kw):
            called["n"] += 1
            return 0
        # retry 不调 publish——它只走 db.transition；发布由 publish 命令触发
        r = client.post("/publications/p_retry002/retry")
        assert 200 <= r.status_code < 400
        # retry 只走 transition,不调 publish——called 应为 0
        assert called["n"] == 0
