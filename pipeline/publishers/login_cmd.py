"""`pipeline.run login <platform> <account>` 命令实现（HARD_PARTS §2 决策 1）。

打开有头浏览器让用户扫码 / 输入凭据完成登录，保存
Playwright `storage_state` JSON 到 `secrets/cookies/<platform>_<account>.json`
（chmod 600，凭据安全）。

各平台登录策略：
- 头条 (Playwright)：开有头 chromium → mp.toutiao.com auth 页 → 等待登录态建立
  （URL 离开 /auth/）→ save storage_state
- 小红书 (XiaohongshuSkills 桥)：subprocess 调 `cdp_publish.py login`
  ——本仓库只启动 CLI，cookie 由其自有 Chrome user-data-dir 管理
- 抖音 (Playwright)：与头条同骨架（仅 selectors + exit_keywords 不同）
  → R7-7 重构后二者共用 `_playwright_login_run` 私有 helper

退出语义：
- 成功 → exit 0 + log_event "saved: ..."
- 用户中断（Ctrl-C） → exit 130
- 平台 / Playwright 故障 → PublishError → exit 1

R7-7：所有 print 替换为 `log_event(stage="login", ref_id=account)`，让 U7-7
前端可通过 logging.Handler 订阅进度。
"""
from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pipeline.publishers.base import PublishError
from pipeline.publishers import toutiao_selectors as tt_sel
from pipeline.utils.log import get_logger, log_event


# 模块级 logger（被 log_event 消费；U7-7 通过 logging.Handler 订阅）
_LOG = get_logger("pipeline.publishers.login")


# 登录态 JSON 默认输出目录
DEFAULT_COOKIES_DIR = Path("secrets/cookies")


@dataclass(frozen=True)
class LoginProfile:
    """Per-platform Playwright 登录骨架配置（R7-7 抽取，消除复制粘贴）。

    仅头条 / 抖音使用——小红书走 subprocess 路径，骨架不同，不并入。

    Attributes:
        platform: 平台标签（用于日志路径 / 错误消息）。
        selectors_module: 平台 selectors 模块，含 `PROFILE_URL_FALLBACK` 常量。
        exit_keywords: URL 不应再包含的子串（任一存在即视为仍在登录态）；
            wait_for_url 会等待 URL 离开所有这些子串。
    """
    platform: str
    selectors_module: ModuleType
    exit_keywords: tuple[str, ...]


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


# ── Playwright 登录骨架（头条 / 抖音共用） ──────────────────


def _playwright_login_run(
    profile: LoginProfile,
    account: str,
    *,
    timeout_s: int = 300,
) -> Path:
    """Playwright 登录骨架：launch → new_context → 遍历 URL fallback → 等待 URL
    离开登录路径 → storage_state 落盘 + chmod 600 → finally browser.close。

    90% 代码从原 `login_toutiao` 搬来；唯一差异由 `LoginProfile` 注入。
    所有进度通过 `log_event(stage="login", ref_id=account)` 发出，便于
    U7-7 Web UI 通过 logging.Handler 订阅。
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:
        raise PublishError(
            f"playwright not installed: {e}; "
            "run `pip install playwright && playwright install chromium`"
        ) from e

    out_path = _ensure_cookies_path(profile.platform, account)

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
            for url in profile.selectors_module.PROFILE_URL_FALLBACK:
                try:
                    resp = page.goto(url, timeout=15000)
                    if resp is not None:
                        break
                except Exception:
                    continue
            else:
                raise PublishError(
                    f"could not reach {profile.platform} login entry; "
                    "check network / browser"
                )

            log_event(
                _LOG, logging.INFO,
                "请在浏览器里完成登录（扫码 / 短信 / 密码）",
                stage="login", ref_id=account,
            )
            log_event(
                _LOG, logging.INFO,
                f"等待登录完成（最多 {timeout_s}s）；完成后页面会自动离开登录页",
                stage="login", ref_id=account,
            )

            # 等待 URL 离开登录路径
            try:
                page.wait_for_url(
                    lambda url: not any(
                        kw in url.lower()
                        for kw in profile.exit_keywords
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
            log_event(
                _LOG, logging.INFO,
                f"saved: {out_path}",
                stage="login", ref_id=account,
            )
            return out_path
        finally:
            browser.close()


# ── 头条 ─────────────────────────────────────────────────────


def login_toutiao(account: str, *, timeout_s: int = 300) -> Path:
    """头条登录 thin wrapper：构造 LoginProfile 调 `_playwright_login_run`。

    Args:
        account: 账号别名（main 等）
        timeout_s: 等用户完成登录的最大秒数（默认 5 分钟）

    Returns:
        storage_state 文件路径

    Raises:
        PublishError: Playwright 未安装 / chromium 启动失败 / 等待超时
    """
    profile = LoginProfile(
        platform="toutiao",
        selectors_module=tt_sel,
        exit_keywords=("login", "auth", "passport"),
    )
    return _playwright_login_run(profile, account, timeout_s=timeout_s)


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
    log_event(
        _LOG, logging.INFO,
        f"login OK; marker saved: {out_path}",
        stage="login", ref_id=account,
    )
    return out_path


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── 抖音 ────────────────────────────────────────────


def login_douyin(account: str, *, timeout_s: int = 300) -> Path:
    """抖音登录 thin wrapper：构造 LoginProfile 调 `_playwright_login_run`。

    抖音登录流程：
    1. 访问 creator.douyin.com → 跳登录页
    2. 用户用抖音 App 扫码
    3. 登录后页面跳回 creator.douyin.com
    4. 保存 storage_state JSON

    与头条共用骨架，仅 selectors 与 exit_keywords 不同（douyin 优先检查
    "passport"）。
    """
    from pipeline.publishers import douyin_selectors as dy_sel
    profile = LoginProfile(
        platform="douyin",
        selectors_module=dy_sel,
        exit_keywords=("passport", "login", "auth"),
    )
    return _playwright_login_run(profile, account, timeout_s=timeout_s)


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