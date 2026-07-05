"""RSS 数据源实现（TECH_SPEC §5.1）。

RssSource 负责"抓取 + 标准化"两件事：
  1. HTTP 拉取 feed XML（feedparser 解析）
  2. 把每条 entry 映射成 RawItem

不接触数据库，不做去重——入库与去重由编排层（M1-2 ingest）完成。

异常约定：
  - 网络/HTTP 错误、feedparser bozo → SourceError
  - 编排层 except SourceError → log warning, 跳过该源, 继续其他源
"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser  # type: ignore[import-untyped]

from pipeline.sources.base import RawItem, SourceAdapter
from pipeline.utils.errors import SourceError

# summary 截断长度，与 TECH_SPEC §3 topics.summary 对齐
SUMMARY_MAX_CHARS = 2000


def _fetch_text(url: str) -> str:
    """HTTP 拉取 feed 原始文本。

    单独抽出便于测试 patch（避免引 httpx 真实请求）。
    网络错误抛出原始异常，由 fetch() 包装成 SourceError。
    """
    import httpx

    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _parse_published(struct: dict | None) -> str | None:
    """把 feedparser 解析出的 published_parsed 转为 ISO8601 UTC 字符串。

    无日期或解析失败 → None（保留条目，置于排序最末）。
    """
    if not struct:
        return None
    try:
        # published_parsed 是 time.struct_time（UTC）
        dt = datetime(*struct[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def _truncate(text: str | None) -> str | None:
    """summary 截断至 SUMMARY_MAX_CHARS。None 透传。"""
    if text is None:
        return None
    return text[:SUMMARY_MAX_CHARS]


class RssSource(SourceAdapter):
    """标准 RSS/Atom 数据源。"""

    def __init__(self, name: str, feed_url: str, max_items: int) -> None:
        self.name = name
        self.feed_url = feed_url
        self.max_items = max_items

    def fetch(self) -> list[RawItem]:
        """抓取并解析 feed。

        步骤：
          1. 拉取文本（异常 → SourceError）
          2. feedparser 解析；bozo 标记为严重错误也 → SourceError
          3. 每条 entry → RawItem；published_at 统一 ISO8601 UTC 或 None
          4. 按 published_at 降序排序（None 排最末）
          5. 截断到 max_items
        """
        try:
            text = _fetch_text(self.feed_url)
        except Exception as e:
            raise SourceError(
                f"rss fetch failed for {self.name} ({self.feed_url}): {e}"
            ) from e

        parsed = feedparser.parse(text)

        # feedparser 不会因为 bozo 而抛异常，但严重错误时 entries 为空
        # 且 bozo_exception 非空 → 视为源不可用
        if parsed.bozo and not parsed.entries:
            raise SourceError(
                f"rss parse failed for {self.name}: "
                f"{parsed.bozo_exception!r}"
            )

        items: list[RawItem] = []
        for entry in parsed.entries:
            items.append(
                RawItem(
                    title=(entry.get("title") or "").strip(),
                    url=entry.get("link") or None,
                    summary=_truncate(entry.get("summary")),
                    published_at=_parse_published(
                        entry.get("published_parsed")
                    ),
                )
            )

        # 排序：dated 降序（newest first），undated 排最末
        # 直接拼接避免 reverse=True 对 (dated, undated) 两组的二次反转
        dated = sorted(
            (it for it in items if it.published_at is not None),
            key=lambda it: it.published_at,  # type: ignore[arg-type,return-value]
            reverse=True,
        )
        undated = [it for it in items if it.published_at is None]
        return (dated + undated)[: self.max_items]