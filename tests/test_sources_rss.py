"""RssSource 单元测试（TECH_SPEC §5.1 + M1-1 验收）。

覆盖：
  - 本地 fixture 解析（不打网络）
  - 排序：按 published_at 降序；无日期排最后
  - 截断：max_items 生效
  - 容错：缺 link/pubDate 仍能产出 RawItem
  - 失败：网络异常 / 解析异常统一包装为 SourceError
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.sources.base import RawItem, SourceAdapter
from pipeline.sources.rss import RssSource
from pipeline.utils.errors import SourceError

FIXTURES = Path(__file__).parent / "fixtures"


def _patched_open(url: str) -> str:
    """把 http(s) URL 重定向到本地 fixture。"""
    if "corrupt" in url:
        return (FIXTURES / "sample_feed_corrupt.xml").read_text(
            encoding="utf-8"
        )
    if "truncated" in url:
        return (FIXTURES / "sample_feed_truncated.xml").read_text(
            encoding="utf-8"
        )
    return (FIXTURES / "sample_feed.xml").read_text(encoding="utf-8")


def test_rsssource_is_sourceadapter() -> None:
    """RssSource 必须继承 SourceAdapter（基类契约）。"""
    src = RssSource(name="rss:test", feed_url="x", max_items=10)
    assert isinstance(src, SourceAdapter)
    assert src.name == "rss:test"


def test_fetch_returns_sorted_by_published_at_desc() -> None:
    """发布时间倒序；无 pubDate 排最末。"""
    src = RssSource(
        name="rss:sample",
        feed_url="file:///sample_feed.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        items = src.fetch()

    assert len(items) == 4
    # 前 3 条按 published_at 降序
    assert [i.title for i in items[:3]] == [
        "First post (newest)",
        "Second post",
        "Third post (oldest)",
    ]
    # 无日期的最末
    assert items[3].title == "No date post"
    assert items[3].published_at is None


def test_published_at_normalized_to_iso8601_utc() -> None:
    """published_at 统一为 ISO8601 UTC（'+00:00' 或 'Z'，便于入库字段排序）。"""
    src = RssSource(
        name="rss:sample",
        feed_url="file:///sample_feed.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        items = src.fetch()

    first = items[0]
    assert first.published_at is not None
    # 必须可解析为带时区的 datetime
    from datetime import datetime, timezone
    parsed = datetime.fromisoformat(first.published_at)
    assert parsed.tzinfo is not None
    # 转 UTC 后日期应匹配原始 pubDate（2026-07-03 12:30 UTC）
    utc = parsed.astimezone(timezone.utc)
    assert utc.utcoffset().total_seconds() == 0
    assert utc.year == 2026 and utc.month == 7 and utc.day == 3
    assert utc.hour == 12 and utc.minute == 30


def test_summary_truncated_to_2000_chars() -> None:
    """summary 截断至 2000 字符（TECH_SPEC §3 topics.summary 同口径）。

    用 fixture 直接构造一个带长 summary 的 entry，验证 fetch 后被截断。
    """
    long_summary = "x" * 5000
    long_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Long</title>
    <item>
      <title>item</title>
      <link>https://example.com/l</link>
      <description>{long_summary}</description>
      <pubDate>Fri, 03 Jul 2026 12:30:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""
    src = RssSource(
        name="rss:long",
        feed_url="file:///long.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", return_value=long_xml
    ):
        items = src.fetch()

    assert len(items) == 1
    assert items[0].summary is not None
    assert len(items[0].summary) == 2000


def test_max_items_truncates() -> None:
    """max_items 截断生效（在排序之后截）。"""
    src = RssSource(
        name="rss:sample",
        feed_url="file:///sample_feed.xml",
        max_items=2,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        items = src.fetch()

    assert len(items) == 2
    assert [i.title for i in items] == [
        "First post (newest)",
        "Second post",
    ]


def test_handles_missing_link_and_pubdate() -> None:
    """缺 link/pubDate 的 item 仍能产出 RawItem（url=None, published_at=None）。"""
    src = RssSource(
        name="rss:truncated",
        feed_url="file:///sample_feed_truncated.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        items = src.fetch()

    assert len(items) == 1
    item = items[0]
    assert item.title == "Only title here"
    assert item.url is None
    assert item.published_at is None
    assert item.summary == "No link, no pubDate."


def test_network_error_wrapped_as_source_error() -> None:
    """网络异常统一包装为 SourceError，编排层能 except 统一处理。"""
    src = RssSource(
        name="rss:bad",
        feed_url="https://nonexistent.example.invalid/feed",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text",
        side_effect=ConnectionError("dns failed"),
    ):
        with pytest.raises(SourceError) as ei:
            src.fetch()
    assert "rss:bad" in str(ei.value)


def test_parse_error_wrapped_as_source_error() -> None:
    """XML 损坏 → feedparser 抛异常 / bozo → 包成 SourceError。"""
    src = RssSource(
        name="rss:corrupt",
        feed_url="file:///corrupt.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        with pytest.raises(SourceError):
            src.fetch()


def test_url_preserved_as_is() -> None:
    """url 字段直接用 item.link（feedparser 已 normalize 过）。"""
    src = RssSource(
        name="rss:sample",
        feed_url="file:///sample_feed.xml",
        max_items=10,
    )
    with patch(
        "pipeline.sources.rss._fetch_text", side_effect=_patched_open
    ):
        items = src.fetch()

    urls = {i.title: i.url for i in items}
    assert urls["First post (newest)"] == "https://example.com/b"
    assert urls["Third post (oldest)"] == "https://example.com/a"