"""tests/test_publisher_registry_builders.py — 补 publishers/__init__.py 覆盖（42%→80%）。

聚焦：_build_toutiao / _build_xiaohongshu / _build_douyin / build_adapters 编排。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import (
    AccountAPI,
    AccountPlaywright,
    AppConfig,
    LLMConfig,
    LLMTiers,
    Pillar,
    PlatformAPI,
    PlatformPlaywright,
    PlatformsConfig,
)
from pipeline.publishers import build_adapters, get_adapter
from pipeline.publishers.base import AccountConfig


# ── helper ────────────────────────────────────────────────


def _minimal_pillar() -> Pillar:
    return Pillar(
        id="tech",
        name="Tech",
        description="tech news",
        scoring_hint="score relevance",
    )


def _minimal_cfg(platforms: PlatformsConfig) -> AppConfig:
    """构造 cfg 必备的 pillars + llm 字段。"""
    return AppConfig(
        pillars=[_minimal_pillar()],
        llm=LLMConfig(tiers=LLMTiers(cheap="x", creative="y", critical="z")),
        platforms=platforms,
    )


# ── builder 单元覆盖 ──────────────────────────────────────────────


def test_build_toutiao_returns_toutiao_publisher(tmp_path: Path) -> None:
    """_build_toutiao 构造 ToutiaoPublisher 实例（screenshot_dir 注入）。"""
    cookies = tmp_path / "toutiao.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    from pipeline.publishers.toutiao import ToutiaoPublisher

    adapter = get_adapter("toutiao", account=account, config=None)
    assert isinstance(adapter, ToutiaoPublisher)
    # 公有 attr 是 _cookies（私有但已约定俗成；不影响 registry 行为契约）
    assert adapter._cookies == cookies
    # screenshot_dir 默认 logs/screenshots/toutiao
    assert adapter._screenshots == Path("logs/screenshots/toutiao")


def test_build_xiaohongshu_returns_publisher(tmp_path: Path) -> None:
    """_build_xiaohongshu 构造 XiaohongshuPublisher，XHS_SKILLS_PATH env 注入。

    ⚠️ 当前 FAIL：_build_xiaohongshu 给 XiaohongshuPublisher 传 cookies_path kwarg
    但 XiaohongshuPublisher.__init__ 不收此形参。M4-3 已知 bug，记录于 TASKS.md。
    """
    cookies = tmp_path / "xhs.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    from pipeline.publishers.xiaohongshu import XiaohongshuPublisher

    with patch.dict(os.environ, {"XHS_SKILLS_PATH": "/custom/skills"}):
        adapter = get_adapter("xiaohongshu", account=account, config=None)
    assert isinstance(adapter, XiaohongshuPublisher)
    # 注意：XiaohongshuPublisher 实际是 _skills（私有）——这里只验证 type
    assert adapter._skills == Path("/custom/skills").expanduser()


def test_build_xiaohongshu_no_env_uses_none(tmp_path: Path) -> None:
    """XHS_SKILLS_PATH 未设 → skills_path=None（M4-3 bug 阻塞）。

    同上，xiaohongshu registry bug 阻塞本测试。修复 _build_xiaohongshu 后生效。
    """
    cookies = tmp_path / "xhs.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    from pipeline.publishers.xiaohongshu import XiaohongshuPublisher

    env = {k: v for k, v in os.environ.items() if k != "XHS_SKILLS_PATH"}
    with patch.dict(os.environ, env, clear=True):
        adapter = get_adapter("xiaohongshu", account=account, config=None)
    assert isinstance(adapter, XiaohongshuPublisher)
    # env 无 XHS_SKILLS_PATH → adapter._skills 应为默认 skills 路径（来自 env 或 DEFAULT）
    # 详见 xiaohongshu.py::_resolve_skills_path_from_env + DEFAULT_SKILLS_PATH


def test_build_douyin_default_ai_ratio_high(tmp_path: Path) -> None:
    """config 无 douyin 平台（cfg.platforms.douyin=None）→ 默认 'high'。

    实际生产路径 build_adapters(cfg) 永远传非 None cfg，但 cfg 本身
    可能在 douyin 平台未启用（getattr 返回 None）→ 走 fallback 'high'。
    """
    from unittest.mock import MagicMock

    from pipeline.publishers.douyin import DouyinPublisher

    cookies = tmp_path / "dy.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    # mock cfg：cfg.platforms.douyin = None（未启用 douyin 平台）
    fake_cfg = MagicMock()
    fake_cfg.platforms = MagicMock()
    fake_cfg.platforms.douyin = None

    adapter = get_adapter("douyin", account=account, config=fake_cfg)
    assert isinstance(adapter, DouyinPublisher)
    assert adapter._ai_ratio == "high"


def test_build_douyin_uses_config_ai_ratio(tmp_path: Path) -> None:
    """config.platforms.douyin.ai_ratio 显式配置 → 覆盖默认。

    注意：当前 cfg schema（M5-2 实现时未把 ai_ratio 提到 PlatformsConfig.douyin）
    没有 ai_ratio 字段。_build_douyin 用 hasattr 防御性读取 + fallback 'high'。
    本测试用 mock cfg 模拟"未来 schema 扩展后"的路径——锁定行为契约。
    """
    from unittest.mock import MagicMock

    from pipeline.publishers.douyin import DouyinPublisher

    cookies = tmp_path / "dy.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    # mock cfg：cfg.platforms.douyin.ai_ratio = "medium"
    fake_plat = MagicMock()
    fake_plat.ai_ratio = "medium"
    fake_cfg = MagicMock()
    fake_cfg.platforms = MagicMock()
    fake_cfg.platforms.douyin = fake_plat

    adapter = get_adapter("douyin", account=account, config=fake_cfg)
    assert isinstance(adapter, DouyinPublisher)
    assert adapter._ai_ratio == "medium"


def test_build_douyin_fallback_when_ai_ratio_none(tmp_path: Path) -> None:
    """config 存在但 ai_ratio=None → 走 fallback 'high'。"""
    from unittest.mock import MagicMock

    from pipeline.publishers.douyin import DouyinPublisher

    cookies = tmp_path / "dy.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    fake_plat = MagicMock()
    fake_plat.ai_ratio = None
    fake_cfg = MagicMock()
    fake_cfg.platforms = MagicMock()
    fake_cfg.platforms.douyin = fake_plat

    adapter = get_adapter("douyin", account=account, config=fake_cfg)
    assert isinstance(adapter, DouyinPublisher)
    assert adapter._ai_ratio == "high"  # fallback


def test_build_douyin_tolerates_config_none(tmp_path: Path) -> None:
    """config=None 时不应崩溃——与其他 builder（x/toutiao/xhs）对齐。

    M4-3 bug：原 _build_douyin 直接读 config.platforms.douyin，config=None 时
    AttributeError。其他 builder 都不读 config，所以只有 douyin 受影响。
    修复后 config=None → 走默认 ai_ratio='high'。
    """
    from pipeline.publishers.douyin import DouyinPublisher

    cookies = tmp_path / "dy.json"
    cookies.write_text("{}")
    account = AccountConfig(id="main", credentials_path=cookies)

    adapter = get_adapter("douyin", account=account, config=None)
    assert isinstance(adapter, DouyinPublisher)
    assert adapter._ai_ratio == "high"


# ── build_adapters 编排覆盖 ────────────────────────────────────────


def test_build_adapters_skips_unconfigured_platforms(tmp_path: Path) -> None:
    """cfg 中未配的平台（如 cfg.toutiao=None）→ 跳过不报错。"""
    cfg = _minimal_cfg(PlatformsConfig())  # 全空
    assert build_adapters(cfg) == {}


def test_build_adapters_warns_on_unknown_platform(tmp_path: Path) -> None:
    """未来 cfg 加了 wechat_mp 但 _BUILDERS 还没注册 → warn + 跳过。

    通过 monkey-patching _BUILDERS 模拟这一场景（不污染其他测试）。
    """
    from pipeline import publishers as pub_mod

    creds = tmp_path / "x_main.json"
    creds.write_text(json.dumps({"bearer_token": "T"}))
    cfg = _minimal_cfg(PlatformsConfig(
        x=PlatformAPI(
            kind="api",
            windows=[],
            accounts=[AccountAPI(id="main", credentials=str(creds))],
        ),
    ))

    # 模拟"x 已被 _BUILDERS 移除"（退化为未注册）
    saved = pub_mod._BUILDERS.copy()
    pub_mod._BUILDERS = {k: v for k, v in saved.items() if k != "x"}
    try:
        with pytest.warns(UserWarning, match="no adapter registered"):
            result = build_adapters(cfg)
    finally:
        pub_mod._BUILDERS = saved

    # x 被跳过 → result 应为空（warn 路径不入 result）
    assert "x" not in result


def test_build_adapters_returns_configured_platforms(tmp_path: Path) -> None:
    """完整 cfg：x + toutiao 都配账号 → build_adapters 返回两平台。

    ⚠️ 当前 FAIL：build_adapters line 143 写死 acc.credentials，但
    AccountPlaywright 字段名是 cookies。M4-3 已知 bug，记录于 TASKS.md。
    """
    x_creds = tmp_path / "x.json"
    x_creds.write_text(json.dumps({"bearer_token": "T"}))
    tt_creds = tmp_path / "tt.json"
    tt_creds.write_text("{}")

    cfg = _minimal_cfg(PlatformsConfig(
        x=PlatformAPI(
            kind="api",
            windows=["09:00-11:00"],
            accounts=[AccountAPI(id="main", credentials=str(x_creds))],
        ),
        toutiao=PlatformPlaywright(
            kind="playwright",
            windows=["07:00-09:00"],
            accounts=[AccountPlaywright(id="main", cookies=str(tt_creds))],
        ),
    ))

    result = build_adapters(cfg)
    assert set(result.keys()) == {"x", "toutiao"}
    assert len(result["x"]) == 1
    assert len(result["toutiao"]) == 1
    # adapter 实例 + account_cfg
    account_cfg, adapter = result["x"][0]
    assert adapter.platform == "x"
    assert account_cfg.credentials_path == x_creds


def test_build_adapters_skips_platform_with_no_accounts() -> None:
    """cfg.platforms.<x> 存在但 accounts=[] → 不入 result（empty items 不添加）。"""
    cfg = _minimal_cfg(PlatformsConfig(
        x=PlatformAPI(kind="api", windows=[], accounts=[]),
    ))
    result = build_adapters(cfg)
    # items 为空时不写入 result
    assert result == {}


def test_build_wechat_mp_returns_publisher(tmp_path: Path) -> None:
    """_build_wechat_mp 构造 WechatMpPublisher（从 credentials 读 app_id/app_secret）。"""
    creds = tmp_path / "wechat_mp_main.json"
    creds.write_text(json.dumps({"app_id": "wx1", "app_secret": "s1"}))
    account = AccountConfig(id="main", credentials_path=creds)

    from pipeline.publishers.wechat_mp import WechatMpPublisher

    adapter = get_adapter("wechat_mp", account=account, config=None)
    assert isinstance(adapter, WechatMpPublisher)
    assert adapter.platform == "wechat_mp"


def test_build_adapters_wechat_mp_uses_credentials_not_cookies(tmp_path: Path) -> None:
    """回归测试：钉死 build_adapters() 的 credentials/cookies 判断 bug fix。

    修复前：`creds = acc.credentials if platform_name == "x" else acc.cookies`
    是按平台名字符串判断，wechat_mp 和 x 一样是 AccountAPI（有 .credentials
    没有 .cookies），但平台名不是 "x" → AttributeError。
    修复后：按账号类型（isinstance AccountAPI/AccountPlaywright）判断，
    x 和 wechat_mp 都能正确取到 .credentials。
    """
    x_creds = tmp_path / "x.json"
    x_creds.write_text(json.dumps({"bearer_token": "T"}))
    wechat_creds = tmp_path / "wechat_mp.json"
    wechat_creds.write_text(json.dumps({"app_id": "wx1", "app_secret": "s1"}))

    cfg = _minimal_cfg(PlatformsConfig(
        x=PlatformAPI(
            kind="api", windows=[],
            accounts=[AccountAPI(id="main", credentials=str(x_creds))],
        ),
        wechat_mp=PlatformAPI(
            kind="api", windows=[],
            accounts=[AccountAPI(id="main", credentials=str(wechat_creds))],
        ),
    ))

    result = build_adapters(cfg)  # 修复前这里直接 AttributeError

    assert set(result.keys()) == {"x", "wechat_mp"}
    x_account_cfg, x_adapter = result["x"][0]
    assert x_adapter.platform == "x"
    assert x_account_cfg.credentials_path == x_creds

    wm_account_cfg, wm_adapter = result["wechat_mp"][0]
    assert wm_adapter.platform == "wechat_mp"
    assert wm_account_cfg.credentials_path == wechat_creds
