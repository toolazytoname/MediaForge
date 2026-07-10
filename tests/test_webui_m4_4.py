"""M4-4 + M10-9 测试:

M10 P1 后 GET 页面路径全部由 SPA catch-all 服务 frontend/dist/index.html。

- 周视图日历 + 设置页用旧 htmx 模板的 GET 测试 → 改为 SPA 行为断言
- 纯函数 bucket_week / collect_cookie_health 保留并使用新 JSON API
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline import db
from pipeline.config import AppConfig
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)


# ── 公共 fixture ──────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """临时 state.db + monkeypatch webui 模块的 DB 路径。"""
    db_path = tmp_path / "state.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.close()

    import pipeline.webui.app as webui_app
    import pipeline.webui.deps as deps
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_minimal_config_yaml(), encoding="utf-8")
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    return tmp_path


def _minimal_config_yaml() -> str:
    return """\
timezone: Asia/Shanghai
pillars:
  - id: ai_daily
    name: AI 日报
    description: desc
    scoring_hint: hint
llm:
  tiers:
    cheap: claude-haiku-4-5
    creative: claude-sonnet-5
    critical: claude-sonnet-5
platforms:
  x:
    kind: api
    windows: ["09:00-11:00"]
    accounts:
      - id: main
        credentials: secrets/x_main.json
"""


@pytest.fixture
def client(tmp_db: Path):
    from starlette.testclient import TestClient
    from pipeline.webui.app import create_app
    app = create_app()
    return TestClient(app)


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    pub_id: str,
    scheduled_at: str,
    platform: str = "x",
    status: str = PublicationStatus.QUEUED.value,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content_id = "c_" + pub_id.removeprefix("p_")
    topic_id = "t_" + pub_id.removeprefix("p_")

    db.insert_topic(conn, Topic(
        id=topic_id, source="rss:test", title="T", url=None,
        summary=None, content_hash=f"h-{topic_id}", pillar="ai_daily",
        score=7.0, score_reason=None,
        status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    ))
    db.insert_content(conn, Content(
        id=content_id, topic_id=topic_id, pillar="ai_daily",
        title="T", canonical_path=f"output/{content_id}/canonical.md",
        formats='["x"]', gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict="ok",
        status=ContentStatus.APPROVED.value,
        created_at=now, updated_at=now,
    ))
    db.insert_publication(conn, Publication(
        id=pub_id, content_id=content_id, platform=platform,
        account_id="main", scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    ))


# ── SPA catch-all（GET 全部由 SPA 服务；M10 P1 行为）────────


class TestSpaCoversLegacyPages:
    @pytest.mark.parametrize("path", [
        "/calendar",
        "/calendar?week=2026-07-06",
        "/settings",
        "/calendar/2026-07-06",
        "/accounts",
    ])
    def test_get_returns_spa_index(self, client, path: str) -> None:
        r = client.get(path)
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "pico" not in r.text.lower()

    def test_publish_calendar_api_replaces_legacy(self, client) -> None:
        """原 /calendar 的 JSON 形式由 /api/v1/publish/calendar 接管。"""
        r = client.get("/api/v1/publish/calendar")
        assert r.status_code == 200
        body = r.json()
        assert "week_start" in body or "by_day" in body or "days" in body

    def test_settings_api_replaces_legacy(self, client) -> None:
        """/settings 数据由 /api/v1/settings 取（含 cookie_health）。"""
        r = client.get("/api/v1/settings")
        assert r.status_code == 200
        body = r.json()
        assert "config" in body or "cookie_health" in body or "doctor" in body


# ── calendar.py 纯函数 ─────────────────────────────────


def test_calendar_bucket_week_segregates_by_day() -> None:
    from pipeline.webui.calendar import bucket_week
    pubs = [
        MagicMock(scheduled_at="2026-07-06T10:00:00+00:00"),
        MagicMock(scheduled_at="2026-07-06T22:00:00+00:00"),
        MagicMock(scheduled_at="2026-07-10T08:00:00+00:00"),
    ]
    b = bucket_week(pubs, anchor_iso="2026-07-08")
    assert b.week_start == date(2026, 7, 6)
    assert b.week_end == date(2026, 7, 12)
    assert len(b.days) == 7
    mon = date(2026, 7, 6)
    fri = date(2026, 7, 10)
    assert len(b.by_day[mon]) == 2
    assert len(b.by_day[fri]) == 1


def test_calendar_bucket_week_handles_invalid_iso() -> None:
    from pipeline.webui.calendar import bucket_week
    pubs = [
        MagicMock(scheduled_at="not a date"),
        MagicMock(scheduled_at="2026-07-06T10:00:00+00:00"),
    ]
    b = bucket_week(pubs, anchor_iso="2026-07-08")
    mon = date(2026, 7, 6)
    assert len(b.by_day[mon]) == 1


def test_calendar_bucket_week_prev_next_anchors() -> None:
    from pipeline.webui.calendar import bucket_week
    b = bucket_week([], anchor_iso="2026-07-08")
    assert b.prev_week == "2026-06-29"
    assert b.next_week == "2026-07-13"


# ── cookie_health_views 纯函数 ─────────────────────────


def test_collect_cookie_health_empty_when_no_platforms() -> None:
    from pipeline.webui.cookie_health_views import collect_cookie_health
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {},
    })
    out = collect_cookie_health(cfg)
    assert out == []


def test_collect_cookie_health_x_unhealthy_missing_file(tmp_db) -> None:
    from pipeline.webui.cookie_health_views import collect_cookie_health
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {
            "x": {
                "kind": "api",
                "windows": ["09:00-11:00"],
                "accounts": [
                    {"id": "main", "credentials": str(tmp_db / "nope.json")},
                ],
            },
        },
    })
    items = collect_cookie_health(cfg)
    assert len(items) == 1
    assert items[0].platform == "x"
    assert items[0].healthy is False
    assert "not found" in items[0].detail


def test_collect_cookie_health_x_healthy_when_credentials_present(tmp_db) -> None:
    """凭据合法 → healthy=True（供 /api/v1/settings 链路用）。"""
    secrets_dir = tmp_db / "secrets"
    secrets_dir.mkdir()
    cred_path = secrets_dir / "x_main.json"
    cred_path.write_text(
        json.dumps({"bearer_token": "AAAA" * 10}), encoding="utf-8",
    )
    from pipeline.webui.cookie_health_views import collect_cookie_health
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {
            "x": {
                "kind": "api",
                "windows": ["09:00-11:00"],
                "accounts": [{"id": "main", "credentials": str(cred_path)}],
            },
        },
    })
    items = collect_cookie_health(cfg)
    assert len(items) == 1
    assert items[0].platform == "x"
    assert items[0].healthy is True


def test_collect_cookie_health_xhs_missing_skills(tmp_db, monkeypatch) -> None:
    """XHS skills 路径不存在 → unhealthy。"""
    monkeypatch.setenv("XHS_SKILLS_PATH", "/nonexistent/xhs-skills")
    from pipeline.webui.cookie_health_views import collect_cookie_health
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {
            "xiaohongshu": {
                "kind": "playwright",
                "windows": ["12:00-14:00"],
                "accounts": [
                    {"id": "main", "cookies": "/tmp/xhs.json"},
                ],
            },
        },
    })
    items = collect_cookie_health(cfg)
    assert len(items) == 1
    assert items[0].platform == "xiaohongshu"
    assert items[0].healthy is False
    assert "skills" in items[0].detail
