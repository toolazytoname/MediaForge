"""M7 R7-4 metrics 裸吞异常修复验证。

覆盖（docs/TASKS.md R7-4 验收标准）：
- collectors.py 与 runner.py 里每个 `except Exception` 块都在 3 行内
  写出 `logger.warning` 并带 `stage="collect"` + `ref_id=<pub_id 或可用 id>`
- 失败仍走 `failed += 1; continue`（不阻断其他 publication）
- 控制流 / 返回值语义不变（regression 防护）
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline import db
from pipeline.config import AppConfig
from pipeline.metrics import run_collect
from pipeline.metrics.collectors import (
    DouyinMetricsCollector,
    ToutiaoMetricsCollector,
    XMetricsCollector,
    XiaohongshuMetricsCollector,
    _real_douyin_probe,
    _real_toutiao_probe,
    _real_x_get,
    build_collector,
)
from pipeline.metrics.runner import CollectResult
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
) -> Publication:
    now = datetime.now(timezone.utc).isoformat()
    pub_time = (
        published_at
        or (datetime.now(timezone.utc) - timedelta(hours=25))
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
        status=PublicationStatus.PUBLISHED.value,
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


@pytest.fixture
def captured_metrics_logs(monkeypatch):
    """让 collectors / runner 复用同一个 propagate=True 的 logger，
    以便 caplog 能抓到（默认 get_logger 设置 propagate=False）。
    """
    log = logging.getLogger("test.metrics.r7_4")
    log.setLevel(logging.WARNING)
    log.propagate = True
    log.handlers.clear()

    def fake_get_logger(name=None, log_dir=None):
        return log

    monkeypatch.setattr(
        "pipeline.metrics.collectors.get_logger", fake_get_logger,
    )
    monkeypatch.setattr(
        "pipeline.metrics.runner.get_logger", fake_get_logger,
    )
    return log


def _find_collect_warnings(
    caplog_records, *,
    ref_id_contains: str | None = None,
) -> list[logging.LogRecord]:
    """从 caplog.records 筛 stage=collect + level=WARNING 的记录。

    ref_id_contains: 只保留 ref_id 包含此子串的记录；None 表示不筛。
    """
    out = []
    for r in caplog_records:
        if r.levelno != logging.WARNING:
            continue
        if getattr(r, "stage", None) != "collect":
            continue
        if ref_id_contains is not None:
            ref = getattr(r, "ref_id", None)
            if not ref or ref_id_contains not in str(ref):
                continue
        out.append(r)
    return out


# ── 静态结构验证：每个 except 块下方 3 行内都有 logger.warning ─


def test_collectors_except_blocks_all_call_logger_warning() -> None:
    """源码静态扫描：collectors.py 里每个 except Exception 块下方 3 行
    都能看到 logger.warning(...)（防「漏补一处」）。
    """
    src = Path(
        "/Users/lazy/Code/crack/MediaForge/pipeline/metrics/collectors.py"
    ).read_text(encoding="utf-8")
    lines = src.splitlines()
    missing: list[int] = []
    for i, line in enumerate(lines):
        if "except Exception" not in line:
            continue
        # 往下看 3 行（含注释）必须出现 logger.warning
        window = "\n".join(lines[i:i + 4])
        if "logger.warning" not in window:
            missing.append(i + 1)  # 1-based 行号
    assert not missing, (
        f"collectors.py 行号 {missing} 缺少 logger.warning:"
    )


def test_runner_except_blocks_all_call_logger_warning() -> None:
    """源码静态扫描：runner.py 里每个 except Exception 块下方 3 行
    都能看到 logger.warning(...)。
    """
    src = Path(
        "/Users/lazy/Code/crack/MediaForge/pipeline/metrics/runner.py"
    ).read_text(encoding="utf-8")
    lines = src.splitlines()
    missing: list[int] = []
    for i, line in enumerate(lines):
        if "except Exception" not in line:
            continue
        window = "\n".join(lines[i:i + 4])
        if "logger.warning" not in window:
            missing.append(i + 1)
    assert not missing, (
        f"runner.py 行号 {missing} 缺少 logger.warning:"
    )


# ── collectors.py 每个 except 的运行时验证 ──────────────────


def test_x_collector_network_error_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """XMetricsCollector.collect: HTTP 抛异常 → warning 日志。"""
    caplog.set_level(logging.WARNING)
    def boom(*a, **kw):
        raise ConnectionError("net down")
    collector = XMetricsCollector(bearer_token="T", http_get=boom)
    pub = MagicMock(platform_post_id="t_abc", id="p_x_log_1")
    assert collector.collect(pub) is None  # 控制流不变

    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_x_log_1",
    )
    assert warns, "X collector 网络异常未发出 warning 日志"


def test_real_x_get_network_error_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """_real_x_get: httpx.get 抛异常 → warning 日志。"""
    caplog.set_level(logging.WARNING)
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "httpx.get", side_effect=RuntimeError("httpx down"),
    ):
        status, body = _real_x_get(
            "https://api.twitter.com/2/tweets/abc",
            headers={"Authorization": "Bearer T"},
        )
    assert status == 0 and body is None
    warns = _find_collect_warnings(caplog.records)
    assert warns, "_real_x_get 网络异常未发出 warning 日志"


def test_toutiao_collector_probe_exception_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """ToutiaoMetricsCollector.collect: probe 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    c = ToutiaoMetricsCollector(
        cookies_path=Path("/tmp/x"),
        probe_fn=MagicMock(side_effect=RuntimeError("probe fail")),
    )
    pub = MagicMock(platform_post_id="71abc", id="p_tt_log_1")
    assert c.collect(pub) is None

    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_tt_log_1",
    )
    assert warns, "头条 collector probe 异常未发出 warning"


def test_real_toutiao_probe_playwright_fail_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """_real_toutiao_probe: playwright 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    # 模拟 import 之外的部分抛错（避免动 global playwright 模块）
    from unittest.mock import patch as _patch
    # 把 sync_playwright 当作返回的 context manager：在它里面抛错
    fake_pw = MagicMock()
    fake_pw.__enter__.side_effect = RuntimeError("chrome fail")
    fake_pw.__exit__.return_value = False
    with _patch(
        "playwright.sync_api.sync_playwright",
        return_value=fake_pw,
        create=True,
    ):
        result = _real_toutiao_probe(Path("/tmp/x.json"), "post_zzz")
    assert result is None
    warns = _find_collect_warnings(caplog.records)
    assert warns, "_real_toutiao_probe 异常未发出 warning"


def test_xhs_collector_probe_exception_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """XiaohongshuMetricsCollector.collect: probe 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)

    def boom(post_id: str):
        raise RuntimeError("xhs fail")

    c = XiaohongshuMetricsCollector(probe_fn=boom)
    pub = MagicMock(platform_post_id="n_abc", id="p_xhs_log_1")
    assert c.collect(pub) is None

    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_xhs_log_1",
    )
    assert warns, "小红书 collector probe 异常未发出 warning"


def test_douyin_collector_probe_exception_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """DouyinMetricsCollector.collect: probe 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    c = DouyinMetricsCollector(
        cookies_path=Path("/tmp/x"),
        probe_fn=MagicMock(side_effect=RuntimeError("dy fail")),
    )
    pub = MagicMock(platform_post_id="v_abc", id="p_dy_log_1")
    assert c.collect(pub) is None

    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_dy_log_1",
    )
    assert warns, "抖音 collector probe 异常未发出 warning"


def test_real_douyin_probe_playwright_fail_logs_warning(
    captured_metrics_logs, caplog,
) -> None:
    """_real_douyin_probe: playwright 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    from unittest.mock import patch as _patch
    fake_pw = MagicMock()
    fake_pw.__enter__.side_effect = RuntimeError("chrome dy down")
    fake_pw.__exit__.return_value = False
    with _patch(
        "playwright.sync_api.sync_playwright",
        return_value=fake_pw,
        create=True,
    ):
        result = _real_douyin_probe(Path("/tmp/x.json"), "v_zzz")
    assert result is None
    warns = _find_collect_warnings(caplog.records)
    assert warns, "_real_douyin_probe 异常未发出 warning"


def test_build_collector_x_failure_logs_warning(
    captured_metrics_logs, caplog, tmp_path,
) -> None:
    """build_collector("x") 工厂失败 → warning（ref_id 可不含 pub_id，
    但 stage=collect 必须 + 有 ref_id 字段）。"""
    caplog.set_level(logging.WARNING)
    # 用空 platforms 让 build_collector 走 except 路径
    from pipeline.config import (
        AppConfig, LLMConfig, LLMTiers, Pillar, PlatformsConfig,
    )
    cfg = AppConfig(
        pillars=[Pillar(id="t", name="T", description="d", scoring_hint="s")],
        llm=LLMConfig(tiers=LLMTiers(cheap="x", creative="y", critical="z")),
        platforms=PlatformsConfig(
            # x 配置但 credentials 文件不存在 → load_x_credentials 抛
            x=__import__("pipeline.config", fromlist=["PlatformAPI"]).PlatformAPI(
                kind="api", windows=[],
                accounts=[__import__(
                    "pipeline.config", fromlist=["AccountAPI"],
                ).AccountAPI(id="main", credentials="/nonexistent.json")],
            ),
        ),
    )
    assert build_collector("x", config=cfg) is None

    warns = _find_collect_warnings(caplog.records)
    assert warns, "build_collector x 失败未发出 warning"


# ── runner.py 每个 except 的运行时验证 ─────────────────────


def test_run_collect_collect_call_failure_logs_warning(
    captured_metrics_logs, caplog, tmp_path,
) -> None:
    """run_collect: collector.collect 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_run_a")
    _seed_publication(conn, pub_id="p_run_b")
    conn.close()

    class _Flaky:
        platform = "x"

        def collect(self, pub):
            if pub.id == "p_run_a":
                raise RuntimeError("boom from collector")
            from pipeline.metrics.collectors import MetricsSnapshot
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=1, likes=0, comments=0, shares=0,
                followers_delta=None, raw="{}",
            )

    import pipeline.metrics.runner as runner_mod
    orig = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: _Flaky()
    try:
        conn = _conn(tmp_path)
        result: CollectResult = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        conn.close()
    finally:
        runner_mod.build_collector = orig

    # 控制流不变：失败那一条不再阻断另一条
    assert result.examined == 2
    assert result.collected == 1
    assert result.failed == 1
    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_run_a",
    )
    assert warns, "runner.collect 失败未发出 warning"


def test_run_collect_insert_snapshot_failure_logs_warning(
    captured_metrics_logs, caplog, tmp_path,
) -> None:
    """run_collect: _insert_snapshot 抛异常 → warning。"""
    caplog.set_level(logging.WARNING)
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_run_ins")
    conn.close()

    class _OkColl:
        platform = "x"

        def collect(self, pub):
            from pipeline.metrics.collectors import MetricsSnapshot
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=1, likes=0, comments=0, shares=0,
                followers_delta=None, raw="{}",
            )

    import pipeline.metrics.runner as runner_mod
    orig_insert = runner_mod._insert_snapshot
    runner_mod._insert_snapshot = (
        lambda conn, snap: (_ for _ in ()).throw(RuntimeError("db fail"))
    )
    orig_build = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: _OkColl()
    try:
        conn = _conn(tmp_path)
        result = run_collect(conn, config=_minimal_cfg())
        conn.close()
    finally:
        runner_mod._insert_snapshot = orig_insert
        runner_mod.build_collector = orig_build

    assert result.failed == 1
    assert result.collected == 0
    warns = _find_collect_warnings(
        caplog.records, ref_id_contains="p_run_ins",
    )
    assert warns, "runner._insert_snapshot 失败未发出 warning"


# ── 控制流 + 返回语义：regression 防护 ──────────────────────


def test_run_collect_continues_after_collector_exception_no_blocking(
    captured_metrics_logs, caplog, tmp_path,
) -> None:
    """异常 collection 不阻断：3 条 publication 一条抛异常 → 另两条仍被处理。"""
    caplog.set_level(logging.WARNING)
    conn = _conn(tmp_path)
    _seed_publication(conn, pub_id="p_a")
    _seed_publication(conn, pub_id="p_b")
    _seed_publication(conn, pub_id="p_c")
    conn.close()

    class _Coll:
        platform = "x"
        seen: list[str] = []

        def collect(self, pub):
            _Coll.seen.append(pub.id)
            if pub.id == "p_b":
                raise RuntimeError("explode")
            from pipeline.metrics.collectors import MetricsSnapshot
            return MetricsSnapshot(
                publication_id=pub.id, platform="x",
                collected_at=datetime.now(timezone.utc).isoformat(),
                views=10, likes=1, comments=0, shares=0,
                followers_delta=None, raw="{}",
            )

    import pipeline.metrics.runner as runner_mod
    orig = runner_mod.build_collector
    runner_mod.build_collector = lambda platform, config: _Coll()
    try:
        conn = _conn(tmp_path)
        result = run_collect(conn, config=_minimal_cfg())
        conn.commit()
        conn.close()
    finally:
        runner_mod.build_collector = orig

    # 三条都跑过（顺序由 SQL 决定，但 a/b/c 全见）
    assert sorted(_Coll.seen) == ["p_a", "p_b", "p_c"]
    assert result.examined == 3
    assert result.collected == 2
    assert result.failed == 1


def test_collector_return_value_still_none_on_exception(
    captured_metrics_logs,
) -> None:
    """控制流不变：collector 抛异常 → 返回 None（不外泄）。"""
    c = ToutiaoMetricsCollector(
        cookies_path=Path("/tmp/x"),
        probe_fn=MagicMock(side_effect=RuntimeError("x")),
    )
    pub = MagicMock(platform_post_id="x", id="p_x")
    assert c.collect(pub) is None


def test_warning_log_record_has_required_fields(
    captured_metrics_logs, caplog,
) -> None:
    """验证日志记录符合结构化契约：含 stage="collect" + 非空 ref_id + msg。"""
    caplog.set_level(logging.WARNING)
    c = ToutiaoMetricsCollector(
        cookies_path=Path("/tmp/x"),
        probe_fn=MagicMock(side_effect=ValueError("argh")),
    )
    c.collect(MagicMock(platform_post_id="z", id="p_struct_check"))

    recs = _find_collect_warnings(caplog.records)
    assert recs, "no warning record"
    r = recs[0]
    assert r.levelno == logging.WARNING
    assert getattr(r, "stage", None) == "collect"
    assert getattr(r, "ref_id", None), "ref_id 空缺"
    assert getattr(r, "msg", "")
