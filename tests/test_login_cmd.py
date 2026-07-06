"""M4-3 `pipeline.run login <platform> <account>` 命令单元测试。

测试契约（HARD_PARTS §2 决策 1）：
- 头条：开有头 chromium → 让人扫码 → 保存 storage_state
- 小红书：提示用户用 XiaohongshuSkills 自带方式登录 → 创建占位 JSON
- 凭据文件权限 chmod 600（HARD_PARTS §9）
- secrets/cookies/<platform>_<account>.json 路径约定

CI 不开真实 chromium（无 GUI）；Playwright 调用通过注入函数替换。
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.publishers.base import PublishError
from pipeline.publishers.login_cmd import (
    DEFAULT_COOKIES_DIR,
    login_toutiao,
    login_xiaohongshu,
    run_login,
)


# ── 顶层分发 ────────────────────────────────────────────────


def test_run_login_dispatches_to_toutiao(tmp_path: Path, monkeypatch) -> None:
    """run_login('toutiao', 'main') → login_toutiao。"""
    monkeypatch.chdir(tmp_path)  # 让 secrets/cookies 落到 tmp_path
    called: dict = {}

    def fake_toutiao(account, **kw):
        called["fn"] = "toutiao"
        called["account"] = account
        return tmp_path / "toutiao_main.json"

    import pipeline.publishers.login_cmd as lc
    monkeypatch.setitem(
        lc._PLATFORM_LOGIN_DISPATCH, "toutiao", fake_toutiao,
    )
    out = run_login("toutiao", "main")
    assert called["fn"] == "toutiao"
    assert called["account"] == "main"
    assert out == tmp_path / "toutiao_main.json"


def test_run_login_dispatches_to_xiaohongshu(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called: dict = {}

    def fake_xhs(account, **kw):
        called["fn"] = "xiaohongshu"
        called["account"] = account
        return tmp_path / "xiaohongshu_main.json"

    import pipeline.publishers.login_cmd as lc
    monkeypatch.setitem(
        lc._PLATFORM_LOGIN_DISPATCH, "xiaohongshu", fake_xhs,
    )
    out = run_login("xiaohongshu", "main")
    assert called["fn"] == "xiaohongshu"
    assert out == tmp_path / "xiaohongshu_main.json"


def test_run_login_unknown_platform_raises() -> None:
    with pytest.raises(PublishError, match="login not implemented"):
        run_login("weibo", "main")


# ── 头条（注入 Playwright） ─────────────────────────────────


def test_login_toutiao_saves_storage_state(
    tmp_path: Path, monkeypatch,
) -> None:
    """注入 fake Playwright：模拟页面跳转出登录路径 → 验证 storage_state 落盘。"""
    monkeypatch.chdir(tmp_path)

    fake_state = {
        "cookies": [{"name": "sessionid", "value": "real", "domain": ".toutiao.com"}],
        "origins": [],
    }

    # 模拟 Playwright 上下文管理器
    fake_context = MagicMock()
    fake_context.storage_state.return_value = fake_state
    fake_context.new_page.return_value = MagicMock()

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    # wait_for_url 第一次调用 → 抛 TimeoutError 模拟「未跳转」前的瞬态；
    # 测试只需让 login_toutiao 至少走一次 wait_for_url 即可验证结构
    fake_page = fake_context.new_page.return_value
    fake_page.wait_for_url.return_value = None
    fake_page.goto.return_value = MagicMock(status=200)

    fake_p = MagicMock()
    fake_p.chromium.launch.return_value = fake_browser
    # sync_playwright() 是 contextmanager
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
        out_path = login_toutiao("main", timeout_s=5)

    # 1. 文件存在 + 内容正确
    assert out_path.exists()
    assert out_path.name == "toutiao_main.json"
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded == fake_state

    # 2. 权限 600（HARD_PARTS §9；Windows 跳过）
    if os.name == "posix":
        mode = stat.S_IMODE(out_path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_login_toutiao_timeout_raises_publish_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """wait_for_url 超时 → PublishError（不抛 raw Playwright TimeoutError）。"""
    monkeypatch.chdir(tmp_path)

    from playwright.sync_api import TimeoutError as PWTimeout

    fake_page = MagicMock()
    fake_page.goto.return_value = MagicMock(status=200)
    fake_page.wait_for_url.side_effect = PWTimeout("simulated")

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_p = MagicMock()
    fake_p.chromium.launch.return_value = fake_browser
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
        with pytest.raises(PublishError, match="login timeout"):
            login_toutiao("main", timeout_s=1)


# ── 小红书（占位 + 提示） ─────────────────────────────────


def test_login_xiaohongshu_creates_placeholder_first_time(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    out_path = login_xiaohongshu("main")
    assert out_path.exists()
    assert out_path.name == "xiaohongshu_main.json"
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert "Placeholder" in loaded.get("_comment", "")
    assert "cookies" in loaded
    # 提示信息打印到 stdout
    captured = capsys.readouterr()
    assert "XiaohongshuSkills" in captured.out


def test_login_xiaohongshu_does_not_overwrite_existing(
    tmp_path: Path, monkeypatch,
) -> None:
    """已存在 cookie 文件 → 不覆盖（保留用户/工具写入的真实状态）。"""
    monkeypatch.chdir(tmp_path)
    DEFAULT_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    existing = DEFAULT_COOKIES_DIR / "xiaohongshu_main.json"
    existing.write_text(
        json.dumps({
            "cookies": [{"name": "real_session", "value": "x"}],
            "origins": [],
        }),
        encoding="utf-8",
    )
    out_path = login_xiaohongshu("main")
    # 内容不被覆盖
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert "real_session" in str(loaded["cookies"])


def test_login_xiaohongshu_respects_skills_path_env(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """XHS_SKILLS_PATH 环境变量 → 提示信息含正确路径。"""
    monkeypatch.chdir(tmp_path)
    custom = "/opt/my-xhs-skills"
    monkeypatch.setenv("XHS_SKILLS_PATH", custom)
    login_xiaohongshu("main")
    captured = capsys.readouterr()
    assert custom in captured.out


# ── cmd_login 入口（run.py） ────────────────────────────────


def test_cmd_login_success_returns_0(tmp_path: Path, monkeypatch) -> None:
    """run.py cmd_login 成功 → exit 0。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "pipeline.publishers.login_cmd.run_login",
        lambda platform, account: tmp_path / f"{platform}_{account}.json",
    )
    from pipeline.run import cmd_login
    args = MagicMock(platform="toutiao", account="main")
    rc = cmd_login(args)
    assert rc == 0


def test_cmd_login_failure_returns_1(tmp_path: Path, monkeypatch, capsys) -> None:
    """run.py cmd_login 失败 → exit 1 + stderr 提示。"""
    monkeypatch.chdir(tmp_path)

    def boom(platform, account):
        raise PublishError("simulated playwright missing")

    monkeypatch.setattr(
        "pipeline.publishers.login_cmd.run_login", boom,
    )
    from pipeline.run import cmd_login
    args = MagicMock(platform="toutiao", account="main")
    rc = cmd_login(args)
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.err


# ── secrets/cookies 路径约定 ────────────────────────────────


def test_default_cookies_dir_constant() -> None:
    """secrets/cookies/ 是 secrets/ 的子目录，gitignore 应已覆盖。"""
    assert DEFAULT_COOKIES_DIR == Path("secrets/cookies")