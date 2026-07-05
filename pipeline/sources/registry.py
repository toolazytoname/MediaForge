"""数据源 registry（TECH_SPEC §5.1）。

build_sources(config.sources) → list[SourceAdapter]
  - 仅构造 enabled=True 的源
  - 按 type 分发到对应适配器实现
  - 未知 type → ValueError（fail-fast，编排层不踩坑）

新增数据源 = 在 _SOURCE_BUILDERS 注册 builder + 新增文件实现 SourceAdapter，
不改 registry 主逻辑。
"""
from __future__ import annotations

from typing import Callable

from pipeline.config import Source, SourceRSS
from pipeline.sources.base import SourceAdapter
from pipeline.sources.rss import RssSource


def _build_rss(cfg: SourceRSS) -> RssSource:
    return RssSource(
        name=cfg.name,
        feed_url=cfg.url,
        max_items=cfg.max_items,
    )


# type 字符串 → builder(cfg) → SourceAdapter 实例
_SOURCE_BUILDERS: dict[str, Callable[[Source], SourceAdapter]] = {
    "rss": _build_rss,
}


def build_sources(sources: list[Source]) -> list[SourceAdapter]:
    """从 config.sources 构造所有启用的 SourceAdapter。

    Args:
        sources: 来自 AppConfig.sources 的 SourceRSS / SourceDailyHot 等列表

    Returns:
        仅含 enabled=True 的 adapter，按输入顺序排列

    Raises:
        ValueError: cfg.type 不在 _SOURCE_BUILDERS 注册表中
    """
    adapters: list[SourceAdapter] = []
    for cfg in sources:
        if not getattr(cfg, "enabled", True):
            continue

        builder = _SOURCE_BUILDERS.get(cfg.type)
        if builder is None:
            raise ValueError(
                f"unknown source type: {cfg.type!r} "
                f"(source name={cfg.name!r}). "
                f"Known: {sorted(_SOURCE_BUILDERS)}"
            )

        adapters.append(builder(cfg))

    return adapters