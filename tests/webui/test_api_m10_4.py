"""M10-4 /api/v1 只读 API 测试（一）：dashboard/topics/sources/contents/review。

覆盖：
  - 各 router 路由注册
  - JSON 响应形状
  - 过滤参数（status/pillar/source/limit/offset）
  - 404 / 200 / 空数据 / 有数据
  - 不写库（mock cfg / tmp db）
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.models import (
    Content, ContentStatus,
    Publication, PublicationStatus,
    Topic, TopicStatus,
)
from pipeline.webui import deps


# ── Fixtures ────────────────────────────────────────────────


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
        "pillars:\n  - id: ai_daily\n    name: AI\n    description: d\n    scoring_hint: s\n"
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


def _seed_topic(conn, *, id="t_x", title="T", status=TopicStatus.RAW):
    now = "2026-07-05T00:00:00+00:00"
    t = Topic(
        id=id, source="rss:test", title=title, url=None,
        summary=None, content_hash=f"h_{id}", pillar="ai_daily",
        score=None, score_reason=None, status=status,
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)


def _seed_content(conn, *, id="c_x", status=ContentStatus.GATED):
    topic_id = "t_" + id.removeprefix("c_")
    _seed_topic(conn, id=topic_id)
    now = "2026-07-05T00:00:00+00:00"
    c = Content(
        id=id, topic_id=topic_id, pillar="ai_daily", title="C",
        canonical_path=f"output/2026-07-05/{id}/canonical.md",
        formats=("x",), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="通过", status=status,
        created_at=now, updated_at=now,
    )
    db.insert_content(conn, c)


# ── Dashboard ──────────────────────────────────────────────


class TestDashboard:
    def test_empty_db_returns_zeros(self, client):
        r = client.get("/api/v1/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert body["counts"] == {"topics": {}, "contents": {}, "publications": {}}
        assert body["todos"] == {"to_review": 0, "to_publish": 0, "publish_failed": 0}
        assert body["budget"]["monthly_usd"] == 80.0
        assert body["budget"]["used_usd"] == 0.0
        assert body["activity"] == []
        assert isinstance(body["gate_histogram"], list)

    def test_with_data(self, client, tmp_env):
        # dashboard 看 topics 计数，直接插 topic 即可
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_d1", status=TopicStatus.SCORED)
        conn.close()
        r = client.get("/api/v1/dashboard")
        body = r.json()
        assert body["todos"]["to_review"] == 0  # 没有 gated content
        assert "scored" in body["counts"]["topics"]
        assert body["counts"]["topics"]["scored"] == 1


# ── Topics ─────────────────────────────────────────────────


class TestTopicsAPI:
    def test_empty(self, client):
        r = client.get("/api/v1/topics")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50

    def test_with_filter(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_t1", status=TopicStatus.RAW)
        _seed_topic(conn, id="t_t2", status=TopicStatus.SCORED)
        conn.close()
        r = client.get("/api/v1/topics?status=scored")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "t_t2"

    def test_pagination(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        for i in range(5):
            _seed_topic(conn, id=f"t_p{i}", title=f"T{i}")
        conn.close()
        r = client.get("/api/v1/topics?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        # offset=2 拿下两条（updated_at DESC，id 顺序不固定，看集合不重叠即可）
        r2 = client.get("/api/v1/topics?limit=2&offset=2")
        ids1 = {it["id"] for it in body["items"]}
        ids2 = {it["id"] for it in r2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    def test_get_by_id(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_g", title="GT")
        conn.close()
        r = client.get("/api/v1/topics/t_g")
        assert r.status_code == 200
        assert r.json()["title"] == "GT"

    def test_get_not_found(self, client):
        r = client.get("/api/v1/topics/t_nope")
        assert r.status_code == 404


# ── Sources ────────────────────────────────────────────────


class TestSourcesAPI:
    def test_empty_sources(self, client):
        r = client.get("/api/v1/sources")
        assert r.status_code == 200
        assert r.json() == {"items": []}


# ── Contents ───────────────────────────────────────────────


class TestContentsAPI:
    def test_empty(self, client):
        r = client.get("/api/v1/contents")
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_list_with_data(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_l1", status=ContentStatus.APPROVED)
        _seed_content(conn, id="c_l2", status=ContentStatus.GATED)
        conn.close()
        r = client.get("/api/v1/contents?status=approved")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "c_l1"
        # tuple → list
        assert isinstance(body["items"][0]["formats"], list)

    def test_detail_404(self, client):
        r = client.get("/api/v1/contents/c_nope")
        assert r.status_code == 404

    def test_detail_200(self, client, tmp_env, monkeypatch):
        # canonical_path 是相对路径，API 用 CWD 解析——chdir 到 tmp_env
        monkeypatch.chdir(tmp_env)
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_d")
        # 写个真实 canonical.md
        out_dir = tmp_env / "output" / "2026-07-05" / "c_d"
        out_dir.mkdir(parents=True)
        (out_dir / "canonical.md").write_text("# Hello", encoding="utf-8")
        conn.close()
        r = client.get("/api/v1/contents/c_d")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "c_d"
        assert "<h1>" in body["canonical_html"]
        assert isinstance(body["files"], list)
        assert body["images"] == {"cover": None, "inline": []}
        assert body["publications"] == []


# ── Review ─────────────────────────────────────────────────


class TestReviewAPI:
    def test_only_gated(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_r1", status=ContentStatus.GATED)
        _seed_content(conn, id="c_r2", status=ContentStatus.APPROVED)
        conn.close()
        r = client.get("/api/v1/review")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "c_r1"
        assert body["items"][0]["status"] == "gated"
