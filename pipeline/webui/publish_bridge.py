"""M10 Phase D：UI 真实发布桥接层。

与 preview_bridge.py 的核心区别：**不做** `_build_preview_bundle` /
`_clone_connection` / `_NoPublishPreviewAdapter` 这三样防真发包装——真实发布
必须和 `pipeline.run cmd_publish` 命令行路径完全一致地调用 `safe_publish`
（它内部自己会调 `build_post_bundle` 并对**真实** `conn` 做状态转移），直接
对真实 `conn` 传 `dry_run=False`、真实 `adapter`，不经过任何包装器。

账号/config/adapter 加载逻辑复用 preview_bridge 里的错误分类与
`_account_credentials_path`——同一套域错误映射，UI 端行为一致。仍然完整走
`safe_publish` 的三重锁与 `config.publish.enabled`/`allowed_platforms` 门禁；
本模块只是多一个触发入口，不降低任何安全门槛。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from pipeline import db
from pipeline.models import PublicationStatus
from pipeline.publishers import get_adapter, safe_publish
from pipeline.publishers.base import AccountConfig
from pipeline.webui.preview_bridge import (
    AccountNotFoundError,
    AdapterInitError,
    ConfigLoadError,
    PlatformNotConfiguredError,
    PreviewError,
    PublicationNotFoundError,
    PublicationWrongStatusError,
    _account_credentials_path,
)


def _run_real_publish(
    conn: sqlite3.Connection,
    publication_id: str,
    run_id: str,
    now: str,
) -> dict[str, Any]:
    """执行单条 publication 的真实发布，返回可序列化结果。

    真实 conn + 真实 adapter + dry_run=False，与 cmd_publish 完全一致；
    真实 state.db 会被 safe_publish 内部改动（status → publishing → published/failed）。
    """
    pub = db.get_publication(conn, publication_id)
    if pub is None:
        raise PublicationNotFoundError(f"publication not found: {publication_id}")
    if pub.status != PublicationStatus.QUEUED.value:
        raise PublicationWrongStatusError(
            f"publication {publication_id} status is {pub.status!r}, not queued"
        )

    from pipeline.webui import deps

    cfg, err = deps.get_config()
    if err:
        raise ConfigLoadError(str(err))

    plat_obj = getattr(cfg.platforms, pub.platform, None)
    if plat_obj is None:
        raise PlatformNotConfiguredError(
            f"platform not configured: {pub.platform}"
        )

    account_cfg = next(
        (account for account in plat_obj.accounts if account.id == pub.account_id),
        None,
    )
    if account_cfg is None:
        raise AccountNotFoundError(
            f"account {pub.account_id!r} not found for platform {pub.platform!r}"
        )

    account = AccountConfig(
        id=account_cfg.id,
        credentials_path=_account_credentials_path(account_cfg),
    )
    try:
        adapter = get_adapter(pub.platform, account=account, config=cfg)
    except Exception as e:
        raise AdapterInitError(str(e)) from e

    result = safe_publish(
        conn,
        pub,
        adapter,
        config=cfg.publish,
        account=account,
        dry_run=False,
        now_iso=now,
    )

    return {
        "published": result.published,
        "reason": result.reason,
        "platform_post_id": result.platform_post_id,
        "url": result.url,
    }


__all__ = [
    "_run_real_publish",
    "PreviewError",
    "PublicationNotFoundError",
    "PublicationWrongStatusError",
    "ConfigLoadError",
    "PlatformNotConfiguredError",
    "AccountNotFoundError",
    "AdapterInitError",
]
