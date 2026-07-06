"""M4-4 Web 控制台 v2 测试：
- 周视图日历（htmx 换周、按天分桶、过滤越界）
- 设置页 cookie 健康状态（每个平台 + 账号的健康检查）
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
from pipeline.config import (
    AccountAPI,
    AccountPlaywright,
    AppConfig,
    PlatformAPI,
    PlatformPlaywright,
    PlatformsConfig,
)
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
    monkeypatch.setattr(webui_app, "_DB_PATH", str(db_path))
    # config 不重要：测试只关心路由存在 + 不报 500
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_minimal_config_yaml(), encoding="utf-8")
    monkeypatch.setattr(webui_app, "_CONFIG_PATH", str(cfg_path))
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
    """seed publication + topic + content 满足 FK。"""
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


# ── 周视图日历 ────────────────────────────────────────────


def test_calendar_returns_200(client) -> None:
    r = client.get("/calendar")
    assert r.status_code == 200


def test_calendar_uses_week_bucket_not_flat_list(client) -> None:
    """新模板用 bucket（7 天分桶）而非 publications 扁平列表。"""
    r = client.get("/calendar")
    assert r.status_code == 200
    html = r.text
    # 模板里含 week-nav 和 day-cell
    assert "week-nav" in html
    assert "calendar-grid" in html
    # 周锚定日显示
    assert "→" in html  # week_start → week_end 之间的箭头


def test_calendar_week_param_filters_to_anchor_week(client, tmp_db) -> None:
    """?week=YYYY-MM-DD 把分桶过滤到该周。"""
    conn = db.connect(str(tmp_db / "state.db"))
    # 2026-07-06 (Monday) → 当周
    now = datetime.now(timezone.utc)
    this_monday = now.date() - timedelta(days=now.weekday())
    next_monday = this_monday + timedelta(days=7)
    # 本周一条 + 下周一条 + 上周一条
    _seed_publication(
        conn, pub_id="p_this01",
        scheduled_at=datetime.combine(
            this_monday, datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat(),
    )
    _seed_publication(
        conn, pub_id="p_next01",
        scheduled_at=datetime.combine(
            next_monday + timedelta(days=2),
            datetime.min.time(), tzinfo=timezone.utc,
        ).isoformat(),
    )
    _seed_publication(
        conn, pub_id="p_prev01",
        scheduled_at=datetime.combine(
            this_monday - timedelta(days=2),
            datetime.min.time(), tzinfo=timezone.utc,
        ).isoformat(),
    )
    conn.close()

    r = client.get(f"/calendar?week={this_monday.isoformat()}")
    assert r.status_code == 200
    html = r.text
    # 本周的应可见
    assert "p_this01" in html
    # 下周 / 上周的不应在本周视图
    assert "p_next01" not in html
    assert "p_prev01" not in html


def test_calendar_grouped_by_day_in_grid(client, tmp_db) -> None:
    """周视图 7 个 day-cell 容器存在。"""
    r = client.get("/calendar")
    html = r.text
    # 模板渲染 7 个 <td class="day-cell">
    assert html.count('class="day-cell"') == 7


def test_calendar_no_publications_shows_em_dash(client) -> None:
    """无排期 → 每个 cell 显示 — 占位。"""
    r = client.get("/calendar")
    assert r.status_code == 200
    # 空 cell 有 <span class="empty">
    assert "empty" in r.text


def test_calendar_status_filter_buttons(client, tmp_db) -> None:
    """模板按 status 渲染按钮：queued 显示 cancel；failed 显示 retry。"""
    conn = db.connect(str(tmp_db / "state.db"))
    now = datetime.now(timezone.utc).isoformat()
    # queued 状态
    _seed_publication(conn, pub_id="p_q01", scheduled_at=now, status="queued")
    # failed 状态
    _seed_publication(conn, pub_id="p_f01", scheduled_at=now, status="failed")
    conn.close()

    r = client.get("/calendar")
    html = r.text
    # 模板根据 status 渲染按钮
    # queued → cancel; failed → retry
    # 至少 cancel 按钮存在（p_q01 命中）
    assert "cancel" in html
    # failed → retry 按钮
    assert "retry" in html


# ── settings cookie 健康 ─────────────────────────────────


def test_settings_includes_cookie_health_section(client) -> None:
    r = client.get("/settings")
    assert r.status_code == 200
    html = r.text
    assert "平台登录态健康" in html
    assert "cookie-health" in html


def test_settings_health_shows_x_healthy_when_credentials_present(
    tmp_db, client,
) -> None:
    """X 凭据合法 → healthy=True。"""
    secrets_dir = tmp_db / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "x_main.json").write_text(
        json.dumps({"bearer_token": "AAAA" * 10}), encoding="utf-8",
    )
    # 重写 config 让路径指向 tmp
    cfg_path = tmp_db / "config.yaml"
    cfg_path.write_text(
        _minimal_config_yaml().replace(
            "secrets/x_main.json",
            str(tmp_db / "secrets/x_main.json"),
        ),
        encoding="utf-8",
    )
    r = client.get("/settings")
    html = r.text
    assert "<td>x</td>" in html
    # healthy 行
    assert "bearer_token len=" in html
    assert "ok\">✓" in html


def test_settings_health_shows_x_unhealthy_when_token_missing(
    tmp_db, client,
) -> None:
    secrets_dir = tmp_db / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "x_main.json").write_text(
        json.dumps({"other": "no token"}), encoding="utf-8",
    )
    cfg_path = tmp_db / "config.yaml"
    cfg_path.write_text(
        _minimal_config_yaml().replace(
            "secrets/x_main.json",
            str(tmp_db / "secrets/x_main.json"),
        ),
        encoding="utf-8",
    )
    r = client.get("/settings")
    html = r.text
    assert "missing bearer_token" in html


def test_settings_health_handles_toutiao_storage_state(tmp_db, client) -> None:
    """头条 storage_state JSON 合法 → healthy。"""
    cookies_dir = tmp_db / "secrets" / "cookies"
    cookies_dir.mkdir(parents=True)
    (cookies_dir / "toutiao_main.json").write_text(json.dumps({
        "cookies": [{"name": "sessionid", "value": "x", "domain": ".toutiao.com", "path": "/"}],
        "origins": [],
    }))
    cfg_path = tmp_db / "config.yaml"
    cfg_path.write_text(_minimal_config_yaml() + """
  toutiao:
    kind: playwright
    windows: ["07:00-09:00"]
    accounts:
      - id: main
        cookies: %s
""" % str(cookies_dir / "toutiao_main.json"), encoding="utf-8",
    )
    r = client.get("/settings")
    assert "toutiao" in r.text
    assert "cookies=" in r.text  # detail 含 cookies=1


def test_settings_health_handles_xhs_missing_skills(
    tmp_db, client, monkeypatch,
) -> None:
    """XHS skills 路径不存在 → unhealthy。

    skills_path 走 env XHS_SKILLS_PATH（per-account 不暴露给 schema；
    AccountPlaywright 只含 id + cookies）。
    """
    monkeypatch.setenv("XHS_SKILLS_PATH", "/nonexistent/xhs-skills")
    cfg_path = tmp_db / "config.yaml"
    cfg_path.write_text(_minimal_config_yaml() + """
  xiaohongshu:
    kind: playwright
    windows: ["12:00-14:00"]
    accounts:
      - id: main
        cookies: secrets/cookies/xhs_main.json
""", encoding="utf-8",
    )
    r = client.get("/settings")
    html = r.text
    assert "xiaohongshu" in html
    assert "skills dir not found" in html


# ── calendar.py 纯函数 ─────────────────────────────────


def test_calendar_bucket_week_segregates_by_day() -> None:
    from pipeline.webui.calendar import bucket_week
    pubs = [
        MagicMock(scheduled_at="2026-07-06T10:00:00+00:00"),
        MagicMock(scheduled_at="2026-07-06T22:00:00+00:00"),
        MagicMock(scheduled_at="2026-07-10T08:00:00+00:00"),
    ]
    b = bucket_week(pubs, anchor_iso="2026-07-08")
    # 2026-07-06 (Mon) → 2026-07-12 (Sun)
    assert b.week_start == date(2026, 7, 6)
    assert b.week_end == date(2026, 7, 12)
    assert len(b.days) == 7
    # 2 条落在 Mon, 1 条落在 Fri
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
    # 1 条合法 + 1 条跳过
    mon = date(2026, 7, 6)
    assert len(b.by_day[mon]) == 1


def test_calendar_bucket_week_prev_next_anchors() -> None:
    from pipeline.webui.calendar import bucket_week
    b = bucket_week([], anchor_iso="2026-07-08")
    assert b.prev_week == "2026-06-29"  # 上周一
    assert b.next_week == "2026-07-13"  # 下周一


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
    """X 凭据文件不存在 → unhealthy。"""
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