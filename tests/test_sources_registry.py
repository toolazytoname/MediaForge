"""registry 单元测试。

覆盖：
  - 只构造 enabled=True 的源
  - type='rss' → RssSource
  - 未知 type → ValueError（fail-fast，编排层不踩坑）
  - 字段透传（name / url / max_items）
"""
from __future__ import annotations

import pytest

from pipeline.config import SourceDailyHot, SourceRSS
from pipeline.sources.base import SourceAdapter
from pipeline.sources.registry import build_sources
from pipeline.sources.rss import RssSource


def _rss(name: str, enabled: bool = True) -> SourceRSS:
    return SourceRSS(
        type="rss",
        name=name,
        url=f"https://example.com/{name}",
        enabled=enabled,
        max_items=15,
    )


def test_builds_only_enabled_sources() -> None:
    """enabled=False 的源应被跳过（编排层拿不到它）。"""
    sources = build_sources(
        [_rss("on", enabled=True), _rss("off", enabled=False)]
    )
    names = [s.name for s in sources]
    assert names == ["on"]


def test_builds_rss_source_with_correct_fields() -> None:
    """type='rss' → RssSource，name/url/max_items 全部透传。"""
    src = build_sources([_rss("hn")])[0]
    assert isinstance(src, RssSource)
    assert isinstance(src, SourceAdapter)
    assert src.name == "hn"
    assert src.feed_url == "https://example.com/hn"
    assert src.max_items == 15


def test_empty_sources_list_returns_empty() -> None:
    """空配置 → 空列表（不报错，编排层走"无源"分支）。"""
    assert build_sources([]) == []


def test_unknown_type_raises_value_error() -> None:
    """未知 type 立即抛 ValueError（fail-fast）。"""
    from pipeline.sources.registry import _SOURCE_BUILDERS

    # 临时清空注册表，触发 unknown 分支
    saved = _SOURCE_BUILDERS.copy()
    _SOURCE_BUILDERS.clear()
    try:
        with pytest.raises(ValueError) as ei:
            build_sources([_rss("x")])
        assert "unknown" in str(ei.value).lower()
        assert "rss" in str(ei.value).lower()
    finally:
        _SOURCE_BUILDERS.update(saved)


def test_preserves_config_order() -> None:
    """按 config 中 sources 的顺序返回（编排层可预期遍历顺序）。"""
    sources = build_sources(
        [_rss("a"), _rss("b"), _rss("c")]
    )
    assert [s.name for s in sources] == ["a", "b", "c"]