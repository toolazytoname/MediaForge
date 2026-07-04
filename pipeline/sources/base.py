"""数据源适配器契约（TECH_SPEC §5.1）。

实现类只负责"抓取 + 标准化"，不接触数据库；入库与去重由编排层完成。
新增数据源 = 新增一个文件 + config sources 注册，不改已有代码。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RawItem:
    title: str
    url: str | None
    summary: str | None
    published_at: str | None     # ISO8601，解析失败置 None


class SourceError(Exception):
    """数据源抓取失败。编排层捕获后跳过该源，继续其他源。"""


class SourceAdapter(ABC):
    name: str                    # 唯一标识，如 'rss:hn'，与 config 对应

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """抓取最新条目，按发布时间倒序，最多 max_items 条。
        网络/解析错误统一包装为 SourceError 抛出。"""
