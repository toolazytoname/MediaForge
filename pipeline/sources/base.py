"""数据源适配器契约（TECH_SPEC §5.1）。

实现类只负责"抓取 + 标准化"，不接触数据库；入库与去重由编排层完成。
新增数据源 = 新增一个文件 + config sources 注册，不改已有代码。

错误类型 SourceError 从 utils.errors 唯一导入（TECH_SPEC §7 全部异常集中）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pipeline.utils.errors import SourceError  # re-export, §7 唯一源


@dataclass(frozen=True)
class RawItem:
    title: str
    url: str | None
    summary: str | None
    published_at: str | None     # ISO8601，解析失败置 None


class SourceAdapter(ABC):
    name: str                    # 唯一标识，如 'rss:hn'，与 config 对应

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """抓取最新条目，按发布时间倒序，最多 max_items 条。
        网络/解析错误统一包装为 SourceError 抛出。"""
