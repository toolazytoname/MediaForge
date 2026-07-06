"""VideoEngine 模块导出（TECH_SPEC §5.6）。

新增引擎 = 新增一个 .py 文件 + 在 _ENGINES 注册；调用方零改动。
"""
from __future__ import annotations

from pipeline.creators.video.base import (
    VideoEngine,
    VideoJobStatus,
    VideoRequest,
)
from pipeline.creators.video.mpt import (
    MPTEngine,
    build_mpt_engine,
    is_mpt_alive,
)

__all__ = [
    "VideoEngine",
    "VideoRequest",
    "VideoJobStatus",
    "MPTEngine",
    "build_mpt_engine",
    "is_mpt_alive",
]


# ── 引擎工厂（按 cfg.video.engine 选择；初始化失败降级） ─


_ENGINE_BUILDERS = {
    "mpt": build_mpt_engine,
}


def build_video_engine(cfg) -> VideoEngine | None:
    """按 cfg.video.engine 构造引擎。失败返回 None（HARD_PARTS §6 决策 5）。

    编排层拿到 None 时跳过视频格式，图文格式不受影响。
    """
    engine_name = getattr(cfg.video, "engine", "mpt")
    builder = _ENGINE_BUILDERS.get(engine_name)
    if builder is None:
        return None
    try:
        return builder(cfg)
    except Exception:
        # 工厂捕获：参数错误、依赖缺失等都不阻断图文链路
        return None