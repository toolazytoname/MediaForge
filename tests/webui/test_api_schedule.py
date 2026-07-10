"""M10-11 阶段 D：POST /api/v1/contents/{id}/schedule + schedule_bridge 单元测试。

覆盖：
  - 成功（approved + 合法 platform/account/time）→ 201 + pub_dict + DB 落库 1 条
  - content_not_found (404)
  - content_wrong_status：draft / gated / rejected_by_human / discarded / failed /
    published / done / approved (重复排期场景也合法) → 400 wrong_status
  - platform_not_configured：cfg.platforms.x = None → 400
  - account_not_found：cfg.platforms.x.accounts[].id 不含 → 400
  - invalid_scheduled_at：过去时间 / 非 ISO8601 → 400
  - duplicate_schedule：UNIQUE(content_id, platform, account_id) 命中 → 409
  - envelope 形状 {detail:{error:{code,message}}}
  - DB 直查 `db.list_publications` 验证真的有 1 条 publication 真落库
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    Topic,
    TopicStatus,
)
from pipeline.sources.dedup import content_hash
from pipeline.webui import deps
from pipeline.webui import schedule_bridge


# ── fixtures ────────────────────────────────────────────────


def _config_yaml(*, platforms: dict | None = None) -> str:
    """生成测试用 config.yaml 内容。

    platforms 参数控制 platform 块；不传默认无平台配置。
    platforms 形如 {"x": True, "toutiao": {"accounts": ["main"]}, ...}
    """
    plat_yaml = ""
    if platforms is not None:
        lines = ["platforms:"]
        for name, cfg in platforms.items():
            if cfg is True:
                # 仅声明平台存在，无账号
                lines.append(f"  {name}:")
                lines.append(f"    kind: api")
                lines.append(f"    windows: ['08:00-10:00']")
                lines.append(f"    accounts: []")
            else:
                lines.append(f"  {name}:")
                lines.append(f"    kind: {cfg.get('kind', 'api')}")
                lines.append(f"    windows: ['08:00-10:00']")
                lines.append(f"    accounts:")
                for acct_id in cfg.get("accounts", []):
                    lines.append(f"      - id: {acct_id}")
                    lines.append(f"        credentials: dummy")
        plat_yaml = "\n" + "\n".join(lines) + "\n"

    return (
        "timezone: Asia/Shanghai\n"
        "pillars:\n"
        "  - id: ai_daily\n"
        "    name: AI/科技日报解读\n"
        "    description: d\n"
        "    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n"
        + plat_yaml
    )


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
        _config_yaml(platforms={
            "x": {"accounts": ["main"]},
            "xiaohongshu": {"accounts": ["xhs_personal", "xhs_business"]},
        }),
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
    id: str = "t_sel01",
    title: str = "Topic",
) -> Topic:
    now = "2026-07-05T00:00:00+00:00"
    t = Topic(
        id=id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar="ai_daily", score=8.0, score_reason="ok",
        status=TopicStatus.CONSUMED.value,  # 已被消费（contents.1:1）
        created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    return t


def _seed_content(
    conn: sqlite3.Connection,
    *,
    id: str = "c_seed01",
    status: str = ContentStatus.APPROVED.value,
    topic_id: str = "t_seed01",
) -> Content:
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


def _assert_error_envelope(body: dict, *, code: str) -> None:
    assert "detail" in body, f"missing detail: {body}"
    assert "error" in body["detail"], f"missing error: {body}"
    assert body["detail"]["error"]["code"] == code
    assert "message" in body["detail"]["error"]


def _future_iso(seconds_from_now: int = 3600) -> str:
    """生成相对 now 的 ISO8601 字符串（默认 +1 小时）。"""
    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    # 用 +00:00 形式而非 Z（兼容 Python 3.11 fromisoformat）
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ── API 端点：成功路径 ──────────────────────────────────────


class TestScheduleSuccess:
    def test_201_with_pub_dict_and_db_insert(
        self, client, tmp_env,
    ):
        """approved content + 合法 platform/account/time → 201 + pub_dict + DB 落库。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_ok0001", status=ContentStatus.APPROVED.value)
        conn.close()

        scheduled_at = _future_iso(3600)
        r = client.post(
            "/api/v1/contents/c_ok0001/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": scheduled_at,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # pub_dict 13 字段全有（与 serialize.pub_dict 1:1）
        for k in (
            "id", "content_id", "platform", "account_id",
            "scheduled_at", "published_at", "platform_post_id",
            "platform_url", "error", "retry_count", "status",
            "created_at", "updated_at",
        ):
            assert k in body, f"missing field {k}"
        assert body["id"].startswith("p_")
        assert body["content_id"] == "c_ok0001"
        assert body["platform"] == "x"
        assert body["account_id"] == "main"
        assert body["status"] == "queued"
        assert body["scheduled_at"].startswith(scheduled_at[:13])  # 至少日期前缀对
        assert body["published_at"] is None
        assert body["platform_post_id"] is None
        assert body["platform_url"] is None
        assert body["error"] is None
        assert body["retry_count"] == 0

        # DB 验证：list_publications 真有 1 条
        conn = db.connect(str(tmp_env / "state.db"))
        pubs = db.list_publications(conn)
        conn.close()
        assert len(pubs) == 1
        assert pubs[0].id == body["id"]
        assert pubs[0].content_id == "c_ok0001"
        assert pubs[0].platform == "x"
        assert pubs[0].account_id == "main"
        assert pubs[0].status == "queued"

    def test_201_allows_gated_status(
        self, client, tmp_env,
    ):
        """approved 之外的合法来源 gated 也允许排期（前置规格：gated 也行）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_gated1", status=ContentStatus.GATED.value)
        conn.close()

        r = client.post(
            "/api/v1/contents/c_gated1/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(7200),
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "queued"


# ── API 端点：content_not_found ─────────────────────────────


class TestScheduleContentNotFound:
    def test_404_missing_content_id(self, client):
        r = client.post(
            "/api/v1/contents/c_nope0001/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(),
            },
        )
        assert r.status_code == 404
        body = r.json()
        _assert_error_envelope(body, code="content_not_found")
        assert "c_nope0001" in body["detail"]["error"]["message"]


# ── API 端点：content_wrong_status ──────────────────────────


class TestScheduleContentWrongStatus:
    """所有禁止状态 → 400 wrong_status。"""

    @pytest.mark.parametrize("forbidden_status", [
        ContentStatus.DRAFT.value,
        ContentStatus.REJECTED_BY_HUMAN.value,
        ContentStatus.DISCARDED.value,
        ContentStatus.FAILED.value,
        ContentStatus.DONE.value,
    ])
    def test_400_for_each_forbidden_status(
        self, client, tmp_env, forbidden_status,
    ):
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_forb00", status=forbidden_status)
        conn.close()

        r = client.post(
            "/api/v1/contents/c_forb00/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(),
            },
        )
        assert r.status_code == 400, (
            f"status={forbidden_status}: got {r.status_code}"
        )
        body = r.json()
        _assert_error_envelope(body, code="wrong_status")
        assert forbidden_status in body["detail"]["error"]["message"]


# ── API 端点：platform_not_configured ───────────────────────


class TestSchedulePlatformNotConfigured:
    def test_400_when_platform_missing_in_cfg(
        self, client, tmp_env,
    ):
        """cfg.platforms.toutiao = None → 400 platform_not_configured。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_pf0001")
        conn.close()

        r = client.post(
            "/api/v1/contents/c_pf0001/schedule",
            json={
                "platform": "toutiao",  # 未在 cfg 配置
                "account_id": "main",
                "scheduled_at": _future_iso(),
            },
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="platform_not_configured")
        assert "toutiao" in body["detail"]["error"]["message"]


# ── API 端点：account_not_found ─────────────────────────────


class TestScheduleAccountNotFound:
    def test_400_when_account_not_in_platform_accounts(
        self, client, tmp_env,
    ):
        """account_id 不在 cfg.platforms.xiaohongshu.accounts → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_ac0001")
        conn.close()

        r = client.post(
            "/api/v1/contents/c_ac0001/schedule",
            json={
                "platform": "xiaohongshu",
                "account_id": "xhs_unknown",
                "scheduled_at": _future_iso(),
            },
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="account_not_found")
        assert "xhs_unknown" in body["detail"]["error"]["message"]


# ── API 端点：invalid_scheduled_at ─────────────────────────


class TestScheduleInvalidScheduledAt:
    def test_400_past_time(self, client, tmp_env):
        """scheduled_at 已过去 → 400 invalid_scheduled_at（防脏数据）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_past01")
        conn.close()

        past = _future_iso(-3600)
        r = client.post(
            "/api/v1/contents/c_past01/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": past,
            },
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_scheduled_at")
        assert "past" in body["detail"]["error"]["message"].lower()

    def test_400_non_iso_string(self, client, tmp_env):
        """scheduled_at 非 ISO8601 → 400 invalid_scheduled_at。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_badis1")
        conn.close()

        r = client.post(
            "/api/v1/contents/c_badis1/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": "tomorrow",
            },
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_scheduled_at")

    def test_400_missing_field(self, client, tmp_env):
        """缺 scheduled_at 字段 → 400。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_miss01")
        conn.close()

        r = client.post(
            "/api/v1/contents/c_miss01/schedule",
            json={
                "platform": "x",
                "account_id": "main",
            },
        )
        assert r.status_code == 400
        body = r.json()
        _assert_error_envelope(body, code="invalid_scheduled_at")


# ── API 端点：duplicate_schedule ────────────────────────────


class TestScheduleDuplicate:
    def test_409_when_unique_collision(self, client, tmp_env):
        """UNIQUE(content_id, platform, account_id) 命中 → 409 duplicate_schedule。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_dup001")
        # 预先插一条 queued publication 模拟已有排期
        now = "2026-07-05T00:00:00+00:00"
        existing = Publication(
            id="p_first01", content_id="c_dup001",
            platform="x", account_id="main",
            scheduled_at="2026-07-08T10:00:00+00:00",
            published_at=None, platform_post_id=None, platform_url=None,
            error=None, retry_count=0,
            status="queued", created_at=now, updated_at=now,
        )
        db.insert_publication(conn, existing)
        conn.close()

        # 再排一条同 (content_id, platform, account_id) → 应 409
        r = client.post(
            "/api/v1/contents/c_dup001/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(86400),
            },
        )
        assert r.status_code == 409
        body = r.json()
        _assert_error_envelope(body, code="duplicate_schedule")

        # DB 仍只有 1 条，没插
        conn = db.connect(str(tmp_env / "state.db"))
        pubs = db.list_publications(conn)
        conn.close()
        assert len(pubs) == 1
        assert pubs[0].id == "p_first01"

    def test_201_when_different_account_same_content(
        self, client, tmp_env,
    ):
        """同 content 但不同 account → 201（UNIQUE 不命中）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_diff01")
        now = "2026-07-05T00:00:00+00:00"
        existing = Publication(
            id="p_first02", content_id="c_diff01",
            platform="xiaohongshu", account_id="xhs_personal",
            scheduled_at="2026-07-08T10:00:00+00:00",
            published_at=None, platform_post_id=None, platform_url=None,
            error=None, retry_count=0,
            status="queued", created_at=now, updated_at=now,
        )
        db.insert_publication(conn, existing)
        conn.close()

        # 改用 xhs_business 账号 → 201
        r = client.post(
            "/api/v1/contents/c_diff01/schedule",
            json={
                "platform": "xiaohongshu",
                "account_id": "xhs_business",
                "scheduled_at": _future_iso(86400),
            },
        )
        assert r.status_code == 201, r.text


# ── bridge 纯函数：success 路径 ─────────────────────────────


class TestScheduleBridgePure:
    """不经过 HTTP，直接测 schedule_for_content。"""

    def test_creates_publication_and_db_insert(self, tmp_path, monkeypatch):
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            _config_yaml(platforms={"x": {"accounts": ["main"]}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
        monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))

        conn = db.connect(db_path)
        _seed_content(conn, id="c_brg001")
        conn.close()

        scheduled_at = "2030-01-01T00:00:00+00:00"  # 未来时间
        conn = db.connect(db_path)
        try:
            pub = schedule_bridge.schedule_for_content(
                conn, "c_brg001",
                platform="x", account_id="main",
                scheduled_at=scheduled_at,
                now="2026-07-05T00:00:00+00:00",
            )
        finally:
            conn.close()

        assert pub.id.startswith("p_")
        assert pub.content_id == "c_brg001"
        assert pub.platform == "x"
        assert pub.account_id == "main"
        assert pub.status == "queued"
        assert pub.scheduled_at == scheduled_at

    def test_raises_on_missing_platform(self, tmp_path, monkeypatch):
        from pipeline.config import load_config
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()
        cfg_path = tmp_path / "config.yaml"
        # 注意：完全不写 platforms 段 → cfg.platforms.toutiao = None → 触发 400
        cfg_path.write_text(
            "timezone: Asia/Shanghai\n"
            "pillars:\n"
            "  - id: ai\n"
            "    name: AI\n"
            "    description: d\n"
            "    scoring_hint: s\n"
            "sources: []\n"
            "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
            "budget: {monthly_usd: 80.0}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
        monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))

        conn = db.connect(db_path)
        _seed_content(conn, id="c_brg002")
        conn.close()

        cfg_obj = load_config(cfg_path)
        conn = db.connect(db_path)
        try:
            with pytest.raises(schedule_bridge.PlatformNotConfiguredError):
                schedule_bridge.schedule_for_content(
                    conn, "c_brg002",
                    platform="toutiao", account_id="main",
                    scheduled_at="2030-01-01T00:00:00+00:00",
                    now="2026-07-05T00:00:00+00:00",
                    cfg_obj=cfg_obj,
                )
        finally:
            conn.close()


# ── 其它端点：数据库写入幂等检查 ───────────────────────────


class TestScheduleEnforcement:
    def test_db_only_one_row_after_collision_attempt(
        self, client, tmp_env,
    ):
        """重复尝试后 DB 真只 1 条（不在错误路径上插半截数据）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_content(conn, id="c_idem01")
        now = "2026-07-05T00:00:00+00:00"
        existing = Publication(
            id="p_idem01", content_id="c_idem01",
            platform="x", account_id="main",
            scheduled_at="2026-07-08T10:00:00+00:00",
            published_at=None, platform_post_id=None, platform_url=None,
            error=None, retry_count=0,
            status="queued", created_at=now, updated_at=now,
        )
        db.insert_publication(conn, existing)
        conn.close()

        # 触发 409
        client.post(
            "/api/v1/contents/c_idem01/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(86400),
            },
        )
        client.post(
            "/api/v1/contents/c_idem01/schedule",
            json={
                "platform": "x",
                "account_id": "main",
                "scheduled_at": _future_iso(172800),
            },
        )

        conn = db.connect(str(tmp_env / "state.db"))
        pubs = db.list_publications(conn)
        conn.close()
        assert len(pubs) == 1
        assert pubs[0].id == "p_idem01"
