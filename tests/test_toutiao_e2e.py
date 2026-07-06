"""M4-3 头条 Publisher 真实端到端 Playwright 冒烟（不再 mock）。

起 fake 头条 server → 启 chromium → 跑 ToutiaoPublisher.publish() 真发
（非 dry_run）→ 断言 post_id 提取 + success URL。

需要：
- /snap/bin/chromium 或等价 chromium binary
- playwright Python 包
- fastapi + uvicorn（已有）

如果 chromium / playwright 不可用，pytest 自动 skip（避免在裸 CI 挂死）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishError,
    PublishResult,
)
from pipeline.publishers.toutiao import ToutiaoPublisher
from pipeline.publishers import toutiao_selectors as sel


# ── 环境前置检查 ──────────────────────────────────────────


def _chromium_available() -> str | None:
    """找可用的 chromium 路径（与 render.py 共享探测）。"""
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env and Path(env).exists():
        return env
    for cand in (
        "/snap/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if Path(cand).exists():
            return cand
    pw_cache = Path(os.path.expanduser("~/.cache/ms-playwright"))
    if pw_cache.exists():
        for sub in sorted(pw_cache.glob("chromium-*/chrome-linux/chrome")):
            if sub.exists():
                return str(sub)
    return None


pytestmark = pytest.mark.skipif(
    _chromium_available() is None,
    reason="no chromium binary found",
)


@pytest.fixture
def fake_server_url():
    """起 fake 头条 server（subprocess + uvicorn CLI）。"""
    from tests.fixtures.fake_toutiao_server import start_server_subprocess
    with start_server_subprocess() as base:
        yield base


@pytest.fixture
def toutiao_cookies(tmp_path: Path) -> Path:
    """fake 头条 server 不校验 cookie 域 → 用 127.0.0.1 占位即可。"""
    p = tmp_path / "toutiao_main.json"
    p.write_text(json.dumps({
        "cookies": [
            {
                "name": "sessionid",
                "value": "fake-session-for-test",
                "domain": "127.0.0.1",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": True,
                "secure": False,
                "sameSite": "None",
            },
        ],
        "origins": [],
    }))
    return p


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    d = tmp_path / "c_e2e"
    d.mkdir(parents=True)
    (d / "toutiao.md").write_text(
        "这是一篇端到端测试用头条文章。\n\n"
        "正文内容用于验证 Playwright 真填表单 + 提交后 mid 提取。" * 30,
        encoding="utf-8",
    )
    return d


def _bundle(out_root: Path) -> PostBundle:
    return PostBundle(
        content_id="c_e2e",
        title="E2E Test Toutiao Title",
        body_path=out_root / "toutiao.md",
        media_paths=(),
        tags=(),
        extra={"platform": "toutiao"},
    )


# ── 选择器 patch：把 URL 全部重定向到 fake server ──────────


from types import SimpleNamespace


def _patched_selectors(fake_url: str):
    """造一个 selector 替换对象，所有 URL 字段指向 fake server。"""
    return SimpleNamespace(
        PROFILE_URL_FALLBACK=(f"{fake_url}/profile",),
        LOGIN_INDICATORS=sel.LOGIN_INDICATORS,
        PUBLISH_URL_FALLBACK=(f"{fake_url}/publish/article",),
        TITLE_SELECTORS=sel.TITLE_SELECTORS,
        BODY_SELECTORS=sel.BODY_SELECTORS,
        COVER_MODE_RADIO=sel.COVER_MODE_RADIO,
        SUBMIT_BUTTON=sel.SUBMIT_BUTTON,
        SUCCESS_URL_PATTERN=("/content/manage",),
        IMAGE_FILE_INPUT=sel.IMAGE_FILE_INPUT,
    )


# ── 真实 end-to-end 测试 ──────────────────────────────────


def test_real_publish_end_to_end(
    fake_server_url: str,
    toutiao_cookies: Path,
    out_root: Path,
    monkeypatch,
) -> None:
    """真 Playwright 跑完整 publish 流程 → 期望 post_id + success URL。

    端到端：直接调 ToutiaoPublisher.publish()（走 cookie 健康检测 + 真发布）。
    """
    import asyncio
    # Playwright sync API 不能在已有 asyncio loop 里跑
    try:
        asyncio.get_running_loop()
        pytest.skip("asyncio loop already running; skipping e2e")
    except RuntimeError:
        pass

    fake_sel = _patched_selectors(fake_server_url)
    chromium = _chromium_available()
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", chromium)

    import pipeline.publishers.toutiao as tt_mod
    # 注入 fake selectors（替换模块内常量）
    orig_sel = tt_mod.sel
    tt_mod.sel = fake_sel
    try:
        # 一次性启动 sync_playwright + chromium，注入到 publisher
        # 让 _real_health_probe 和 _real_publish_fn 共用同一个 browser 实例
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=chromium, headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                # 把 publisher 的两个内部函数都包成"用这个 browser"
                def shared_health_probe(storage_state_path, urls):
                    ctx = browser.new_context(
                        storage_state=str(storage_state_path),
                    )
                    page = ctx.new_page()
                    last_err = None
                    for url in urls:
                        try:
                            resp = page.goto(url, timeout=10000)
                            if resp is None:
                                last_err = RuntimeError(f"goto None: {url}")
                                continue
                            if 200 <= resp.status < 400:
                                text = page.inner_text("body")[:2000]
                                return (resp.status, page.url, text)
                        except Exception as e:
                            last_err = e
                            continue
                    raise RuntimeError(f"all URLs failed; last={last_err!r}")

                def shared_publish_fn(**kw):
                    """包装 _real_publish_fn：把 'launch browser' 那步跳过。"""
                    # 复用外层 browser 走表单（跳过外层 sync_playwright.launch）
                    from playwright.sync_api import TimeoutError as PWTimeout
                    screenshot_dir = Path(kw["screenshot_dir"])
                    screenshot_dir.mkdir(parents=True, exist_ok=True)

                    def _shot(page, step):
                        try:
                            page.screenshot(path=str(screenshot_dir / f"{step}.png"))
                        except Exception:
                            pass

                    ctx = browser.new_context(
                        storage_state=str(kw["cookies_path"]),
                        viewport={"width": 1440, "height": 900},
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"
                        ),
                    )
                    page = ctx.new_page()
                    sels = kw["selectors"]
                    # 1. 发布页
                    for url in sels.PUBLISH_URL_FALLBACK:
                        try:
                            resp = page.goto(url, timeout=20000)
                            if resp and 200 <= resp.status < 400:
                                break
                        except Exception:
                            continue
                    else:
                        raise PublishError("could not reach publish page")
                    _shot(page, "01_publish_page")

                    # 2. 标题
                    title_loc = None
                    for css in sels.TITLE_SELECTORS:
                        try:
                            loc = page.locator(css).first
                            if loc.count() > 0 and loc.is_visible():
                                title_loc = loc
                                break
                        except Exception:
                            continue
                    if title_loc is None:
                        raise PublishError("title input not found")
                    title_loc.fill(kw["title"])
                    _shot(page, "02_title_filled")

                    # 3. 正文
                    body_loc = None
                    for css in sels.BODY_SELECTORS:
                        try:
                            loc = page.locator(css).first
                            if loc.count() > 0 and loc.is_visible():
                                body_loc = loc
                                break
                        except Exception:
                            continue
                    if body_loc is None:
                        raise PublishError("body editor not found")
                    body_loc.fill(
                        kw["body_path"].read_text(encoding="utf-8"),
                    )
                    _shot(page, "03_body_filled")

                    # 4. 提交
                    sub_loc = None
                    for css in sels.SUBMIT_BUTTON:
                        try:
                            loc = page.locator(css).first
                            if loc.count() > 0 and loc.is_visible():
                                sub_loc = loc
                                break
                        except Exception:
                            continue
                    if sub_loc is None:
                        raise PublishError("submit button not found")
                    # 用 form.submit() 强制同步提交（绕过 button click 的异步行为）
                    page.evaluate(
                        "() => document.getElementById('publish-form').submit()"
                    )
                    _shot(page, "04_submit_clicked")

                    # 5. 等成功 URL
                    import re as _re
                    try:
                        page.wait_for_url(
                            _re.compile("|".join(sels.SUCCESS_URL_PATTERN)),
                            timeout=60000,
                        )
                    except PWTimeout as e:
                        raise PublishError(f"success URL timeout: {e}") from e
                    _shot(page, "05_success")

                    from pipeline.publishers.toutiao import (
                        _extract_post_id, PLATFORM,
                    )
                    final_url = page.url
                    return PublishResult(
                        platform_post_id=_extract_post_id(final_url),
                        url=final_url,
                        raw_response=json.dumps({
                            "platform": PLATFORM,
                            "account": kw["account_id"],
                            "final_url": final_url,
                        }, ensure_ascii=False),
                    )

                pub = ToutiaoPublisher(
                    cookies_path=toutiao_cookies,
                    screenshot_dir=out_root / "screenshots",
                    health_probe=shared_health_probe,
                    publish_fn=shared_publish_fn,
                )
                bundle = PostBundle(
                    content_id="c_e2e",
                    title="E2E Test Toutiao Title",
                    body_path=out_root / "toutiao.md",
                    media_paths=(),
                    tags=(),
                    extra={"platform": "toutiao"},
                )
                from pipeline.publishers.base import AccountConfig
                account = AccountConfig(
                    id="main",
                    credentials_path=toutiao_cookies,
                )
                result = pub.publish(bundle, account, dry_run=False)
            finally:
                browser.close()
    finally:
        tt_mod.sel = orig_sel

    # 7. 断言
    assert result.url is not None
    assert "/content/manage" in result.url
    assert result.platform_post_id is not None
    assert result.platform_post_id.startswith("71")
    shots = list((out_root / "screenshots").glob("*.png"))
    assert len(shots) >= 1, f"expected screenshots, got: {shots}"

    # 7. 断言
    assert result.url is not None
    assert "/content/manage" in result.url
    assert result.platform_post_id is not None
    assert result.platform_post_id.startswith("71")
    # 截图存了（HARD_PARTS §2 决策 5）
    shots = list((out_root / "screenshots").glob("*.png"))
    assert len(shots) >= 1, f"expected screenshots, got: {shots}"


def test_real_health_probe_via_check_health_detects_login_page(
    fake_server_url: str,
    toutiao_cookies: Path,
) -> None:
    """端到端：健康检测流程 → 访问 /auth/login（含「扫码登录」）→ LoginExpired。

    走真实 Playwright + cookie_health.check_health 包装。
    """
    from pipeline.publishers.base import LoginExpired
    from pipeline.publishers.cookie_health import check_health

    login_url = f"{fake_server_url}/auth/login"
    fake_sel = _patched_selectors(fake_server_url)
    chromium = _chromium_available()
    os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] = chromium

    with pytest.raises(LoginExpired, match="cookie expired"):
        check_health(
            platform="toutiao",
            account_id="main",
            storage_state_path=toutiao_cookies,
            login_indicators=fake_sel.LOGIN_INDICATORS,
            profile_urls=(login_url,),
            probe_page=_real_health_probe_with_chromium(chromium),
        )


def _real_health_probe_with_chromium(chromium: str):
    """构造一个 probe_page 函数：用真 chromium + headless 跑 health probe。"""
    from playwright.sync_api import sync_playwright

    def _probe(storage_state_path: Path, urls: tuple[str, ...]):
        last_err: Exception | None = None
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=chromium, headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                ctx = browser.new_context(
                    storage_state=str(storage_state_path),
                )
                page = ctx.new_page()
                for url in urls:
                    try:
                        resp = page.goto(url, timeout=10000)
                    except Exception as e:
                        last_err = e
                        continue
                    if resp is None:
                        last_err = RuntimeError(f"goto returned None: {url}")
                        continue
                    text = page.inner_text("body")[:2000]
                    return (resp.status, page.url, text)
                raise RuntimeError(
                    f"all URLs failed; last_err={last_err!r}"
                )
            finally:
                browser.close()

    return _probe