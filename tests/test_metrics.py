"""M6-1 collect：表现数据回流 测试。

覆盖契约（HARD_PARTS §5 幂等 + M6-1 验收）：
- MetricsSnapshot 字段完整
- XMetricsCollector：官方 API 解析 public_metrics；401/403/429 静默返回 None
- 头条 / 抖音 / 小红书 collector：cookie-based；失败返回 None 不抛
- run_collect：候选 → collector → insert metrics；多次跑天然幂等（时间序列）
- 单条失败 → 不阻断其他 publication
- 未到 24h 的 published 不被 collect（MIN_PUBLISH_AGE_HOURS）
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
from pipeline.metrics import (
    CollectResult,
    DouyinMetricsCollector,
    MetricsSnapshot,
    MIN_PUBLISH_AGE_HOURS,
    ToutiaoMetricsCollector,
    XMetricsCollector,
    XiaohongshuMetricsCollector,
    build_collector,
    run_collect,
)
from pipeline.metrics.runner import _select_candidates
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)


# ── helpers ─────────────────────────────────────────────


def _conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    pub_id: str,
    platform: str = "x",
    platform_post_id: str | None = "t_abc",
    published_at: datetime | None = None,
    status: str = PublicationStatus.PUBLISHED.value,
) -> Publication:
    """seed 一个 content + publication（满足 FK）。"""
    now = datetime.now(timezone.utc).isoformat()
    pub_time = (
        published_at or
        (datetime.now(timezone.utc) - timedelta(hours=25))
    ).isoformat()
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
    pub = Publication(
        id=pub_id, content_id=content_id, platform=platform,
        account_id="main", scheduled_at=pub_time,
        published_at=pub_time, platform_post_id=platform_post_id,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    )
    db.insert_publication(conn, pub)
    return pub


def _minimal_cfg() -> AppConfig:
    return AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
    })


# ── MetricsSnapshot ──────────────────────────────────────


def test_snapshot_construction() -> None:
    snap = MetricsSnapshot(
        publication_id="p1", platform="x",
        collected_at=datetime.now(timezone.utc).isoformat(),
        views=100, likes=10, comments=2, shares=3,
        followers_delta=1, raw="{}",
    )
    assert snap.publication_id == "p1"
    assert snap.views == 100


# ── XMetricsCollector ───────────────────────────────────


def test_x_collector_parses_public_metrics() -> None:
    payload = {
        "data": {
            "id": "t_abc",
            "text": "hello",
            "public_metrics": {
                "retweet_count": 5,
                "reply_count": 2,
                "like_count": 30,
                "quote_count": 1,
                "impression_count": 1500,
            },
        },
    }
    fake_get = MagicMock(return_value=(200, payload))
    c = XMetricsCollector(bearer_token="xxx", http_get=fake_get)
    pub = MagicMock(platform_post_id="t_abc", id="p1")
    snap = c.collect(pub)
    assert snap is not None
    assert snap.views == 1500
    assert snap.likes == 30
    assert snap.comments == 2
    # shares = retweet + quote
    assert snap.shares == 6
    assert snap.followers_delta is None
    fake_get.assert_called_once()
    # URL 含 tweet id + tweet.fields
    call_url = fake_get.call_args.args[0]
    assert "t_abc" in call_url
    assert "public_metrics" in call_url


def test_x_collector_returns_none_when_no_post_id() -> None:
    c = XMetricsCollector(bearer_token="xxx")
    pub = MagicMock(platform_post_id=None, id="p1")
    assert c.collect(pub) is None


def test_x_collector_returns_none_on_401() -> None:
    c = XMetricsCollector(
        bearer_token="expired",
        http_get=MagicMock(return_value=(401, None)),
    )
    pub = MagicMock(platform_post_id="t", id="p")
    assert c.collect(pub) is None


def test_x_collector_returns_none_on_429_rate_limit() -> None:
    """429 → None（明天 cron 自动重试）。"""
    c = XMetricsCollector(
        bearer_token="xxx",
        http_get=MagicMock(return_value=(429, None)),
    )
    assert c.collect(MagicMock(platform_post_id="t", id="p")) is None


def test_x_collector_returns_none_on_network_error() -> None:
    """网络异常 → None（编排层不阻断其他 publication）。"""
    def boom(*a, **kw):
        raise ConnectionError("network down")
    c = XMetricsCollector(bearer_token="xxx", http_get=boom)
    assert c.collect(MagicMock(platform_post_id="t", id="p")) is None


def test_x_collector_returns_zero_metrics_on_missing_fields() -> None:
    """缺字段 → 0（不静默 None；metrics 表允许部分字段为 0）。"""
    c = XMetricsCollector(
        bearer_token="xxx",
        http_get=MagicMock(return_value=(200, {"data": {}})),
    )
    snap = c.collect(MagicMock(platform_post_id="t", id="p"))
    assert snap is not None
    assert snap.views == 0
    assert snap.likes == 0


def test_x_collector_returns_none_when_data_not_dict() -> None:
    """data 不是 dict → None（响应结构异常）。"""
    c = XMetricsCollector(
        bearer_token="xxx",
        http_get=MagicMock(return_value=(200, {"data": "not a dict"})),
    )
    assert c.collect(MagicMock(platform_post_id="t", id="p")) is None


def test_x_collector_requires_bearer_token() -> None:
    with pytest.raises(ValueError, match="bearer_token"):
        XMetricsCollector(bearer_token="")


# ── 头条 collector ──────────────────────────────────────


def test_toutiao_collector_via_probe_fn(tmp_path: Path) -> None:
    fake_probe = MagicMock(return_value={
        "views": 5000, "likes": 200, "comments": 30, "shares": 15,
    })
    c = ToutiaoMetricsCollector(
        cookies_path=tmp_path / "cookies.json",
        probe_fn=fake_probe,
    )
    pub = MagicMock(platform_post_id="71abc", id="p1")
    snap = c.collect(pub)
    assert snap is not None
    assert snap.views == 5000
    assert snap.likes == 200


def test_toutiao_collector_returns_none_on_probe_exception(tmp_path: Path) -> None:
    c = ToutiaoMetricsCollector(
        cookies_path=tmp_path / "cookies.json",
        probe_fn=MagicMock(side_effect=RuntimeError("playwright fail")),
    )
    assert c.collect(MagicMock(platform_post_id="x", id="p")) is None


def test_toutiao_collector_returns_none_when_no_post_id(tmp_path: Path) -> None:
    c = ToutiaoMetricsCollector(
        cookies_path=tmp_path / "cookies.json",
        probe_fn=MagicMock(),
    )
    assert c.collect(MagicMock(platform_post_id=None, id="p")) is None


# ── 小红书 collector ────────────────────────────────────


def test_xhs_collector_returns_none_when_no_probe_fn() -> None:
    """M6-1 阶段 XiaohongshuSkills 未公开标准化 metrics 命令。"""
    c = XiaohongshuMetricsCollector()  # 无 probe_fn
    assert c.collect(MagicMock(platform_post_id="n1", id="p")) is None


def test_xhs_collector_with_probe_fn(tmp_path: Path) -> None:
    c = XiaohongshuMetricsCollector(
        probe_fn=MagicMock(return_value={
            "views": 3000, "likes": 100, "comments": 25,
        }),
    )
    snap = c.collect(MagicMock(platform_post_id="n1", id="p"))
    assert snap is not None
    assert snap.platform == "xiaohongshu"
    assert snap.views == 3000


# ── 抖音 collector ──────────────────────────────────────


def test_douyin_collector_via_probe_fn(tmp_path: Path) -> None:
    c = DouyinMetricsCollector(
        cookies_path=tmp_path / "cookies.json",
        probe_fn=MagicMock(return_value={
            "views": 8000, "likes": 500, "comments": 100,
        }),
    )
    snap = c.collect(MagicMock(platform_post_id="v_xyz", id="p1"))
    assert snap is not None
    assert snap.platform == "douyin"
    assert snap.views == 8000


# ── build_collector 工厂 ─────────────────────────────────


def test_build_collector_returns_x_when_configured(tmp_path: Path) -> None:
    creds = tmp_path / "x_main.json"
    creds.write_text(json.dumps({"bearer_token": "AAAA" * 5}),
                     encoding="utf-8")
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {
            "x": {
                "kind": "api",
                "windows": ["09:00-11:00"],
                "accounts": [{"id": "main", "credentials": str(creds)}],
            },
        },
    })
    c = build_collector("x", config=cfg)
    assert isinstance(c, XMetricsCollector)


def test_build_collector_returns_toutiao_when_configured(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.json"
    cookies.write_text(json.dumps({"cookies": [{"name": "s", "value": "v"}]}),
                       encoding="utf-8")
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "platforms": {
            "toutiao": {
                "kind": "playwright",
                "windows": ["07:00-09:00"],
                "accounts": [{"id": "main", "cookies": str(cookies)}],
            },
        },
    })
    c = build_collector("toutiao", config=cfg)
    assert isinstance(c, ToutiaoMetricsCollector)


def test_build_collector_returns_none_for_unknown_platform() -> None:
    assert build_collector("weibo", config=_minimal_cfg()) is None


def test_build_collector_returns_none_when_platform_not_in_config() -> None:
    assert build_collector("x", config=_minimal_cfg()) is None


# ── _select_candidates：published 且 ≥ 24h ─────────────


def test_select_candidates_filters_recently_published(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    # 25h 前发布 → 应被选
    _seed_publication(
        conn, pub_id="p_old",
        published_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    # 1h 前发布 → 不应被选（< 24h）
    _seed_publication(
        conn, pub_id="p_new",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    # 已取消 → 不应被选
    _seed_publication(
        conn, pub_id="p_cancel",
        published_at=datetime.now(timezone.utc) - timedelta(hours=48),
        status=PublicationStatus.CANCELLED.value,
    )
    conn.close()

    conn = _conn(tmp_path)
    cands = _select_candidates(conn, now=datetime.now(timezone.utc))
    conn.close()
    assert len(cands) == 1
    assert cands[0].id == "p_old"


def test_select_candidates_respects_min_age_override(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_publication(
        conn, pub_id="p_25h",
        published_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    conn.close()

    conn = _conn(tmp_path)
    # min_age=48 → 25h 前的还不足
    cands = _select_candidates(
        conn, now=datetime.now(timezone.utc), min_age_hours=48,
    )
    conn.close()
    assert len(cands) == 0


# ── run_collect 编排 ────────────────────────────────────


def test_run_collect_inserts_metrics_for_eligible(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_x_001")
    conn.commit()
    conn.close()

    # 用 fake collector：返回固定 snapshot
    class _FakeColl:
        platform = "x"

        def collect(self, pub):
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=1000, likes=50, comments=10,
                shares=5, followers_delta=None,
                raw="{}",
            )

    import pipeline.metrics.runner as runner_mod
    orig_build = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: _FakeColl()
    try:
        conn = _conn(tmp_path)
        result = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        # 校验 metrics 表
        rows = conn.execute("SELECT * FROM metrics").fetchall()
        conn.close()
    finally:
        runner_mod.build_collector = orig_build

    assert result.examined == 1
    assert result.collected == 1
    assert result.failed == 0
    assert len(rows) == 1


def test_run_collect_idempotent_multiple_runs(tmp_path: Path) -> None:
    """多次 collect → 多条 metrics 快照（时间序列；天然幂等）。"""
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_idem")
    conn.close()

    class _FakeColl:
        platform = "x"
        call_count = [0]

        def collect(self, pub):
            self.call_count[0] += 1
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=100 * self.call_count[0],
                likes=10, comments=1, shares=0,
                followers_delta=None, raw="{}",
            )

    fake = _FakeColl()
    import pipeline.metrics.runner as runner_mod
    orig_build = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: fake
    try:
        # 第一次
        conn = _conn(tmp_path)
        r1 = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        first_rows = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        # 第二次
        conn = _conn(tmp_path)
        r2 = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        second_rows = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
    finally:
        runner_mod.build_collector = orig_build

    assert r1.collected == 1
    assert r2.collected == 1
    # 时间序列：两次 → 2 条快照（HARD_PARTS §5 metrics 表设计）
    assert first_rows == 1
    assert second_rows == 2


def test_run_collect_continues_on_failure(tmp_path: Path) -> None:
    """单条 collector 抛异常 → 不阻断其他 publication。"""
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_fail")
    _seed_publication(conn, pub_id="p_ok")
    conn.close()

    class _FlakyColl:
        platform = "x"

        def collect(self, pub):
            if pub.id == "p_fail":
                raise RuntimeError("network glitch")
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=100, likes=10, comments=1, shares=0,
                followers_delta=None, raw="{}",
            )

    fake = _FlakyColl()
    import pipeline.metrics.runner as runner_mod
    orig_build = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: fake
    try:
        conn = _conn(tmp_path)
        result = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        conn.close()
    finally:
        runner_mod.build_collector = orig_build

    assert result.examined == 2
    assert result.collected == 1
    assert result.failed == 1


def test_run_collect_skips_platform_without_collector(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    # 小红书 publication；M6-1 阶段 XHS collector 无 probe_fn → collect 返回 None
    _seed_publication(conn, pub_id="p_xhs", platform="xiaohongshu")
    conn.close()

    # 强制 build_collector 返回真实 XiaohongshuMetricsCollector（无 probe_fn）
    conn = _conn(tmp_path)
    result = run_collect(conn, config=_minimal_cfg())
    conn.commit()
    conn.close()

    # collector 存在但 collect 返回 None → 计入 failed（snapshot 为 None）
    assert result.examined == 1
    assert result.failed == 1


def test_run_collect_skips_recently_published(tmp_path: Path) -> None:
    """< 24h 的 published 不被 collect。"""
    conn = _conn(tmp_path)
    _seed_publication(
        conn, pub_id="p_recent",
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    conn.close()

    conn = _conn(tmp_path)
    result = run_collect(conn, config=_minimal_cfg())
    conn.commit()
    conn.close()

    assert result.examined == 0
    assert result.collected == 0