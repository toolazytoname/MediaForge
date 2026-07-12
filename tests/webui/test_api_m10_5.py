"""M10-5 /api/v1 只读 API 测试（二）：publish/analytics/accounts/runs/settings。

覆盖：
  - publish/calendar 空数据 + week 参数
  - publish/records 过滤 + 分页 + with_metric
  - analytics/weekly / cost / publication metrics / platforms
  - accounts 列表 + login-guidance 静态
  - runs 白名单（publish 拒绝；ingest 501；GET run_id 404）
  - settings 脱敏 + doctor
"""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from pipeline import db, db_reads
from pipeline.models import (
    Content, ContentStatus,
    Publication, PublicationStatus,
    Topic, TopicStatus,
)
from pipeline.webui import deps


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
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


def _seed_published(conn, *, platform="x", account_id="main"):
    t = Topic(
        id=f"t_{platform}_{account_id}", source="rss:test", title="T",
        url=None, summary=None, content_hash=f"h_{platform}_{account_id}",
        pillar="ai_daily", score=None, score_reason=None,
        status=TopicStatus.SCORED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    db.insert_topic(conn, t)
    c = Content(
        id=f"c_{platform}_{account_id}", topic_id=t.id, pillar="ai_daily",
        title="C", canonical_path="output/x/canonical.md",
        formats=("x",), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=ContentStatus.APPROVED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    db.insert_content(conn, c)
    p = Publication(
        id=f"p_{platform}_{account_id}", content_id=c.id,
        platform=platform, account_id=account_id,
        scheduled_at="2026-07-08T10:00:00+00:00",
        published_at=None, platform_post_id=None, platform_url=None,
        error=None, retry_count=0, status=PublicationStatus.QUEUED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    db.insert_publication(conn, p)
    return p


# ── Publish ────────────────────────────────────────────────


class TestPublishCalendar:
    def test_empty(self, client):
        r = client.get("/api/v1/publish/calendar")
        assert r.status_code == 200
        body = r.json()
        assert "week_start" in body
        assert body["this_week"]
        assert len(body["days"]) == 7

    def test_with_pubs(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_published(conn, platform="x", account_id="a1")
        conn.close()
        # 用 2026-07-08 这周锚定
        r = client.get("/api/v1/publish/calendar?week=2026-07-08")
        body = r.json()
        # 至少一天有 publication
        has_pub = any(d["publications"] for d in body["days"])
        assert has_pub


class TestPublishRecords:
    def test_empty(self, client):
        r = client.get("/api/v1/publish/records")
        body = r.json()
        assert body["items"] == []
        assert body["limit"] == 50

    def test_with_filter(self, client, tmp_env):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_published(conn, platform="x", account_id="a1")
        _seed_published(conn, platform="toutiao", account_id="a1")
        conn.close()
        r = client.get("/api/v1/publish/records?platform=x")
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["platform"] == "x"

    def test_with_metric(self, client, tmp_env):
        from pipeline.models import Metric
        conn = db.connect(str(tmp_env / "state.db"))
        p = _seed_published(conn, platform="x", account_id="a1")
        db.insert_metric(conn, Metric(
            publication_id=p.id,
            collected_at="2026-07-05T12:00:00+00:00",
            views=100, likes=10, comments=2, shares=1,
            followers_delta=0, raw=None,
        ))
        conn.close()
        r = client.get("/api/v1/publish/records?with_metric=true")
        assert r.status_code == 200
        body = r.json()
        assert body["items"][0]["latest_metric"]["views"] == 100


# ── Analytics ──────────────────────────────────────────────


class TestAnalyticsWeekly:
    def test_empty(self, client):
        r = client.get("/api/v1/analytics/weekly")
        assert r.status_code == 200
        body = r.json()
        assert "overview" in body
        assert "gate_histogram" in body


class TestAnalyticsCost:
    def test_group_stage(self, client):
        r = client.get("/api/v1/analytics/cost?group=stage")
        assert r.status_code == 200
        assert r.json()["group"] == "stage"

    def test_group_day(self, client):
        r = client.get("/api/v1/analytics/cost?group=day&days=30")
        assert r.status_code == 200
        assert r.json()["group"] == "day"

    def test_invalid_group_rejected(self, client):
        r = client.get("/api/v1/analytics/cost?group=invalid")
        assert r.status_code == 422


class TestAnalyticsPublicationMetrics:
    def test_empty_series(self, client):
        r = client.get("/api/v1/analytics/publications/p_nope/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics"] == []
        assert body["count"] == 0


class TestAnalyticsPlatforms:
    def test_empty(self, client):
        r = client.get("/api/v1/analytics/platforms")
        assert r.status_code == 200
        assert r.json()["items"] == []


# ── Accounts ───────────────────────────────────────────────


class TestAccounts:
    def test_empty(self, client):
        r = client.get("/api/v1/accounts")
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_login_guidance(self, client):
        r = client.get("/api/v1/accounts/login-guidance")
        assert r.status_code == 200
        items = r.json()["items"]
        platforms = {it["platform"] for it in items}
        assert {"toutiao", "xiaohongshu", "x", "douyin", "wechat_mp"} <= platforms

    def test_login_guidance_has_auth_type(self, client):
        r = client.get("/api/v1/accounts/login-guidance")
        by_platform = {it["platform"]: it for it in r.json()["items"]}
        assert by_platform["toutiao"]["auth_type"] == "scan_qr"
        assert by_platform["xiaohongshu"]["auth_type"] == "scan_qr"
        assert by_platform["douyin"]["auth_type"] == "scan_qr"
        assert by_platform["x"]["auth_type"] == "config_file"
        assert by_platform["wechat_mp"]["auth_type"] == "config_file"


# ── Runs ───────────────────────────────────────────────────


class TestRuns:
    def test_list_empty(self, client):
        r = client.get("/api/v1/runs")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert "ingest" in body["stage_whitelist"]
        assert "publish" not in body["stage_whitelist"]

    def test_get_run_404(self, client):
        r = client.get("/api/v1/runs/r_xyz")
        assert r.status_code == 404

    def test_post_publish_rejected(self, client):
        # publish 不在白名单——即使不是发布 trigger 也得拒绝
        r = client.post("/api/v1/runs/publish")
        assert r.status_code == 400

    def test_post_unknown_stage_rejected(self, client):
        r = client.post("/api/v1/runs/unknown")
        assert r.status_code == 400

    def test_post_whitelisted_stage_not_implemented(self, client):
        # ingest 在白名单但 P1 未实现
        r = client.post("/api/v1/runs/ingest")
        assert r.status_code == 501


# ── Settings ───────────────────────────────────────────────


class TestSettings:
    def test_returns_sanitized_config(self, client):
        r = client.get("/api/v1/settings")
        assert r.status_code == 200
        body = r.json()
        # config 是 dict，无明文密钥（这里 config 里没有密钥字段）
        assert isinstance(body["config"], dict)
        # doctor 列表
        assert isinstance(body["doctor"], list)
