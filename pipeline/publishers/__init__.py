"""Publisher registry（ARCHITECTURE §6 + TECH_SPEC §5.2）。

集中 export 公共 API 与 `get_adapter(platform, account)` 工厂。
M4-2: x（XApiPublisher）
M4-3: toutiao（Playwright 自写）/ xiaohongshu（XiaohongshuSkills 桥）

新增平台 = 在 _BUILDERS 加一行；调用方零改动。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.safe_publish import (
    INTENT_LOG_PREFIX,
    SafePublishResult,
    safe_publish,
    timeout_publishings,
    build_post_bundle,
)

__all__ = [
    "AccountConfig",
    "PostBundle",
    "PublishError",
    "PublishResult",
    "PublisherAdapter",
    "SafePublishResult",
    "safe_publish",
    "timeout_publishings",
    "build_post_bundle",
    "INTENT_LOG_PREFIX",
    "get_adapter",
    "build_adapters",
]


# ── platform → adapter 工厂 ────────────────────────────────


def _build_x(account: AccountConfig, config: Any) -> PublisherAdapter:
    """X / Twitter → XApiPublisher（从 account.credentials_path 读 bearer_token）。"""
    from pipeline.publishers.x_api import XApiPublisher, load_x_credentials
    token = load_x_credentials(account.credentials_path)
    return XApiPublisher(bearer_token=token)


def _build_toutiao(account: AccountConfig, config: Any) -> PublisherAdapter:
    """头条 → ToutiaoPublisher（Playwright 自写，HARD_PARTS §2）。"""
    from pipeline.publishers.toutiao import ToutiaoPublisher
    return ToutiaoPublisher(
        cookies_path=account.credentials_path,
        screenshot_dir=Path("logs/screenshots/toutiao"),
    )


def _build_xiaohongshu(
    account: AccountConfig, config: Any,
) -> PublisherAdapter:
    """小红书 → XiaohongshuPublisher（subprocess 桥，evaluation-notes §2）。"""
    from pipeline.publishers.xiaohongshu import XiaohongshuPublisher
    # 路径优先级：env XHS_SKILLS_PATH > 默认
    skills_path = os.environ.get("XHS_SKILLS_PATH")
    return XiaohongshuPublisher(
        cookies_path=account.credentials_path,
        skills_path=skills_path,
    )


def _build_douyin(account: AccountConfig, config: Any) -> PublisherAdapter:
    """抖音 → DouyinPublisher（Playwright 自写，PRD §3.4 AI 标识必勾）。"""
    from pipeline.publishers.douyin import DouyinPublisher
    # AI 标识占比：config.platforms.douyin.ai_ratio > 默认 'high'
    ai_ratio = "high"
    plat = getattr(config.platforms, "douyin", None)
    if plat is not None and hasattr(plat, "ai_ratio"):
        ai_ratio = plat.ai_ratio or "high"
    return DouyinPublisher(
        cookies_path=account.credentials_path,
        screenshot_dir=Path("logs/screenshots/douyin"),
        ai_ratio=ai_ratio,
    )


_BUILDERS: dict[str, Callable[[AccountConfig, Any], PublisherAdapter]] = {
    "x": _build_x,
    "toutiao": _build_toutiao,
    "xiaohongshu": _build_xiaohongshu,
    "douyin": _build_douyin,
}


def get_adapter(
    platform: str,
    *,
    account: AccountConfig,
    config: Any,
) -> PublisherAdapter:
    """按 platform 字符串取 adapter。未知 platform → ValueError。

    account: 从 cfg.platforms.<p>.accounts[] 取 AccountConfig
    config:  AppConfig（保留供未来按 cfg 选 driver/stealth）
    """
    builder = _BUILDERS.get(platform)
    if builder is None:
        supported = sorted(_BUILDERS.keys())
        raise ValueError(
            f"unknown platform {platform!r}; "
            f"supported: {supported}"
        )
    return builder(account, config)


def build_adapters(
    cfg: Any,
) -> dict[str, list[tuple[AccountConfig, PublisherAdapter]]]:
    """遍历 cfg.platforms 全部启用 platform，返回 {platform: [(account, adapter), ...]}。

    M4-2/M4-3 接入后 cmd_publish 用这个批量构造；
    未注册平台（如 cfg.toutiao 但 _BUILDERS 还没 toutiao）→ 跳 warn。
    """
    import warnings

    out: dict[str, list[tuple[AccountConfig, PublisherAdapter]]] = {}
    platforms = cfg.platforms.model_dump(exclude_none=False)
    for platform_name in ("x", "toutiao", "xiaohongshu", "douyin"):
        plat_obj = getattr(cfg.platforms, platform_name, None)
        if plat_obj is None:
            continue
        accounts_raw = plat_obj.accounts or []
        items: list[tuple[AccountConfig, PublisherAdapter]] = []
        for acc in accounts_raw:
            account_cfg = AccountConfig(
                id=acc.id,
                credentials_path=Path(acc.credentials),
            )
            if platform_name not in _BUILDERS:
                warnings.warn(
                    f"platform {platform_name!r} configured but no adapter "
                    f"registered (skipping account={acc.id})",
                    stacklevel=2,
                )
                continue
            adapter = get_adapter(
                platform_name, account=account_cfg, config=cfg,
            )
            items.append((account_cfg, adapter))
        if items:
            out[platform_name] = items
    return out


# imports stay at bottom to avoid cycle when builders import sub-modules
