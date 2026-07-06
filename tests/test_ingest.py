"""ingest 编排单元测试（HARD_PARTS §5 幂等 + §8 单源失败不阻断）。

行为契约：
  - 遍历所有启用源 → fetch → normalize → try_insert_topic → 累计
  - 单源 SourceError → log warning, 跳过该源, 继续其他源
  - 二次运行：所有命中 hash 的条目都是 dup（fetched > 0, new=0, dup=fetched）
  - 打印摘要 `ingest: N fetched, N new, N dup`
  - 入库条目 status='raw'
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.ingest import run_ingest
from pipeline.models import TopicStatus
from pipeline.sources.base import RawItem, SourceAdapter, SourceError


# ── Fake sources ───────────────────────────────────────────

class FakeSource(SourceAdapter):
    def __init__(
        self,
        name: str,
        items: list[RawItem] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._items = items or []
        self._raise = raise_exc

    def fetch(self) -> list[RawItem]:
        if self._raise is not None:
            raise self._raise
        return list(self._items)


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    conn = db.connect(p)
    db.init_db(conn)
    return conn


def _items(*titles: str) -> list[RawItem]:
    return [
        RawItem(
            title=t,
            url=f"https://example.com/{i}",
            summary=None,
            published_at=None,
        )
        for i, t in enumerate(titles)
    ]


# ── 摘要输出 ──────────────────────────────────────────────

def test_summary_line_format(capsys, tmp_path) -> None:
    """stdout 打印 `ingest: N fetched, N new, N dup`。"""
    conn = _open_db(tmp_path)
    src = FakeSource("rss:a", _items("Hello", "World"))

    run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

    captured = capsys.readouterr()
    assert "ingest:" in captured.out
    assert "2 fetched" in captured.out
    assert "2 new" in captured.out
    assert "0 dup" in captured.out


# ── 入库逻辑 ──────────────────────────────────────────────

def test_first_pass_inserts_all(tmp_path) -> None:
    """首次 ingest 全部入库，new=fetched, dup=0。"""
    conn = _open_db(tmp_path)
    src = FakeSource("rss:a", _items("A", "B", "C"))

    result = run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

    assert result.fetched == 3
    assert result.new == 3
    assert result.dup == 0
    assert result.failed_sources == ()

    rows = conn.execute("SELECT * FROM topics").fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["status"] == TopicStatus.RAW.value


def test_second_pass_all_dup(tmp_path) -> None:
    """第二次跑同源：fetched 不变，全部判 dup（HARD_PARTS §5 幂等）。"""
    conn = _open_db(tmp_path)
    src = FakeSource("rss:a", _items("A", "B"))

    run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")
    result = run_ingest(conn, [src], now="2026-07-05T00:01:00+00:00")

    assert result.fetched == 2
    assert result.new == 0
    assert result.dup == 2


def test_normalized_title_dedup_across_sources(tmp_path) -> None:
    """不同 source 但 normalize 后同标题 → 第二条判 dup。"""
    conn = _open_db(tmp_path)
    a = FakeSource("rss:a", _items("Hello, World!"))
    b = FakeSource("rss:b", _items("hello world"))

    run_ingest(conn, [a], now="2026-07-05T00:00:00+00:00")
    result = run_ingest(conn, [b], now="2026-07-05T00:01:00+00:00")

    assert result.new == 0
    assert result.dup == 1
    # 保留首次入库的 source
    rows = conn.execute("SELECT source FROM topics").fetchall()
    assert rows[0]["source"] == "rss:a"


# ── 单源失败不阻断 ───────────────────────────────────────

def test_one_source_fails_others_continue(capsys, tmp_path) -> None:
    """一源抛 SourceError，其他源正常入库（HARD_PARTS §8）。"""
    conn = _open_db(tmp_path)
    bad = FakeSource(
        "rss:bad", raise_exc=SourceError("network down")
    )
    good = FakeSource("rss:good", _items("X", "Y"))

    result = run_ingest(conn, [bad, good], now="2026-07-05T00:00:00+00:00")

    assert result.fetched == 2
    assert result.new == 2
    assert result.dup == 0
    assert result.failed_sources == ("rss:bad",)

    # 警告打印到 stderr
    captured = capsys.readouterr()
    assert "rss:bad" in (captured.err + captured.out)


def test_unexpected_exception_also_isolated(capsys, tmp_path) -> None:
    """非 SourceError 异常也隔离（防御性：bug 不应阻断整批）。"""
    conn = _open_db(tmp_path)
    bad = FakeSource(
        "rss:bug", raise_exc=RuntimeError("oops")
    )
    good = FakeSource("rss:good", _items("Z"))

    result = run_ingest(conn, [bad, good], now="2026-07-05T00:00:00+00:00")

    assert result.new == 1
    assert result.failed_sources == ("rss:bug",)


def test_all_sources_fail_returns_zero_fetched(capsys, tmp_path) -> None:
    """全失败：fetched=0, new=0, 仍打印摘要。"""
    conn = _open_db(tmp_path)
    a = FakeSource("rss:a", raise_exc=SourceError("a"))
    b = FakeSource("rss:b", raise_exc=SourceError("b"))

    result = run_ingest(conn, [a, b], now="2026-07-05T00:00:00+00:00")

    assert result.fetched == 0
    assert result.new == 0
    assert result.dup == 0
    assert set(result.failed_sources) == {"rss:a", "rss:b"}


# ── 入库内容保真 ────────────────────────────────────────

def test_inserted_topic_preserves_fields(tmp_path) -> None:
    """入库字段保真：source / title / url / summary（≤2000）。"""
    conn = _open_db(tmp_path)
    items = [
        RawItem(
            title="Test",
            url="https://example.com/x",
            summary="x" * 5000,
            published_at="2026-07-01T00:00:00+00:00",
        ),
    ]
    src = FakeSource("rss:a", items)

    run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

    row = conn.execute("SELECT * FROM topics").fetchone()
    assert row["title"] == "Test"
    assert row["url"] == "https://example.com/x"
    assert row["source"] == "rss:a"
    assert len(row["summary"]) == 2000
    assert row["content_hash"]


def test_empty_source_list_is_noop(tmp_path) -> None:
    """空源列表 → 0/0/0，不报错。"""
    conn = _open_db(tmp_path)

    result = run_ingest(conn, [], now="2026-07-05T00:00:00+00:00")

    assert (result.fetched, result.new, result.dup) == (0, 0, 0)


# ── 域名安全校验（M1-5 借鉴 Horizon/sansan0） ────────────────

from pipeline.sources import safety as safety_mod


def _url_items(titles_urls: list[tuple[str, str]]) -> list[RawItem]:
    """按 (title, url) 元组构造 RawItem 列表。"""
    return [
        RawItem(title=t, url=u, summary=None, published_at=None)
        for t, u in titles_urls
    ]


def test_safety_drops_bad_items_when_source_registered(
    capsys, tmp_path,
) -> None:
    """源在 KNOWN_DOMAIN_RULES 登记 → URL 不匹配的条目被丢弃，dropped_safety>0。

    注意：fetched/new/dup 三计数只计通过校验的条目（被丢弃的不入库）。
    """
    conn = _open_db(tmp_path)
    # 测试结束后清理规则表（防止污染其他测试）
    safety_mod.KNOWN_DOMAIN_RULES["test:demo"] = "demo.com"
    try:
        items = _url_items([
            ("Good1", "https://demo.com/a"),
            ("Bad", "https://attacker.com/b"),
            ("Good2", "https://m.demo.com/c"),
            ("Http", "http://demo.com/d"),
            ("NoURL", ""),
        ])
        src = FakeSource("test:demo", items)

        result = run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

        # 5 条 fetch，3 条通过（Good1/Good2/NoURL）入库，2 条丢弃
        assert result.fetched == 3
        assert result.new == 3
        assert result.dup == 0
        assert result.dropped_safety == 2
        assert result.failed_sources == ()

        # warn 日志到 stderr
        captured = capsys.readouterr()
        assert "test:demo" in captured.err
        assert "dropped 2 item(s)" in captured.err
    finally:
        safety_mod.KNOWN_DOMAIN_RULES.pop("test:demo", None)


def test_safety_no_op_when_source_not_registered(capsys, tmp_path) -> None:
    """源未在 KNOWN_DOMAIN_RULES 登记 → 不校验，dropped_safety=0，warn 不打。"""
    conn = _open_db(tmp_path)
    # 不登记 'rss:demo' —— 应该全部放行
    items = _url_items([
        ("X", "https://attacker.com/a"),  # 任意域名都放行
        ("Y", "http://insecure.example.com/b"),
    ])
    src = FakeSource("rss:demo", items)

    result = run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

    assert result.fetched == 2
    assert result.new == 2
    assert result.dropped_safety == 0

    captured = capsys.readouterr()
    assert "dropped" not in captured.err  # 无 drop 时不打印 warn


def test_safety_mixed_sources_independent_rules(
    capsys, tmp_path,
) -> None:
    """多源独立校验：A 源登记规则、B 源未登记 → 各自行为独立。"""
    conn = _open_db(tmp_path)
    safety_mod.KNOWN_DOMAIN_RULES["test:has_rule"] = "good.com"
    try:
        a_items = _url_items([
            ("A-good", "https://good.com/1"),
            ("A-bad", "https://evil.com/2"),
        ])
        b_items = _url_items([
            ("B-anything", "https://random.com/3"),  # B 源不校验
        ])
        a = FakeSource("test:has_rule", a_items)
        b = FakeSource("rss:no_rule", b_items)

        result = run_ingest(conn, [a, b], now="2026-07-05T00:00:00+00:00")

        # A: 1 入库 + 1 drop；B: 1 入库
        assert result.new == 2
        assert result.dropped_safety == 1
    finally:
        safety_mod.KNOWN_DOMAIN_RULES.pop("test:has_rule", None)


def test_safety_does_not_affect_dedup_count(tmp_path) -> None:
    """dropped_safety 不影响 dup 计数（被丢弃的不参与后续 dedup）。"""
    conn = _open_db(tmp_path)
    safety_mod.KNOWN_DOMAIN_RULES["test:demo"] = "demo.com"
    try:
        items = _url_items([("Same", "https://demo.com/1")])
        src = FakeSource("test:demo", items)

        run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")
        result = run_ingest(conn, [src], now="2026-07-05T00:01:00+00:00")

        # 首次 1 入库，第二次 1 dup（content_hash 命中）
        assert result.fetched == 1
        assert result.new == 0
        assert result.dup == 1
        assert result.dropped_safety == 0
    finally:
        safety_mod.KNOWN_DOMAIN_RULES.pop("test:demo", None)


def test_safety_default_value_in_ingest_result(tmp_path) -> None:
    """IngestResult.dropped_safety 默认 0（旧调用方兼容）。"""
    conn = _open_db(tmp_path)
    src = FakeSource("rss:any", _url_items([("X", "https://anywhere.com/1")]))

    result = run_ingest(conn, [src], now="2026-07-05T00:00:00+00:00")

    # 字段存在且为 0
    assert hasattr(result, "dropped_safety")
    assert result.dropped_safety == 0