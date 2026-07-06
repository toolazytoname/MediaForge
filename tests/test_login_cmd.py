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
    _chmod_600,
    _ensure_cookies_path,
    login_douyin,
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


# ── 小红书（真 subprocess 调 cdp_publish.py login） ──────


def test_login_xiaohongshu_runs_cdp_publish_login(
    tmp_path: Path, monkeypatch,
) -> None:
    """注入 fake cdp_publish.py login：CLI exit 0 → marker 落盘。"""
    monkeypatch.chdir(tmp_path)
    skills = tmp_path / "xhs-skills"
    (skills / "scripts").mkdir(parents=True)
    (skills / "scripts" / "cdp_publish.py").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    out_path = login_xiaohongshu("main", skills_path=skills)
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert "XiaohongshuSkills login completed" in loaded["_comment"]
    assert loaded["account"] == "main"
    assert str(skills) in loaded["skills_path"]


def test_login_xiaohongshu_passes_account_and_headless(
    tmp_path: Path, monkeypatch,
) -> None:
    """--account / --headless 参数透传。"""
    monkeypatch.chdir(tmp_path)
    skills = tmp_path / "xhs-skills"
    (skills / "scripts").mkdir(parents=True)
    (skills / "scripts" / "cdp_publish.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
        encoding="utf-8",
    )

    received: dict = {}

    def fake_run(cmd, **kw):
        received["cmd"] = cmd
        received["timeout"] = kw.get("timeout")
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)
    login_xiaohongshu("alt", skills_path=skills, headless=True, timeout_s=42)
    cmd = received["cmd"]
    assert cmd[0] == "python"
    assert "cdp_publish.py" in cmd[1]
    assert "login" in cmd
    assert "--account" in cmd
    assert "alt" in cmd
    assert "--headless" in cmd
    assert received["timeout"] == 42


def test_login_xiaohongshu_missing_cli_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    """skills_path 不存在 → PublishError，不调 subprocess。"""
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "no-such"
    with pytest.raises(PublishError, match="CLI not found"):
        login_xiaohongshu("main", skills_path=bad)


def test_login_xiaohongshu_cli_nonzero_exit_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    """CLI exit != 0 → PublishError（带 stderr 摘要）。"""
    monkeypatch.chdir(tmp_path)
    skills = tmp_path / "xhs-skills"
    (skills / "scripts").mkdir(parents=True)
    (skills / "scripts" / "cdp_publish.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
        encoding="utf-8",
    )

    def fake_run(cmd, **kw):
        class _R:
            returncode = 2
            stdout = ""
            stderr = "cookie expired"
        return _R()

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)
    with pytest.raises(PublishError, match="CLI failed"):
        login_xiaohongshu("main", skills_path=skills)


def test_login_xiaohongshu_timeout_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    """subprocess 超时 → PublishError。"""
    monkeypatch.chdir(tmp_path)
    skills = tmp_path / "xhs-skills"
    (skills / "scripts").mkdir(parents=True)
    (skills / "scripts" / "cdp_publish.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8",
    )

    import subprocess as _sp
    def fake_run(cmd, **kw):
        raise _sp.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr(_sp, "run", fake_run)
    with pytest.raises(PublishError, match="login timeout"):
        login_xiaohongshu("main", skills_path=skills, timeout_s=1)


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


# ── 内部 helper ─────────────────────────────────────


def test_ensure_cookies_path_creates_dir(tmp_path: Path, monkeypatch) -> None:
    """_ensure_cookies_path 自动 mkdir secrets/cookies/，文件名 = <p>_<acc>.json。"""
    monkeypatch.chdir(tmp_path)
    p = _ensure_cookies_path("toutiao", "main")
    # 路径是相对路径（DEFAULT_COOKIES_DIR = "secrets/cookies"）；CWD 已 chdir 到 tmp_path
    assert p.name == "toutiao_main.json"
    assert p.parent.name == "cookies"
    assert p.parent.parent.name == "secrets"
    assert (tmp_path / "secrets" / "cookies").is_dir()


def test_chmod_600_posix(tmp_path: Path) -> None:
    """POSIX 系统 _chmod_600 设为 0o600。"""
    if os.name != "posix":
        pytest.skip("posix-only")
    f = tmp_path / "x.json"
    f.write_text("{}")
    _chmod_600(f)
    mode = stat.S_IMODE(f.stat().st_mode)
    assert mode == 0o600


# ── 抖音（注入 Playwright） ─────────────────────────────────


def test_login_douyin_saves_storage_state(
    tmp_path: Path, monkeypatch,
) -> None:
    """注入 fake Playwright：模拟扫码完成 → storage_state 落盘。"""
    monkeypatch.chdir(tmp_path)

    fake_state = {
        "cookies": [{"name": "sessionid", "value": "v", "domain": ".douyin.com"}],
        "origins": [],
    }

    fake_page = MagicMock()
    fake_page.wait_for_url.return_value = None
    fake_page.goto.return_value = MagicMock(status=200)

    fake_context = MagicMock()
    fake_context.storage_state.return_value = fake_state
    fake_context.new_page.return_value = fake_page

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_p = MagicMock()
    fake_p.chromium.launch.return_value = fake_browser
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
        out_path = login_douyin("main", timeout_s=5)

    assert out_path.exists()
    assert out_path.name == "douyin_main.json"
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded == fake_state
    if os.name == "posix":
        mode = stat.S_IMODE(out_path.stat().st_mode)
        assert mode == 0o600


def test_login_douyin_timeout_raises_publish_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """扫码超时 → PublishError。"""
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
            login_douyin("main", timeout_s=1)


def test_login_douyin_no_entry_url_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    """所有 PROFILE_URL_FALLBACK 都不通 → PublishError。"""
    monkeypatch.chdir(tmp_path)

    fake_page = MagicMock()
    fake_page.goto.return_value = None  # 每次 goto 返回 None

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_p = MagicMock()
    fake_p.chromium.launch.return_value = fake_browser
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
        with pytest.raises(PublishError, match="could not reach douyin"):
            login_douyin("main", timeout_s=1)


def test_login_douyin_chromium_launch_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    """chromium 启动失败 → PublishError（捕获所有 Exception）。"""
    monkeypatch.chdir(tmp_path)

    fake_p = MagicMock()
    fake_p.chromium.launch.side_effect = RuntimeError("no chrome")
    fake_p.__enter__ = lambda s: fake_p
    fake_p.__exit__ = lambda s, *a: None

    with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
        with pytest.raises(PublishError, match="chromium launch failed"):
            login_douyin("main", timeout_s=1)


# ── run_login 入口（douyin dispatch） ─────────────────────


def test_run_login_dispatches_to_douyin(tmp_path: Path, monkeypatch) -> None:
    """run_login('douyin', 'main') → login_douyin。"""
    monkeypatch.chdir(tmp_path)
    called: dict = {}

    def fake_dy(account, **kw):
        called["fn"] = "douyin"
        called["account"] = account
        return tmp_path / "douyin_main.json"

    import pipeline.publishers.login_cmd as lc
    monkeypatch.setitem(
        lc._PLATFORM_LOGIN_DISPATCH, "douyin", fake_dy,
    )
    out = run_login("douyin", "main")
    assert called["fn"] == "douyin"
    assert called["account"] == "main"
    assert out == tmp_path / "douyin_main.json"