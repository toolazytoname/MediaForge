"""M5-2 抖音 Publisher 真实端到端 Playwright 冒烟。

起 fake 抖音 server → 启 chromium → 走 DouyinPublisher 端到端：
1. 健康检测（访问 /creator-micro/home）
2. 访问发布页
3. 上传视频文件
4. 填标题 + 勾选 AI 生成（PRD §3.4）+ 选占比
5. 提交 → 等成功 URL → 提取 video_id

验证 PRD §3.4（AI 标识）的端到端契约：mock HTML 含
`input[type='checkbox'][data-type='ai-generated']`，
真实 Playwright 必须能勾上。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.publishers.base import (
    AccountConfig, PostBundle, PublishError, PublishResult,
)
from pipeline.publishers.douyin import DouyinPublisher
from pipeline.publishers import douyin_selectors as sel


# ── 环境前置检查 ──────────────────────────────────────────


def _chromium_available() -> str | None:
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env and Path(env).exists():
        return env
    for cand in (
        "/snap/bin/chromium", "/usr/bin/chromium",
        "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
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
    from tests.fixtures.fake_douyin_server import start_server_subprocess
    with start_server_subprocess() as base:
        yield base


@pytest.fixture
def dy_cookies(tmp_path: Path) -> Path:
    p = tmp_path / "douyin_main.json"
    p.write_text(json.dumps({
        "cookies": [{"name": "sessionid", "value": "x", "domain": "127.0.0.1", "path": "/"}],
        "origins": [],
    }))
    return p


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    d = tmp_path / "c_dy_e2e"
    d.mkdir(parents=True)
    # 1MB fake mp4（实测 ftypisom magic）
    (d / "video.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * (1024 * 1024),
    )
    (d / "douyin.md").write_text(
        "这是一段抖音视频描述，介绍 AI 工具使用。",
        encoding="utf-8",
    )
    return d


def _patched_selectors(fake_url: str):
    return SimpleNamespace(
        PROFILE_URL_FALLBACK=(f"{fake_url}/creator-micro/home",),
        LOGIN_INDICATORS=sel.LOGIN_INDICATORS,
        PUBLISH_URL_FALLBACK=(f"{fake_url}/creator-micro/home/upload/video",),
        VIDEO_FILE_INPUT=sel.VIDEO_FILE_INPUT,
        TITLE_SELECTORS=sel.TITLE_SELECTORS,
        DESC_SELECTORS=sel.DESC_SELECTORS,
        AI_DECLARE_CHECKBOX=sel.AI_DECLARE_CHECKBOX,
        AI_DECLARE_RATIO=sel.AI_DECLARE_RATIO,
        SUBMIT_BUTTON=sel.SUBMIT_BUTTON,
        SUCCESS_URL_PATTERN=("/creator-micro/content/manage",),
        HASHTAG_INPUT=sel.HASHTAG_INPUT,
    )


def test_real_douyin_end_to_end(
    fake_server_url: str, dy_cookies: Path, out_root: Path, monkeypatch,
) -> None:
    """真 Playwright 跑完整 publish 流程 → 期望 video_id + PRD §3.4 AI 勾选。"""
    try:
        asyncio.get_running_loop()
        pytest.skip("asyncio loop running")
    except RuntimeError:
        pass

    fake_sel = _patched_selectors(fake_server_url)
    chromium = _chromium_available()
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", chromium)

    import pipeline.publishers.douyin as dy_mod
    orig_sel = dy_mod.sel
    dy_mod.sel = fake_sel

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=chromium, headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                # shared health probe + publish fn
                def shared_probe(storage_state_path, urls):
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
                    from playwright.sync_api import (
                        TimeoutError as PWTimeout,
                    )
                    import re as _re
                    screenshot_dir = Path(kw["screenshot_dir"])
                    screenshot_dir.mkdir(parents=True, exist_ok=True)

                    def _shot(page, step):
                        try:
                            page.screenshot(
                                path=str(screenshot_dir / f"{step}.png"),
                            )
                        except Exception:
                            pass

                    ctx = browser.new_context(
                        storage_state=str(kw["cookies_path"]),
                        viewport={"width": 1440, "height": 900},
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
                        raise PublishError("publish page unreachable")
                    _shot(page, "01_publish")

                    # 2. 上传视频
                    fi = None
                    for css in sels.VIDEO_FILE_INPUT:
                        try:
                            loc = page.locator(css).first
                            if loc.count() > 0:
                                fi = loc
                                break
                        except Exception:
                            continue
                    if fi is None:
                        raise PublishError("video input not found")
                    fi.set_input_files(str(kw["video_path"]))
                    _shot(page, "02_uploaded")

                    # 3. 标题
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
                        raise PublishError("title not found")
                    title_loc.fill(kw["title"])

                    # 4. **PRD §3.4 — AI 勾选**
                    ai_checked = False
                    for css in sels.AI_DECLARE_CHECKBOX:
                        try:
                            loc = page.locator(css).first
                            if loc.count() > 0:
                                if not loc.is_checked():
                                    loc.check()
                                ai_checked = True
                                break
                        except Exception:
                            continue
                    if not ai_checked:
                        raise PublishError(
                            "PRD §3.4: AI 勾选框未命中（页面缺 data-type='ai-generated' 或 fallback）"
                        )
                    _shot(page, "03_ai_checked")

                    # 5. 提交
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
                        raise PublishError("submit not found")
                    sub_loc.click()

                    # 6. 等成功 URL
                    try:
                        page.wait_for_url(
                            _re.compile("|".join(sels.SUCCESS_URL_PATTERN)),
                            timeout=30000,
                        )
                    except PWTimeout as e:
                        raise PublishError(f"success URL timeout: {e}") from e

                    final_url = page.url
                    from pipeline.publishers.douyin import _extract_post_id
                    return PublishResult(
                        platform_post_id=_extract_post_id(final_url),
                        url=final_url,
                        raw_response=json.dumps({
                            "platform": "douyin",
                            "account": kw["account_id"],
                            "final_url": final_url,
                            "ai_declare_ratio": kw["ai_ratio"],
                            "ai_checked": ai_checked,
                        }, ensure_ascii=False),
                    )

                pub = DouyinPublisher(
                    cookies_path=dy_cookies,
                    screenshot_dir=out_root / "screenshots",
                    health_probe=shared_probe,
                    publish_fn=shared_publish_fn,
                    ai_ratio="high",
                )
                bundle = PostBundle(
                    content_id="c_dy_e2e",
                    title="E2E Test Douyin Title",
                    body_path=out_root / "douyin.md",
                    media_paths=(out_root / "video.mp4",),
                    tags=(),
                    extra={"description": "x" * 20},
                )
                account = AccountConfig(
                    id="main",
                    credentials_path=dy_cookies,
                )
                result = pub.publish(bundle, account, dry_run=False)
            finally:
                browser.close()
    finally:
        dy_mod.sel = orig_sel

    # 断言
    assert result.url is not None
    assert "/creator-micro/content/manage" in result.url
    assert result.platform_post_id is not None
    assert result.platform_post_id.startswith("71")
    # AI 勾选留档
    assert '"ai_checked": true' in result.raw_response
    # 截图存了
    shots = list((out_root / "screenshots").glob("*.png"))
    assert len(shots) >= 3, f"expected screenshots, got: {shots}"


def test_real_health_probe_detects_login_page(
    fake_server_url: str, dy_cookies: Path,
) -> None:
    """真实健康检测：page 含 login 关键词 → LoginExpired。

    不依赖 fake server；直接用 page.set_content 注入带关键词的 HTML。
    """
    from pipeline.publishers.base import LoginExpired
    from pipeline.publishers.cookie_health import check_health

    chromium = _chromium_available()
    os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] = chromium

    def fake_probe_with_login_keyword(storage_state_path, urls):
        from playwright.sync_api import sync_playwright
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
                page.set_content(
                    "<html><body><h1>请先登录 后再操作</h1></body></html>"
                )
                text = page.inner_text("body")[:2000]
                # URL 含 login 关键词 → 命中 login_url_keywords
                return (200, "https://creator.douyin.com/login", text)
            finally:
                browser.close()

    with pytest.raises(LoginExpired, match="cookie expired"):
        check_health(
            platform="douyin",
            account_id="main",
            storage_state_path=dy_cookies,
            login_indicators=("请先登录",),
            profile_urls=("about:blank",),
            probe_page=fake_probe_with_login_keyword,
        )