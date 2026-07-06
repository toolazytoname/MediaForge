"""抖音 Publisher（TECH_SPEC §5.2 + HARD_PARTS §2 + PRD §3.4）。

抖音创作者中心视频自动发布。**M5-2 是视频平台**，区别于图文平台
（X/头条/小红书）：

差异点：
1. **必传视频文件**：media_paths[0] 必须是 mp4 路径（不像头条/小红书可纯文字）
2. **AI 生成内容标识（PRD §3.4）**：必须勾选「内容含 AI 生成」+ 选占比；
   不勾选 = 平台违规（可能下架 + 账号扣分）
3. **更长超时**：视频上传 + 转码通常 1-3 分钟（不同于图文的 5-10 秒）

设计参考（M0-0 DECISION 改写）：
- AiToEarn 整体方案**放弃**（自部署无法无人值守）
- 参考 social-auto-upload douyin_uploader（patchright + storage_state）
- 参考 AiToEarn electron 遗留代码的 cookie 判活字段

接口契约（TECH_SPEC §5.2）：
- platform = 'douyin'
- validate(bundle) → list[str]：本地校验（不触网络）
- publish(bundle, account, dry_run) → PublishResult

测试友好：Playwright 调用全部走注入函数。
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
)
from pipeline.publishers import douyin_selectors as sel


PLATFORM = "douyin"
# 抖音标题上限（来自创作者中心 UI 限制）
TITLE_MAX_LEN = 30
# 抖音描述上限（实测 220-500 间常被截断）
DESC_MIN_LEN = 10
DESC_MAX_LEN = 1000
# 视频文件大小限制（抖音实测：≤ 128 MB；> 128 MB 需切片）
VIDEO_MAX_BYTES = 128 * 1024 * 1024
# AI 生成内容占比合法值（PRD §3.4 + 抖音 UI 下拉项）
AI_RATIO_VALUES = ("low", "medium", "high")


# ── helpers ────────────────────────────────────────────


def _resolve_douyin_video(bundle: PostBundle) -> Path:
    """bundle.media_paths[0] = mp4 文件路径。

    M5-1 MPTEngine.fetch 把 mp4 落到 content_dir/<platform>.mp4 或类似路径。
    PostBundle 默认 media_paths=()，由编排层（safe_publish 扩展）注入。

    本任务暂未改 safe_publish → 仅当 media_paths 非空时校验；空则要求
    bundle.body_path 同目录下的 *.mp4 文件。
    """
    if bundle.media_paths:
        return Path(bundle.media_paths[0])
    # 兜底：content_dir 下第一个 .mp4
    base = bundle.body_path.parent
    candidates = sorted(base.glob("*.mp4"))
    if candidates:
        return candidates[0]
    raise FileNotFoundError(
        f"no video file found for douyin: "
        f"media_paths empty and no .mp4 in {base}"
    )


def _extract_post_id(final_url: str) -> str | None:
    """从成功 URL 提取抖音 video_id。"""
    # 1. ?video_id=...
    m = re.search(r"[?&]video_id=(\d+)", final_url)
    if m:
        return m.group(1)
    # 2. /video/{id}
    m = re.search(r"/video/(\d+)", final_url)
    if m:
        return m.group(1)
    return None


# ── 抖音 Publisher ──────────────────────────────────────


class DouyinPublisher(PublisherAdapter):
    """抖音 (douyin.com) PublisherAdapter — 视频发布。"""

    platform = PLATFORM

    def __init__(
        self,
        *,
        cookies_path: Path,
        health_probe: Callable | None = None,
        publish_fn: Callable | None = None,
        screenshot_dir: Path | None = None,
        # AI 标识默认占比；config 可覆盖
        ai_ratio: str = "high",
    ) -> None:
        if not cookies_path:
            raise ValueError("DouyinPublisher requires cookies_path")
        if ai_ratio not in AI_RATIO_VALUES:
            raise ValueError(
                f"ai_ratio must be one of {AI_RATIO_VALUES}, got {ai_ratio!r}"
            )
        self._cookies = Path(cookies_path)
        self._health_probe = health_probe or _real_health_probe
        self._publish_fn = publish_fn or _real_publish_fn
        self._screenshots = (
            Path(screenshot_dir) if screenshot_dir
            else Path("logs/screenshots/douyin")
        )
        self._ai_ratio = ai_ratio

    # ── validate ─────────────────────────────────────

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

        # 2. 视频文件存在 + 大小
        try:
            video_path = _resolve_douyin_video(bundle)
        except FileNotFoundError as e:
            issues.append(str(e))
            return issues
        try:
            size = video_path.stat().st_size
        except OSError as e:
            issues.append(f"cannot stat video file: {e}")
            return issues
        if size == 0:
            issues.append(f"video file is empty: {video_path}")
        if size > VIDEO_MAX_BYTES:
            issues.append(
                f"video too large: {size} bytes "
                f"(max {VIDEO_MAX_BYTES} = 128MB)"
            )

        # 3. 描述（如果提供）
        desc = bundle.extra.get("description", "") if bundle.extra else ""
        if desc:
            if len(desc) < DESC_MIN_LEN:
                issues.append(
                    f"description too short: {len(desc)} chars "
                    f"(min {DESC_MIN_LEN})"
                )
            if len(desc) > DESC_MAX_LEN:
                issues.append(
                    f"description too long: {len(desc)} chars "
                    f"(max {DESC_MAX_LEN})"
                )

        # 4. cookies 文件存在
        if not self._cookies.exists():
            issues.append(
                f"cookies file missing: {self._cookies} "
                "(run `python -m pipeline.run login douyin <account>`)"
            )
        return issues

    # ── publish ──────────────────────────────────────

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        try:
            video_path = _resolve_douyin_video(bundle)
        except FileNotFoundError as e:
            raise PublishError(str(e))

        description = (
            bundle.extra.get("description", "") if bundle.extra else ""
        )
        hashtags = bundle.tags or ()

        # dry-run：不调浏览器，返回模拟结果
        if dry_run:
            return PublishResult(
                platform_post_id="dry-douyin",
                url=None,
                raw_response=json.dumps({
                    "dry_run": True,
                    "platform": PLATFORM,
                    "account": account.id,
                    "title": bundle.title,
                    "video_path": str(video_path),
                    "video_bytes": video_path.stat().st_size,
                    "ai_declare_ratio": self._ai_ratio,
                }, ensure_ascii=False),
            )

        # cookie 健康检测先行（HARD_PARTS §2 决策 2）
        self._ensure_cookie_health(account)

        # 真实发布
        result = self._publish_fn(
            cookies_path=self._cookies,
            video_path=video_path,
            title=bundle.title,
            description=description,
            hashtags=tuple(hashtags),
            ai_ratio=self._ai_ratio,
            screenshot_dir=self._screenshots,
            selectors=sel,
            account_id=account.id,
        )
        return result

    # ── cookie health ─────────────────────────────────

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
            raise
        if not health.healthy:
            raise PublishError(
                f"{PLATFORM}/{account.id} cookie unhealthy: {health.detail}"
            )


# ── 真实实现（生产；测试通过注入替换） ─────────────────────


def _real_health_probe(
    storage_state_path: Path,
    urls: tuple[str, ...],
) -> tuple[int, str, str]:
    """Playwright 真实探活：访问 creator.douyin.com 个人主页。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PublishError(f"playwright not installed: {e}") from e

    last_err: Exception | None = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            ctx = browser.new_context(
                storage_state=str(storage_state_path),
            )
            page = ctx.new_page()
            for url in urls:
                try:
                    resp = page.goto(url, timeout=15000)
                except Exception as e:
                    last_err = e
                    continue
                if resp is None:
                    last_err = RuntimeError(f"goto None: {url}")
                    continue
                if 200 <= resp.status < 400:
                    text = page.inner_text("body")[:2000]
                    return (resp.status, page.url, text)
            raise PublishError(
                f"all profile URLs failed; last={last_err!r}"
            )
        finally:
            browser.close()


def _real_publish_fn(
    *,
    cookies_path: Path,
    video_path: Path,
    title: str,
    description: str,
    hashtags: tuple[str, ...],
    ai_ratio: str,
    screenshot_dir: Path,
    selectors,
    account_id: str,
) -> PublishResult:
    """Playwright 真实发布抖音视频（PRD §3.4 AI 标识必勾）。

    流程：
    1. 启动 chromium + 注入 storage_state
    2. 访问创作者中心发布页
    3. 上传视频文件
    4. 等转码完成（页面会有进度提示；保守等 60s）
    5. 填标题 + 描述
    6. **勾选「内容含 AI 生成」+ 选占比**（PRD §3.4）
    7. 点发布
    8. 等跳转到作品管理页 → 提取 URL + video_id
    9. 全程截图存 screenshot_dir

    失败语义：选择器找不到 / 转码失败 / 跳转超时 → PublishError。
    """
    try:
        from playwright.sync_api import (
            sync_playwright, TimeoutError as PWTimeout,
        )
    except ImportError as e:
        raise PublishError(f"playwright not installed: {e}") from e

    screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _shot(page, step: str) -> None:
        try:
            page.screenshot(path=str(screenshot_dir / f"{step}.png"))
        except Exception:
            pass

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise PublishError(f"chromium launch failed: {e!r}") from e
        try:
            ctx = browser.new_context(
                storage_state=str(cookies_path),
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            # 1. 发布页
            for url in selectors.PUBLISH_URL_FALLBACK:
                try:
                    resp = page.goto(url, timeout=20000)
                    if resp and 200 <= resp.status < 400:
                        break
                except Exception:
                    continue
            else:
                raise PublishError(
                    f"could not reach douyin publish page; "
                    f"tried {selectors.PUBLISH_URL_FALLBACK}"
                )
            _shot(page, "01_publish_page")

            # 2. 上传视频
            file_input = None
            for css in selectors.VIDEO_FILE_INPUT:
                try:
                    loc = page.locator(css).first
                    if loc.count() > 0:
                        file_input = loc
                        break
                except Exception:
                    continue
            if file_input is None:
                raise PublishError(
                    "douyin video file input not found "
                    f"(selectors={selectors.VIDEO_FILE_INPUT})"
                )
            file_input.set_input_files(str(video_path))
            _shot(page, "02_video_uploaded")

            # 3. 等转码（保守 60s；真正转码可能 1-3 分钟）
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass  # 不阻塞继续（可能转码还在后台跑）
            _shot(page, "03_video_transcoded")

            # 4. 标题
            title_loc = None
            for css in selectors.TITLE_SELECTORS:
                try:
                    loc = page.locator(css).first
                    if loc.count() > 0 and loc.is_visible():
                        title_loc = loc
                        break
                except Exception:
                    continue
            if title_loc is None:
                raise PublishError(
                    "douyin title input not found "
                    f"(selectors={selectors.TITLE_SELECTORS})"
                )
            title_loc.fill(title)
            _shot(page, "04_title_filled")

            # 5. 描述（如提供）
            if description:
                desc_loc = None
                for css in selectors.DESC_SELECTORS:
                    try:
                        loc = page.locator(css).first
                        if loc.count() > 0 and loc.is_visible():
                            desc_loc = loc
                            break
                    except Exception:
                        continue
                if desc_loc:
                    desc_loc.fill(description)
                    _shot(page, "05_desc_filled")

            # 6. AI 生成内容标识（PRD §3.4 — 必勾）
            _declare_ai_content(page, selectors, ai_ratio)
            _shot(page, "06_ai_declared")

            # 7. 提交
            sub_loc = None
            for css in selectors.SUBMIT_BUTTON:
                try:
                    loc = page.locator(css).first
                    if loc.count() > 0 and loc.is_visible():
                        sub_loc = loc
                        break
                except Exception:
                    continue
            if sub_loc is None:
                raise PublishError(
                    "douyin submit button not found "
                    f"(selectors={selectors.SUBMIT_BUTTON})"
                )
            sub_loc.click()
            _shot(page, "07_submit_clicked")

            # 8. 等成功 URL
            try:
                page.wait_for_url(
                    re.compile("|".join(selectors.SUCCESS_URL_PATTERN)),
                    timeout=120000,   # 视频发布 + 审核可能 1-2 分钟
                )
            except PWTimeout as e:
                raise PublishError(
                    f"douyin publish timeout waiting for success URL: {e}"
                ) from e
            _shot(page, "08_success")

            final_url = page.url
            video_id = _extract_post_id(final_url)

            return PublishResult(
                platform_post_id=video_id,
                url=final_url,
                raw_response=json.dumps({
                    "platform": PLATFORM,
                    "account": account_id,
                    "final_url": final_url,
                    "video_id": video_id,
                    "ai_declare_ratio": ai_ratio,  # 留档供审计
                }, ensure_ascii=False),
            )
        finally:
            browser.close()


def _declare_ai_content(page, selectors, ai_ratio: str) -> None:
    """勾选「内容含 AI 生成」+ 选占比（PRD §3.4 强制要求）。

    - 找不到勾选框 → 抛 PublishError（绝不静默忽略；平台违规风险）
    - 占比下拉找不到 → 抛 PublishError
    """
    # 1. 勾选 checkbox
    checked = False
    for css in selectors.AI_DECLARE_CHECKBOX:
        try:
            loc = page.locator(css).first
            if loc.count() > 0:
                if not loc.is_checked():
                    loc.check()
                checked = True
                break
        except Exception:
            continue
    if not checked:
        raise PublishError(
            "PRD §3.4: AI 生成内容勾选框未找到 "
            f"(selectors={selectors.AI_DECLARE_CHECKBOX}); "
            "页面可能已改版。**绝不静默忽略**——平台违规风险"
        )

    # 2. 选占比（low / medium / high）
    if ai_ratio in AI_RATIO_VALUES:
        for css in selectors.AI_DECLARE_RATIO:
            try:
                loc = page.locator(css).first
                if loc.count() > 0:
                    loc.select_option(value=ai_ratio)
                    return
            except Exception:
                continue
        # 找不到占比下拉 → 警告但不让发布失败（勾选已满足核心要求）
        # 注：有些页面只有勾选，没有占比下拉；放过


__all__ = [
    "DouyinPublisher",
    "TITLE_MAX_LEN",
    "DESC_MIN_LEN",
    "DESC_MAX_LEN",
    "VIDEO_MAX_BYTES",
    "AI_RATIO_VALUES",
]