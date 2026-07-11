"""cmd_schedule / cmd_publish 的小型 pure helper 回归测试（真实数据冒烟发现的 bug）。

1. PlatformsConfig 的平台字段是可选的（缺省 = None = 未启用），但 config.yaml
   里配置的平台通常只是全部支持平台的子集（如只配了 x/toutiao/xiaohongshu，
   没配 douyin）。cmd_schedule 必须把这些 None 过滤掉再传给 scheduler.plan()，
   否则对 None 取 .windows 会 AttributeError。
2. x 平台账号是 AccountAPI（字段 credentials），Playwright 三平台
   （toutiao/xiaohongshu/douyin）是 AccountPlaywright（字段 cookies）——字段名
   不同，cmd_publish 取错字段会 AttributeError。
"""
from __future__ import annotations

from pipeline.config import (
    AccountAPI,
    AccountPlaywright,
    PlatformAPI,
    PlatformsConfig,
    PlatformPlaywright,
)
from pipeline.run import _account_credentials_path, _active_platform_configs


def test_filters_out_unconfigured_platforms():
    # Arrange: 只配置 x 和 toutiao，xiaohongshu/douyin 缺省为 None
    cfg = PlatformsConfig(
        x=PlatformAPI(kind="api", windows=["09:00-11:00"], accounts=[]),
        toutiao=PlatformPlaywright(
            kind="playwright", windows=["07:00-09:00"], accounts=[]
        ),
    )

    # Act
    result = _active_platform_configs(cfg)

    # Assert
    assert set(result.keys()) == {"x", "toutiao"}
    assert all(v is not None for v in result.values())


def test_returns_empty_dict_when_no_platforms_configured():
    # Arrange
    cfg = PlatformsConfig()

    # Act
    result = _active_platform_configs(cfg)

    # Assert
    assert result == {}


def test_account_credentials_path_uses_credentials_field_for_x():
    # Arrange
    account = AccountAPI(id="main", credentials="secrets/x_main.json")

    # Act
    result = _account_credentials_path("x", account)

    # Assert
    assert result == "secrets/x_main.json"


def test_account_credentials_path_uses_cookies_field_for_playwright_platforms():
    # Arrange
    account = AccountPlaywright(
        id="main", cookies="secrets/cookies/toutiao_main.json"
    )

    # Act
    result = _account_credentials_path("toutiao", account)

    # Assert
    assert result == "secrets/cookies/toutiao_main.json"
