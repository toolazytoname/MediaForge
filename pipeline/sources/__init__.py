"""数据源适配器包（TECH_SPEC §5.1）。"""
from __future__ import annotations

from pipeline.sources.base import RawItem, SourceAdapter
from pipeline.sources.registry import build_sources
from pipeline.sources.rss import RssSource

__all__ = ["RawItem", "SourceAdapter", "RssSource", "build_sources"]