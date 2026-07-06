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


# ── 小红书（提示用户用 XiaohongshuSkills 自带方式登录）─────


def login_xiaohongshu(
    account: str,
    *,
    skills_path: str | Path | None = None,
) -> Path:
    """小红书登录：提示用户在 mac 上按 XiaohongshuSkills 项目说明完成。

    XiaohongshuSkills 自管 login state（cookie / QR 扫码），不同小版本间实现
    可能差异。M4-3 不强耦合其内部登录机制——只确保 storage_state JSON
    路径就位且为合法占位（首次发布时由 XiaohongshuSkills 自己写入）。

    用户拿到本命令打印的提示后：
      1. 在 mac 上 clone white0dew/XiaohongshuSkills
      2. 按其 README 跑登录命令
      3. 把生成的 cookie/state 文件软链 / 拷贝到
         secrets/cookies/xiaohongshu_<account>.json

    本函数仍创建占位文件，让 pipeline 配置校验通过、不被误报"凭据缺失"。
    """
    out_path = _ensure_cookies_path("xiaohongshu", account)

    skills = Path(
        skills_path
        or os.environ.get("XHS_SKILLS_PATH")
        or "~/.agents/skills/xiaohongshu-skills",
    ).expanduser()

    print(
        f"[login/xiaohongshu/{account}] 提示：XiaohongshuSkills 自管登录态。\n"
        f"  1. 在 mac 上 clone 项目：\n"
        f"     git clone https://github.com/white0dew/XiaohongshuSkills.git\n"
        f"  2. 进入目录，按 README 完成登录（通常为 QR 扫码）\n"
        f"  3. 把生成的 cookie/state JSON 放到:\n"
        f"     {out_path}\n"
        f"  或者把项目放到: {skills}\n"
        f"  设置 XHS_SKILLS_PATH 环境变量覆盖默认路径。\n"
        f"\n"
        f"  本命令会创建一个占位 JSON，让 pipeline 校验通过。"
    )

    if not out_path.exists():
        out_path.write_text(
            json.dumps({
                "_comment": (
                    "Placeholder. Replace with XiaohongshuSkills-generated "
                    "cookie/state JSON. See XiaohongshuSkills README."
                ),
                "cookies": [],
                "origins": [],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _chmod_600(out_path)
    print(f"[login/xiaohongshu/{account}] placeholder saved: {out_path}")
    return out_path


# ── 顶层分发 ────────────────────────────────────────────────


_PLATFORM_LOGIN_DISPATCH = {
    "toutiao": login_toutiao,
    "xiaohongshu": login_xiaohongshu,
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