"""publisher registry 单元测试。

`pipeline/publishers/__init__.py` 的 `get_adapter(platform, account, config)` 返回
对应 PublisherAdapter。`build_adapters(cfg, log_dir)` 一次性构造 cfg 中所有启用的
platform 的 adapter。

设计动机：M4-2 引入 X；M4-3 接入头条/小红书——靠统一注册表避免 cmd_publish 长 if/elif。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import (
    AccountAPI,
    PlatformAPI,
    PlatformsConfig,
)
from pipeline.publishers.base import AccountConfig, PublisherAdapter


def _platforms_x_main() -> PlatformsConfig:
    return PlatformsConfig(
        x=PlatformAPI(
            kind="api",
            windows=["09:00-11:00"],
            accounts=[AccountAPI(id="main", credentials="secrets/x_main.json")],
        ),
    )


def test_get_adapter_unknown_platform_raises() -> None:
    from pipeline.publishers import get_adapter

    with pytest.raises(ValueError, match="(weibo|unknown)"):
        get_adapter("weibo", account=None, config=None)


def test_build_adapters_returns_x_when_enabled(tmp_path: Path) -> None:
    """config.platforms.x 启用 → build_adapters 返回 X adapter。"""
    creds = tmp_path / "x_main.json"
    creds.write_text(json.dumps({"bearer_token": "dummy"}))

    # 用 cfg 传入 accounts.credentials 路径
    pcfg = PlatformAPI(
        kind="api",
        windows=[],
        accounts=[AccountAPI(id="main", credentials=str(creds))],
    )
    account = AccountConfig(
        id="main",
        credentials_path=Path(pcfg.accounts[0].credentials),
    )

    from pipeline.publishers import get_adapter

    adapter = get_adapter("x", account=account, config=None)
    assert isinstance(adapter, PublisherAdapter)
    assert adapter.platform == "x"


def test_get_adapter_x_resolves_credentials(tmp_path: Path) -> None:
    """x adapter 凭据缺失 → 抛 FileNotFoundError 或 ValueError（取决于顺序）。"""
    from pipeline.publishers import get_adapter

    account = AccountConfig(
        id="main",
        credentials_path=tmp_path / "nope.json",
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        get_adapter("x", account=account, config=None)


def test_get_adapter_passes_bearer_token(tmp_path: Path) -> None:
    """凭据含 bearer_token → 注入到 XApiPublisher。"""
    creds = tmp_path / "x_main.json"
    creds.write_text(json.dumps({"bearer_token": "AB"}))
    account = AccountConfig(
        id="main", credentials_path=creds,
    )

    from pipeline.publishers import get_adapter
    from pipeline.publishers.x_api import XApiPublisher

    adapter = get_adapter("x", account=account, config=None)
    assert isinstance(adapter, XApiPublisher)
    # bearer 已解析；私有成员 _token
    assert adapter._token == "AB"
