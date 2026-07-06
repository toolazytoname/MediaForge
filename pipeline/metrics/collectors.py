"""表现数据回流 collector（TECH_SPEC §3 metrics 表 + M6-1）。

每个平台一个 collector，统一接口 `collect(publication) -> MetricsSnapshot`。
- X：官方 API v2，GET /2/tweets/{id} 拿 public_metrics
- 头条 / 抖音 / 小红书：创作者后台登录态抓自己作品（**只读自己的数据，合规**）

设计要点：
- Collector 失败 → 返回 None（编排层记 warning，下次再试；不阻塞其他 publication）
- metrics 表允许多次快照（时间序列），天然幂等（HARD_PARTS §5 决策）
- 调用方拿不到数据时**静默重试次日**（cron 自动重跑）
- 不修改 publication 表，只 insert metrics
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pipeline.models import Publication


# ── MetricsSnapshot（不写 DB 的中间表示） ────────────────


@dataclass(frozen=True)
class MetricsSnapshot:
    """单条 publication 单次抓取的指标快照。"""
    publication_id: str
    platform: str
    collected_at: str          # ISO8601 UTC
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    followers_delta: int | None
    raw: str                   # 平台原始响应（JSON / 调试用）


# ── Collector 抽象 ───────────────────────────────────────


class MetricsCollector(ABC):
    """平台 collector 统一接口。"""
    platform: str

    @abstractmethod
    def collect(self, pub: Publication) -> MetricsSnapshot | None:
        """抓取一条 publication 的当前指标。失败返回 None（编排层记录重试）。

        - 不抛异常：网络 / 平台限流 / cookie 失效都包成 None
        - 返回 MetricsSnapshot：含原始 raw（用于审计）
        """


# ── X API v2 collector（最可靠；走官方 REST） ─────────


class XMetricsCollector(MetricsCollector):
    """X API v2 collector：GET /2/tweets/{id} → public_metrics。

    需要 bearer_token（与 X Publisher 共用凭据）。
    限流：free tier 100 次/15min；超限 → None（明天重试）。
    """

    platform = "x"

    def __init__(
        self,
        *,
        bearer_token: str,
        http_get: Callable[..., tuple[int, dict | None]] | None = None,
    ) -> None:
        if not bearer_token:
            raise ValueError("XMetricsCollector requires bearer_token")
        self._token = bearer_token
        self._get = http_get or _real_x_get

    def collect(self, pub: Publication) -> MetricsSnapshot | None:
        if not pub.platform_post_id:
            return None
        url = (
            f"https://api.twitter.com/2/tweets/"
            f"{pub.platform_post_id}"
            f"?tweet.fields=public_metrics"
        )
        try:
            status, payload = self._get(
                url, headers={
                    "Authorization": f"Bearer {self._token}",
                }, timeout=15.0,
            )
        except Exception:
            return None

        if status == 401 or status == 403:
            # bearer_token 失效 → 不重试（用户需手动 refresh）
            return None
        if status == 429:
            # 限流 → 明天重试
            return None
        if status != 200 or not isinstance(payload, dict):
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        metrics = data.get("public_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        try:
            views = int(metrics.get("impression_count", 0))
            likes = int(metrics.get("like_count", 0))
            comments = int(metrics.get("reply_count", 0))
            shares = int(
                metrics.get("retweet_count", 0)
                + metrics.get("quote_count", 0)
            )
        except (TypeError, ValueError):
            return None

        return MetricsSnapshot(
            publication_id=pub.id,
            platform="x",
            collected_at=datetime.now(timezone.utc).isoformat(),
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            followers_delta=None,   # X public_metrics 不含此字段
            raw=json.dumps(payload, ensure_ascii=False)[:4000],
        )


def _real_x_get(url, *, headers=None, timeout=15.0):
    """默认 httpx GET → (status, json|None)。"""
    import httpx
    try:
        resp = httpx.get(url, headers=headers or {}, timeout=timeout)
    except Exception:
        return (0, None)
    if resp.status_code >= 400:
        return (resp.status_code, None)
    try:
        return (resp.status_code, resp.json())
    except ValueError:
        return (resp.status_code, None)


# ── 头条创作者后台 collector（cookie 抓） ─────────────


class ToutiaoMetricsCollector(MetricsCollector):
    """头条创作者后台：登录态访问作品管理页 → 解析阅读/点赞/评论。

    只读自己账号的数据，合规（HARD_PARTS §9 决策 5）。
    **M6-1 阶段实现为**：基础 cookie + Playwright 抓取 + 启发式解析。
    反检测失败 / 选择器改版 → 返回 None，下次重试。
    """

    platform = "toutiao"

    def __init__(
        self,
        *,
        cookies_path: Path,
        probe_fn: Callable[..., dict | None] | None = None,
    ) -> None:
        self._cookies = Path(cookies_path)
        self._probe = probe_fn or _real_toutiao_probe

    def collect(self, pub: Publication) -> MetricsSnapshot | None:
        if not pub.platform_post_id:
            return None
        try:
            data = self._probe(self._cookies, pub.platform_post_id)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return MetricsSnapshot(
            publication_id=pub.id,
            platform="toutiao",
            collected_at=datetime.now(timezone.utc).isoformat(),
            views=_safe_int(data.get("views")),
            likes=_safe_int(data.get("likes")),
            comments=_safe_int(data.get("comments")),
            shares=_safe_int(data.get("shares")),
            followers_delta=_safe_int(data.get("followers_delta")),
            raw=json.dumps(data, ensure_ascii=False)[:4000],
        )


def _real_toutiao_probe(cookies_path: Path, post_id: str) -> dict | None:
    """真实抓头条创作者后台作品数据。

    M6-1 阶段：访问 mp.toutiao.com/content/manage 列表页 → 启发式从 HTML
    提取 post_id 对应的 views/likes/comments。
    **集成时需复核**：头条创作者后台改版时选择器 / 数据结构会变。
    """
    # 实现细节：Playwright 启 chromium + 注入 cookies + 访问作品列表 →
    # 启发式 regex 提取数字。失败 → None（编排层明天重试）。
    # 真实集成时按 HARD_PARTS §2 防腐层原则抽出 selectors。
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    url = "https://mp.toutiao.com/content/manage"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    storage_state=str(cookies_path),
                )
                page = ctx.new_page()
                resp = page.goto(url, timeout=20000)
                if resp is None or resp.status != 200:
                    return None
                html = page.content()
                return _parse_toutiao_manage_html(html, post_id)
            finally:
                browser.close()
    except Exception:
        return None


def _parse_toutiao_manage_html(html: str, post_id: str) -> dict | None:
    """启发式从头条后台 HTML 提取单条作品数据。

    注：HTML 结构会改版；这是 M6-1 阶段占位实现，**集成时按实测修订**。
    """
    # 占位：找含 post_id 的段落 + 临近的数字
    # 真实集成时改为：等 JSON API 端点 / 或者 CSS selector 提取
    pattern = re.compile(
        rf"{re.escape(post_id)}.*?(\d+)",
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    return {
        "views": _safe_int(m.group(1)),
        # likes/comments/shares 留 None（启发式不靠谱）
    }


def _safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── 小红书 / 抖音 collector（subprocess 桥或占位） ─


class XiaohongshuMetricsCollector(MetricsCollector):
    """小红书 metrics：subprocess 调 XiaohongshuSkills 的数据接口（如有）。

    M6-1 阶段：占位 — XiaohongshuSkills 未公开标准化 metrics 命令；
    若其后续版本提供 → 调 subprocess；否则 probe_fn 抛 NotImplemented。
    """
    platform = "xiaohongshu"

    def __init__(
        self,
        *,
        skills_path: str | Path | None = None,
        probe_fn: Callable[[str], dict | None] | None = None,
    ) -> None:
        self._skills = skills_path
        self._probe = probe_fn

    def collect(self, pub: Publication) -> MetricsSnapshot | None:
        if not pub.platform_post_id or not self._probe:
            return None
        try:
            data = self._probe(pub.platform_post_id)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return MetricsSnapshot(
            publication_id=pub.id,
            platform="xiaohongshu",
            collected_at=datetime.now(timezone.utc).isoformat(),
            views=_safe_int(data.get("views")),
            likes=_safe_int(data.get("likes")),
            comments=_safe_int(data.get("comments")),
            shares=_safe_int(data.get("shares")),
            followers_delta=_safe_int(data.get("followers_delta")),
            raw=json.dumps(data, ensure_ascii=False)[:4000],
        )


class DouyinMetricsCollector(MetricsCollector):
    """抖音 metrics：创作者后台登录态抓。

    M6-1 阶段：与头条相似（Playwright + 启发式）；
    真实集成时按 creator.douyin.com 实际页面结构改 selectors。
    """
    platform = "douyin"

    def __init__(
        self,
        *,
        cookies_path: Path,
        probe_fn: Callable[..., dict | None] | None = None,
    ) -> None:
        self._cookies = Path(cookies_path)
        self._probe = probe_fn or _real_douyin_probe

    def collect(self, pub: Publication) -> MetricsSnapshot | None:
        if not pub.platform_post_id:
            return None
        try:
            data = self._probe(self._cookies, pub.platform_post_id)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return MetricsSnapshot(
            publication_id=pub.id,
            platform="douyin",
            collected_at=datetime.now(timezone.utc).isoformat(),
            views=_safe_int(data.get("views")),
            likes=_safe_int(data.get("likes")),
            comments=_safe_int(data.get("comments")),
            shares=_safe_int(data.get("shares")),
            followers_delta=_safe_int(data.get("followers_delta")),
            raw=json.dumps(data, ensure_ascii=False)[:4000],
        )


def _real_douyin_probe(cookies_path: Path, video_id: str) -> dict | None:
    """真实抓抖音创作者后台数据（占位实现；集成时按实际页面改）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    url = "https://creator.douyin.com/creator-micro/content/manage"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    storage_state=str(cookies_path),
                )
                page = ctx.new_page()
                resp = page.goto(url, timeout=20000)
                if resp is None or resp.status != 200:
                    return None
                # 占位启发式 — 真实集成时改为结构化提取
                html = page.content()
                return _parse_douyin_manage_html(html, video_id)
            finally:
                browser.close()
    except Exception:
        return None


def _parse_douyin_manage_html(html: str, video_id: str) -> dict | None:
    """占位启发式解析（集成时按实际页面结构改）。"""
    pattern = re.compile(
        rf"{re.escape(video_id)}.*?(\d+)",
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    return {
        "views": _safe_int(m.group(1)),
    }


# ── 工厂 ───────────────────────────────────────────────


def build_collector(platform: str, *, config: object) -> MetricsCollector | None:
    """按 platform 构造 collector。失败返回 None（HARD_PARTS §6 同款降级）。"""
    if platform == "x":
        # 找 config.platforms.x.accounts[0].credentials
        try:
            x = getattr(config.platforms, "x", None)
            if x is None or not x.accounts:
                return None
            from pipeline.publishers.x_api import load_x_credentials
            tok = load_x_credentials(x.accounts[0].credentials)
            return XMetricsCollector(bearer_token=tok)
        except Exception:
            return None
    if platform == "toutiao":
        try:
            plat = getattr(config.platforms, "toutiao", None)
            if plat is None or not plat.accounts:
                return None
            return ToutiaoMetricsCollector(
                cookies_path=Path(plat.accounts[0].cookies),
            )
        except Exception:
            return None
    if platform == "xiaohongshu":
        return XiaohongshuMetricsCollector()
    if platform == "douyin":
        try:
            plat = getattr(config.platforms, "douyin", None)
            if plat is None or not plat.accounts:
                return None
            return DouyinMetricsCollector(
                cookies_path=Path(plat.accounts[0].cookies),
            )
        except Exception:
            return None
    return None


__all__ = [
    "MetricsSnapshot",
    "MetricsCollector",
    "XMetricsCollector",
    "ToutiaoMetricsCollector",
    "XiaohongshuMetricsCollector",
    "DouyinMetricsCollector",
    "build_collector",
]