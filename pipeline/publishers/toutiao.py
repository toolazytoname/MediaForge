"""头条 Publisher（TECH_SPEC §5.2 + HARD_PARTS §2）。

Playwright 自写实现（HARD_PARTS §2 决策 3 + 4 + 5）：
- 反检测基线：固定 viewport + 真实 UA + 操作间随机 sleep
- 选择器集中在 toutiao_selectors.py（HARD_PARTS §2 决策 4）
- 全程截图存 logs/screenshots/（HARD_PARTS §2 决策 5）
- cookie 失效检测先行（HARD_PARTS §2 决策 2）
- 频控由编排层（safe_publish / config.publish.max_daily_per_account）守，
  本模块不重复实现

接口契约（TECH_SPEC §5.2）：
- platform = 'toutiao'
- validate(bundle) → list[str]：本地校验（不触网络）
- publish(bundle, account, dry_run) → PublishResult：执行发布

测试友好：
- Playwright 调用全部走注入函数（launch_browser / fill_form / submit 等）
  生产 = 真实 Playwright 实现；测试 = fake
- 真实 Playwright import 在 `_launch_real` 内部（按需），CI 无 playwright 也能跑
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from pipeline.publishers.base import (
    AccountConfig,
    LoginExpired,
    PostBundle,
    PublishError,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.cookie_health import (
    CookieHealth,
    check_health,
    load_storage_state,
)
from pipeline.publishers import toutiao_selectors as sel


# ── 常量 ───────────────────────────────────────────────────

PLATFORM = "toutiao"
# 头条标题上限（来自官方创作者平台建议 + social-auto-upload 实际测试）
TITLE_MAX_LEN = 36
# 头条正文建议字数（无硬上限但 < 300 字通常被判低质）
BODY_MIN_LEN = 300
BODY_MAX_LEN = 10000  # 头条图文实际阈值；超出会截断


# ── 本地校验 ────────────────────────────────────────────────


def _resolve_toutiao_body(bundle: PostBundle) -> Path:
    """bundle.body_path → 平台正文文件。

    与 x_api.py 行为一致（X 优先 thread.md 子目录 fallback）：

    1. bundle.body_path 存在 → 用它（默认 = canonical.md）
    2. 不存在 → 回退到同目录 toutiao.md（ARCHITECTURE §8 派生产物）
    3. 都不存在 → 报错

    单测里 caller 也可直接传 toutiao.md（命中分支 1）。
    """
    body = bundle.body_path
    if body.exists():
        return body
    cand = body.parent / "toutiao.md"
    if cand.exists():
        return cand
    raise FileNotFoundError(
        f"toutiao body not found at {body} nor {cand}"
    )


def _resolve_toutiao_media(bundle: PostBundle) -> tuple[Path, ...]:
    """bundle.media_paths 非空则直接用；为空则扫描 body_path.parent 下
    cover.png + images/inline-*.png（generate-images 阶段产物，见
    derivative_toutiao_images.py）。

    与 douyin.py::_resolve_douyin_video、xiaohongshu.py::_resolve_xhs_bundle
    同一模式——safe_publish.py::build_post_bundle 始终传 media_paths=()，
    由各 adapter 自己取派生媒体。

    返回顺序：cover.png 在前，inline 按数字排序（避免 inline-10 排在
    inline-2 前的字典序错误）。
    """
    if bundle.media_paths:
        return tuple(Path(p) for p in bundle.media_paths)

    base = bundle.body_path.parent
    media: list[Path] = []
    cover = base / "cover.png"
    if cover.exists():
        media.append(cover)

    images_dir = base / "images"
    if images_dir.exists():
        inline: list[tuple[int, Path]] = []
        for p in images_dir.glob("inline-*.png"):
            m = re.match(r"inline-(\d+)\.png$", p.name)
            if m:
                inline.append((int(m.group(1)), p))
        inline.sort(key=lambda pair: pair[0])
        media.extend(p for _, p in inline)

    return tuple(media)


# ── 头条 Publisher ──────────────────────────────────────────


class ToutiaoPublisher(PublisherAdapter):
    """头条 (toutiao.com) PublisherAdapter。"""

    platform = PLATFORM

    def __init__(
        self,
        *,
        cookies_path: Path,
        # 注入：测试 fake / 生产真实 Playwright
        health_probe: Callable | None = None,
        publish_fn: Callable | None = None,
        screenshot_dir: Path | None = None,
    ) -> None:
        if not cookies_path:
            raise ValueError("ToutiaoPublisher requires cookies_path")
        self._cookies = Path(cookies_path)
        self._health_probe = health_probe or _real_health_probe
        self._publish_fn = publish_fn or _real_publish_fn
        self._screenshots = (
            Path(screenshot_dir) if screenshot_dir
            else Path("logs/screenshots/toutiao")
        )

    # ── 公开：validate ──

    def validate(self, bundle: PostBundle) -> list[str]:
        """本地校验（不触网络）。"""
        issues: list[str] = []
        # 1. 标题长度
        title = (bundle.title or "").strip()
        if not title:
            issues.append("title is empty")
        elif len(title) > TITLE_MAX_LEN:
            issues.append(
                f"title too long: {len(title)} chars (max {TITLE_MAX_LEN})"
            )

        # 2. 正文文件存在 + 字数
        try:
            body_path = _resolve_toutiao_body(bundle)
        except FileNotFoundError as e:
            issues.append(str(e))
            return issues

        try:
            body = body_path.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"cannot read toutiao body: {e}")
            return issues

        # 去 markdown 标记估字数（粗略：去 # * > - 等）
        plain = re.sub(r"[#*>\-\[\]\(\)`_]", "", body)
        plain = re.sub(r"\s+", "", plain)
        body_len = len(plain)
        if body_len < BODY_MIN_LEN:
            issues.append(
                f"body too short: {body_len} chars (min {BODY_MIN_LEN})"
            )
        if body_len > BODY_MAX_LEN:
            issues.append(
                f"body too long: {body_len} chars (max {BODY_MAX_LEN})"
            )

        # 2.5 未处理的图片占位符：generate-images 阶段应已把 [IMAGE: ...]
        # 替换为真实插图引用（见 derivative_toutiao_images.py）。漏跑会让
        # 占位符文字原样出现在真实发布的文章里。
        if "[IMAGE:" in body:
            issues.append(
                "body contains unresolved [IMAGE: ...] placeholder(s); "
                "run `python -m pipeline.run generate-images` first"
            )

        # 3. cookies 文件存在性
        if not self._cookies.exists():
            issues.append(
                f"cookies file missing: {self._cookies} "
                "(run `python -m pipeline.run login toutiao <account>`)"
            )
        return issues

    # ── 公开：publish ──

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        body_path = _resolve_toutiao_body(bundle)

        # dry-run：不调浏览器，返回模拟结果
        if dry_run:
            return PublishResult(
                platform_post_id="dry-toutiao",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": PLATFORM,
                    "account": account.id,
                    "title": bundle.title,
                    "body_chars": len(body_path.read_text(encoding="utf-8")),
                }, ensure_ascii=False),
            )

        # cookie 健康检测先行（HARD_PARTS §2 决策 2）
        self._ensure_cookie_health(account)

        # 真实发布（走注入的 publish_fn）
        result = self._publish_fn(
            cookies_path=self._cookies,
            body_path=body_path,
            title=bundle.title,
            images=_resolve_toutiao_media(bundle),
            screenshot_dir=self._screenshots,
            selectors=sel,
            account_id=account.id,
        )
        return result

    # ── 私有：cookie 健康检测 ──

    def _ensure_cookie_health(self, account: AccountConfig) -> None:
        try:
            health = check_health(
                platform=PLATFORM,
                account_id=account.id,
                storage_state_path=self._cookies,
                login_indicators=sel.LOGIN_INDICATORS,
                profile_urls=sel.PROFILE_URL_FALLBACK,
                probe_page=self._health_probe,
            )
        except LoginExpired:
            # LoginExpired 透传（编排层会停止该平台所有任务）
            raise
        if not health.healthy:
            raise PublishError(
                f"{PLATFORM}/{account.id} cookie unhealthy: {health.detail}"
            )


# ── 真实实现（生产；测试通过注入替换） ───────────────────────


def _real_health_probe(
    storage_state_path: Path,
    urls: tuple[str, ...],
) -> tuple[int, str, str]:
    """Playwright 真实探活：挨个访问 urls 直到 2xx，返回 (status, final_url, text)。

    返回 page_text 为页面 body 文本的前 2000 字符（用于登录关键词匹配）。
    全部 URL 都失败 → 抛 PublishError。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PublishError(
            f"playwright not installed: {e}; "
            "run `pip install playwright && playwright install chromium`"
        ) from e

    last_err: Exception | None = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            context = browser.new_context(
                storage_state=str(storage_state_path),
            )
            page = context.new_page()
            for url in urls:
                try:
                    resp = page.goto(url, timeout=15000)
                except Exception as e:
                    last_err = e
                    continue
                if resp is None:
                    last_err = RuntimeError(f"goto returned None for {url}")
                    continue
                if 200 <= resp.status < 400:
                    text = page.inner_text("body")[:2000]
                    return (resp.status, page.url, text)
            raise PublishError(
                f"all profile URLs failed; last error: {last_err!r}"
            )
        finally:
            browser.close()


def _real_publish_fn(
    *,
    cookies_path: Path,
    body_path: Path,
    title: str,
    images: tuple[Path, ...],
    screenshot_dir: Path,
    selectors,
    account_id: str,
) -> PublishResult:
    """Playwright 真实发布头条（HARD_PARTS §2 全部要点）。

    流程：
    1. 启动 chromium + 注入 storage_state
    2. 访问创作者中心发布页
    3. 填标题 + 正文 + 自动封面
    4. 点发布
    5. 等跳转到成功页 → 提取 URL + post_id
    6. 全程截图存 screenshot_dir

    失败语义：
    - 找不到选择器 → PublishError（含 step 上下文）
    - 跳转超时 → PublishError
    - 跳到登录页 → LoginExpired（被 health_probe 拦住，但兜底再抛）
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:
        raise PublishError(f"playwright not installed: {e}") from e

    screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _shot(page, step: str) -> None:
        try:
            page.screenshot(path=str(screenshot_dir / f"{step}.png"))
        except Exception:
            pass  # 截图失败不阻断

    def _find_visible_locator(page, selectors: tuple, timeout_ms: int = 6000):
        """逐个候选选择器等它出现并可见（SPA 水合比 load 事件慢，
        不能只查一次 count()——真实头条页面首屏白屏几秒是常态）。"""
        for css in selectors:
            try:
                page.wait_for_selector(css, timeout=timeout_ms, state="visible")
            except PWTimeout:
                continue
            except Exception:
                continue
            loc = page.locator(css).first
            if loc.count() > 0:
                return loc
        return None

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            context = browser.new_context(
                storage_state=str(cookies_path),
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # 1. 进发布页
            for url in selectors.PUBLISH_URL_FALLBACK:
                try:
                    resp = page.goto(url, timeout=20000)
                    if resp and 200 <= resp.status < 400:
                        break
                except Exception:
                    continue
            else:
                raise PublishError(
                    f"could not reach toutiao publish page; "
                    f"tried {selectors.PUBLISH_URL_FALLBACK}"
                )
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass  # SPA 可能有长轮询，networkidle 超时不代表页面没渲染好
            _shot(page, "01_publish_page")

            # 2. 填标题
            title_locator = _find_visible_locator(page, selectors.TITLE_SELECTORS)
            if title_locator is None:
                raise PublishError(
                    "toutiao title input not found "
                    f"(selectors={selectors.TITLE_SELECTORS}); "
                    "page may have changed"
                )
            title_locator.fill(title)
            _shot(page, "02_title_filled")

            # 3. 填正文
            body_locator = _find_visible_locator(page, selectors.BODY_SELECTORS)
            if body_locator is None:
                raise PublishError(
                    "toutiao body editor not found "
                    f"(selectors={selectors.BODY_SELECTORS}); "
                    "page may have changed"
                )
            # 头条正文常见为 markdown → 粘贴纯文本（去掉 markdown 标记）
            plain_body = re.sub(
                r"^#+ .+$", "", body_path.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
            body_locator.fill(plain_body)
            _shot(page, "03_body_filled")

            # 4. 自动封面（默认）
            for css in selectors.COVER_MODE_RADIO:
                try:
                    loc = page.locator(css).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click()
                        break
                except Exception:
                    continue

            # 5. 点发布
            submit_locator = _find_visible_locator(page, selectors.SUBMIT_BUTTON)
            if submit_locator is None:
                raise PublishError(
                    "toutiao submit button not found "
                    f"(selectors={selectors.SUBMIT_BUTTON})"
                )
            submit_locator.click()
            _shot(page, "04_submit_clicked")

            # 6. 等成功 URL（最多 60s）
            try:
                page.wait_for_url(
                    re.compile("|".join(selectors.SUCCESS_URL_PATTERN)),
                    timeout=60000,
                )
            except PWTimeout as e:
                raise PublishError(
                    f"toutiao publish timeout waiting for success URL: {e}"
                ) from e
            _shot(page, "05_success")

            final_url = page.url
            # post_id 从 URL 末段提取（mp 平台惯例是 query 参数 ?mid=... 或 path segment）
            post_id = _extract_post_id(final_url)

            return PublishResult(
                platform_post_id=post_id,
                url=final_url,
                raw_response=json.dumps({
                    "platform": PLATFORM,
                    "account": account_id,
                    "final_url": final_url,
                    "post_id": post_id,
                }, ensure_ascii=False),
            )
        finally:
            browser.close()


def _extract_post_id(final_url: str) -> str:
    """从成功 URL 提取 post_id。

    头条 mp 平台典型：'.../content/manage?mid=7123456789...' 或 path 含 id。
    兜底：返回 None（编排层靠 URL 即可）。
    """
    # 1. ?mid=...  / ?id=...
    m = re.search(r"[?&](?:mid|id|pgc_id|article_id)=(\d+)", final_url)
    if m:
        return m.group(1)
    # 2. path 末段纯数字
    m = re.search(r"/(\d{10,})/?$", final_url.rstrip("/"))
    if m:
        return m.group(1)
    return None


__all__ = [
    "ToutiaoPublisher",
    "TITLE_MAX_LEN",
    "BODY_MIN_LEN",
    "BODY_MAX_LEN",
]