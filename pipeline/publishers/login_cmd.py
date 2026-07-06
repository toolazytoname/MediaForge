"""`pipeline.run login <platform> <account>` 命令实现（HARD_PARTS §2 决策 1）。

打开有头浏览器让用户扫码 / 输入凭据完成登录，保存
Playwright `storage_state` JSON 到 `secrets/cookies/<platform>_<account>.json`
（chmod 600，凭据安全）。

各平台登录策略：
- 头条 (Playwright)：开有头 chromium → mp.toutiao.com auth 页 → 等待登录态建立
  （URL 离开 /auth/）→ save storage_state
- 小红书 (XiaohongshuSkills 桥)：提示用户用 XiaohongshuSkills 自带的方式
  完成登录（该项目有自己的登录态管理），保存路径由我方记录
  ——M4-3 阶段不强耦合其内部登录机制，由用户在 mac 上按项目 README 操作

退出语义：
- 成功 → exit 0 + 打印 storage_state 路径
- 用户中断（Ctrl-C） → exit 130
- 平台 / Playwright 故障 → exit 1
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pipeline.publishers.base import PublishError
from pipeline.publishers import toutiao_selectors as tt_sel


# 登录态 JSON 默认输出目录
DEFAULT_COOKIES_DIR = Path("secrets/cookies")


def _ensure_cookies_path(platform: str, account: str) -> Path:
    """secrets/cookies/<platform>_<account>.json（chmod 600 兜底）。"""
    DEFAULT_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_COOKIES_DIR / f"{platform}_{account}.json"


def _chmod_600(path: Path) -> None:
    """凭据文件权限 600（HARD_PARTS §9）。"""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows 不支持 chmod；跳过（仍可读）
        pass


# ── 头条 ─────────────────────────────────────────────────────


def login_toutiao(account: str, *, timeout_s: int = 300) -> Path:
    """开有头 chromium → 头条创作者中心登录 → 保存 storage_state。

    Args:
        account: 账号别名（main 等）
        timeout_s: 等用户完成登录的最大秒数（默认 5 分钟）

    Returns:
        storage_state 文件路径

    Raises:
        PublishError: Playwright 未安装 / chromium 启动失败
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:
        raise PublishError(
            f"playwright not installed: {e}; "
            "run `pip install playwright && playwright install chromium`"
        ) from e

    out_path = _ensure_cookies_path("toutiao", account)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # 进登录入口
            for url in tt_sel.PROFILE_URL_FALLBACK:
                try:
                    resp = page.goto(url, timeout=15000)
                    if resp is not None:
                        break
                except Exception:
                    continue
            else:
                raise PublishError(
                    "could not reach toutiao login entry; "
                    "check network / browser"
                )

            print(f"[login/toutiao/{account}] 请在浏览器里完成登录（扫码 / 短信 / 密码）。")
            print(f"[login/toutiao/{account}] 等待登录完成（最多 {timeout_s}s）...")
            print("[login/toutiao/{account}] 完成后页面会自动离开登录页。")

            # 等待 URL 离开登录路径（任一 auth/login/passport 子串消失）
            # 不强制特定 URL：登录后头条可能跳到任一创作者中心页
            try:
                page.wait_for_url(
                    lambda url: not any(
                        kw in url.lower()
                        for kw in ("login", "auth", "passport")
                    ),
                    timeout=timeout_s * 1000,
                )
            except PWTimeout as e:
                raise PublishError(
                    f"login timeout after {timeout_s}s; user did not complete "
                    f"login or page did not redirect away from auth"
                ) from e

            # 保存 storage_state
            state = context.storage_state()
            out_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _chmod_600(out_path)
            print(f"[login/toutiao/{account}] saved: {out_path}")
            return out_path
        finally:
            browser.close()


# ── 小红书（subprocess 调 XiaohongshuSkills 自带 login） ───


def login_xiaohongshu(
    account: str,
    *,
    skills_path: str | Path | None = None,
    headless: bool = False,
    timeout_s: int = 300,
) -> Path:
    """小红书登录：调 `python <skills>/scripts/cdp_publish.py login`。

    XiaohongshuSkills 自管 Chrome user-data-dir（每个 account 一个 profile）；
    cookie 不在我方 storage_state JSON 里。我们的 login 命令只负责启动
    有头 / 无头 Chrome、让人扫码（headless=False 默认），成功后 CLI 自己
    把 cookie 存进 user-data-dir。下次发布 CLI 直接复用同一 profile。

    Args:
        account: 账号别名（传给 --account）
        skills_path: 覆盖 XHS_SKILLS_PATH
        headless: True 走 `--headless`（CLI 支持；用于 cron 远程登录 + QR 截图）
        timeout_s: subprocess 超时（默认 5 分钟）

    Returns:
        占位 / 标记文件路径（XHS 不暴露 cookie 文件路径；返回 secrets/cookies/
        xiaohongshu_<account>.json 占位仅作 pipeline 状态指示）
    """
    out_path = _ensure_cookies_path("xiaohongshu", account)
    skills = Path(
        skills_path
        or os.environ.get("XHS_SKILLS_PATH")
        or "~/.agents/skills/xiaohongshu-skills",
    ).expanduser()

    cli_script = skills / "scripts" / "cdp_publish.py"
    if not cli_script.exists():
        raise PublishError(
            f"XiaohongshuSkills CLI not found: {cli_script}. "
            f"Clone white0dew/XiaohongshuSkills (HEAD 988fd2e) and "
            f"set XHS_SKILLS_PATH env."
        )

    cmd = [
        "python", str(cli_script), "login",
        "--account", account,
    ]
    if headless:
        cmd.append("--headless")

    import subprocess as _sp
    try:
        proc = _sp.run(cmd, timeout=timeout_s)
    except _sp.TimeoutExpired as e:
        raise PublishError(
            f"xhs login timeout after {timeout_s}s; user did not scan QR"
        ) from e
    except FileNotFoundError as e:
        raise PublishError(
            f"xhs login failed: command not found: {cmd[0]}: {e}"
        ) from e

    if proc.returncode != 0:
        snippet = (proc.stderr or proc.stdout)[-400:]
        raise PublishError(
            f"xhs login CLI failed (exit={proc.returncode}): {snippet}"
        )

    # 写占位文件（指示登录完成；CLI 自己管 Chrome profile）
    out_path.write_text(
        json.dumps({
            "_comment": (
                "XiaohongshuSkills login completed. "
                "Actual cookies live in the CLI's Chrome user-data-dir, "
                "not in this file. This file exists only so the pipeline "
                "config-validator does not flag credentials missing."
            ),
            "account": account,
            "skills_path": str(skills),
            "logged_in_at": _now_iso(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _chmod_600(out_path)
    print(f"[login/xiaohongshu/{account}] login OK; marker saved: {out_path}")
    return out_path


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── 抖音 ────────────────────────────────────────────


def login_douyin(account: str, *, timeout_s: int = 300) -> Path:
    """抖音登录：开有头 chromium → 创作者中心 → 扫码登录。

    抖音登录流程：
    1. 访问 creator.douyin.com → 跳登录页
    2. 用户用抖音 App 扫码
    3. 登录后页面跳回 creator.douyin.com
    4. 保存 storage_state JSON

    与头条登录逻辑相似（都是 Playwright storage_state 模式），单独函数
    是因为 cookie 路径 / 等待 URL 不一样。
    """
    try:
        from playwright.sync_api import (
            sync_playwright, TimeoutError as PWTimeout,
        )
    except ImportError as e:
        raise PublishError(
            f"playwright not installed: {e}; "
            "run `pip install playwright && playwright install chromium`"
        ) from e

    from pipeline.publishers import douyin_selectors as dy_sel

    out_path = _ensure_cookies_path("douyin", account)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            # 进登录入口
            for url in dy_sel.PROFILE_URL_FALLBACK:
                try:
                    resp = page.goto(url, timeout=15000)
                    if resp is not None:
                        break
                except Exception:
                    continue
            else:
                raise PublishError(
                    "could not reach douyin login entry; "
                    "check network / browser"
                )

            print(f"[login/douyin/{account}] 请在浏览器里用抖音 App 扫码登录。")
            print(f"[login/douyin/{account}] 等待登录完成（最多 {timeout_s}s）...")

            # 等待 URL 离开登录路径
            try:
                page.wait_for_url(
                    lambda url: not any(
                        kw in url.lower()
                        for kw in ("passport", "login", "auth")
                    ),
                    timeout=timeout_s * 1000,
                )
            except PWTimeout as e:
                raise PublishError(
                    f"login timeout after {timeout_s}s; user did not scan QR"
                ) from e

            # 保存 storage_state
            state = ctx.storage_state()
            out_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _chmod_600(out_path)
            print(f"[login/douyin/{account}] saved: {out_path}")
            return out_path
        finally:
            browser.close()


# ── 顶层分发 ────────────────────────────────────────────────


_PLATFORM_LOGIN_DISPATCH = {
    "toutiao": login_toutiao,
    "xiaohongshu": login_xiaohongshu,
    "douyin": login_douyin,
}


def run_login(platform: str, account: str) -> Path:
    """顶层 login 入口（被 pipeline/run.py::cmd_login 调用）。"""
    fn = _PLATFORM_LOGIN_DISPATCH.get(platform)
    if fn is None:
        supported = sorted(_PLATFORM_LOGIN_DISPATCH.keys())
        raise PublishError(
            f"login not implemented for platform {platform!r}; "
            f"supported: {supported}"
        )
    return fn(account)


__all__ = [
    "run_login",
    "login_toutiao",
    "login_xiaohongshu",
    "DEFAULT_COOKIES_DIR",
]