"""Pixelle-Video 第二引擎（TECH_SPEC §5.6 + HARD_PARTS §6 + M0-0 DECISION）。

Pixelle-Video（ATH-MaaS/Pixelle-Video，24.1k⭐，Apache-2.0）：
- AI 生成类内容（知识科普 / 读书 / 情感类）优先引擎
- MPT 仍是默认兜底（时效资讯量产）
- 异步 HTTP API 与 VideoEngine 契约天然契合

API 契约（按 M0-0 evaluation-notes §4 复核版本）：
- POST /api/video/generate/async → task_id
  body: {
    "mode": "fixed",                # 注入我方文案，跳过 Pixelle LLM 写稿
    "title": "...",                 # 必传（不传会触发 Pixelle LLM 生成）
    "text": "...\\n\\n段落1\\n\\n段落2...",  # 双换行 = 分镜边界
    "frame_template": "1080x1920" | "1920x1080" | "1080x1080",
    "prompt_prefix": "...",         # 可选，全局风格提示
    "voice": "zh-CN-YunxiNeural",   # 边缘 TTS 音色
  }
- GET /api/tasks/{id} → {state, progress, ...}
  状态：pending / running / done / failed
  重要：progress 对 video 任务**未接线**，恒 null — **以 state 为准**
  404 = 服务重启（任务状态存内存），按 failed + 重提交
- GET /api/videos/{id}/download → mp4 文件
  注意：完成任务**默认 24h 后被服务端清理**——及时 fetch

**契约不变**：VideoEngine ABC（TECH_SPEC §5.6）— submit/poll/fetch 三段式。

实测 / 集成注意：
- frame_template 是必填（aspect 映射：9:16 → 1080x1920 等）
- duration_s 无法硬指定（API 无 duration 字段；时长=各帧 TTS 累加）
  → 适配层按语速预估校验，不严格控制
- 口播稿分段协议：双换行（\\n\\n）= 分镜边界——我方口播稿派生已隐含分段
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from pipeline.creators.video.base import (
    VideoEngine,
    VideoJobStatus,
    VideoRequest,
)
from pipeline.utils.errors import CreateError


# ── 常量 ─────────────────────────────────────────────

# aspect → frame_template 映射（Pixelle templates/ 目录）
FRAME_TEMPLATE_BY_ASPECT = {
    "9:16": "1080x1920",
    "16:9": "1920x1080",
    "1:1": "1080x1080",
}

# Pixelle 状态映射（与我方内部 state 对齐）
_PIXELLE_STATE_MAP = {
    "pending": "pending",
    "running": "running",
    "processing": "running",   # 别名
    "done": "done",
    "completed": "done",       # 别名
    "success": "done",         # 别名
    "failed": "failed",
    "error": "failed",         # 别名
}

# 默认 TTS 音色（M0-0 默认 zh-CN-YunxiNeural）
DEFAULT_VOICE = "zh-CN-YunxiNeural"

# fetch 下载超时（视频通常 10-50MB）
FETCH_TIMEOUT_S = 300.0

# 24h 服务端清理警告（HARD_PARTS §6 决策：及时 fetch）
COMPLETION_TTL_HOURS = 24


# ── HTTP 客户端（可注入） ─────────────────────────────


def _real_http_post(url: str, *, json_body: dict, timeout: float) -> dict:
    """默认 httpx POST → 解析 JSON。"""
    import httpx
    try:
        resp = httpx.post(url, json=json_body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise CreateError(
            f"Pixelle service unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise CreateError(
            f"Pixelle submit HTTP {resp.status_code}: {snippet}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise CreateError(
            f"Pixelle submit non-JSON (HTTP {resp.status_code}): "
            f"{resp.text[:200]!r}"
        ) from e


def _real_http_get_json(url: str, *, timeout: float) -> tuple[int, dict | None]:
    """默认 httpx GET → (status, json|None)。

    返回 status 是为了 caller 区分 404（任务丢失）vs 200（正常）。
    """
    import httpx
    try:
        resp = httpx.get(url, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise CreateError(
            f"Pixelle poll unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code == 404:
        # 任务丢失（服务重启或 24h 清理）— 不抛，caller 决定
        return (404, None)
    if resp.status_code >= 400:
        return (resp.status_code, None)
    try:
        return (resp.status_code, resp.json())
    except ValueError:
        return (resp.status_code, None)


def _real_http_download(url: str, dest: Path, *, timeout: float) -> Path:
    """下载 mp4 到 dest，tmp → rename。"""
    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    try:
        with httpx.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code >= 400:
                if tmp.exists():
                    tmp.unlink()
                raise CreateError(
                    f"Pixelle download HTTP {resp.status_code}"
                )
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        tmp.rename(dest)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        if tmp.exists():
            tmp.unlink()
        raise CreateError(
            f"Pixelle download failed: {url}: {e!r}"
        ) from e
    return dest


# ── PixelleEngine ───────────────────────────────────────


class PixelleEngine(VideoEngine):
    """Pixelle-Video 引擎客户端（TECH_SPEC §5.6）。"""

    name = "pixelle"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:9000",
        poll_interval_s: int = 30,
        timeout_s: int = 1800,   # 30 min（视频生成慢）
        voice: str = DEFAULT_VOICE,
        prompt_prefix: str = "",
        http_post: Callable[..., dict] | None = None,
        http_get_json: Callable[..., tuple[int, dict | None]] | None = None,
        http_download: Callable[..., Path] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._poll_interval = poll_interval_s
        self._timeout_s = timeout_s
        self._voice = voice
        self._prompt_prefix = prompt_prefix
        self._post = http_post or _real_http_post
        self._get = http_get_json or _real_http_get_json
        self._download = http_download or _real_http_download
        self._sleep = sleep_fn or time.sleep

    # ── submit ──────────────────────────────────────────

    def submit(self, req: VideoRequest) -> str:
        """POST /api/video/generate/async → 拿 task_id。

        关键契约：
        - mode="fixed" 跳过 Pixelle LLM 写稿（文案主权在我方）
        - title 必传（不传会触发 Pixelle LLM 生成，违反文案主权）
          → 从 req.style["title"] 取，回退 req.content_id
        - text 双换行分段 = 分镜边界
        - frame_template 必填（aspect 映射）

        **契约说明**：VideoRequest 契约（TECH_SPEC §5.6）无 title 字段；
        title 走 style["title"] 注入（编排层从 canonical.title 填进去）。
        """
        frame_template = FRAME_TEMPLATE_BY_ASPECT.get(req.aspect)
        if frame_template is None:
            raise CreateError(
                f"aspect {req.aspect!r} not supported by Pixelle; "
                f"supported: {list(FRAME_TEMPLATE_BY_ASPECT.keys())}"
            )

        # title 注入：style["title"] 优先，回退 content_id（避免触发其 LLM）
        title = (
            req.style.get("title")
            or req.style.get("video_title")
            or req.content_id
        )

        url = f"{self._base}/api/video/generate/async"
        body = {
            "mode": "fixed",   # 文案主权（HARD_PARTS §6 决策 2）
            "title": title,
            "text": req.script,
            "frame_template": frame_template,
            "voice": self._voice,
        }
        if self._prompt_prefix:
            body["prompt_prefix"] = self._prompt_prefix

        try:
            resp = self._post(url, json_body=body, timeout=30.0)
        except CreateError:
            raise
        task_id = resp.get("task_id") or resp.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise CreateError(
                f"Pixelle submit returned no task_id: {resp!r}"
            )
        return task_id

    # ── poll ──────────────────────────────────────────

    def poll(self, job_id: str) -> VideoJobStatus:
        """GET /api/tasks/{id} → 标准化 VideoJobStatus。

        关键决策（evaluation-notes §4 复核）：
        - progress 字段对 video 任务**未接线** — 恒 null，不依赖
        - 404 = 任务丢失（服务重启 / 24h 清理）→ 抛 CreateError（带
          「task lost」标识让编排层决定是否重提交）
        """
        url = f"{self._base}/api/tasks/{job_id}"
        status_code, payload = self._get(url, timeout=15.0)
        if status_code == 404:
            # 任务丢失（服务重启或超过 24h）→ 抛错让编排层 catch 重提交
            raise CreateError(
                f"Pixelle task lost (404): job_id={job_id}; "
                "service restarted or >24h cleanup. Resubmit."
            )
        if payload is None:
            raise CreateError(
                f"Pixelle poll got HTTP {status_code} without JSON"
            )
        raw_state = (
            payload.get("state") or payload.get("status") or "unknown"
        )
        state = _PIXELLE_STATE_MAP.get(str(raw_state).lower(), "unknown")
        # progress 字段对 video 任务未接线 → 强制 None（不要百分比假象）
        progress = None
        error = payload.get("error") or payload.get("msg")
        if state == "failed" and not error:
            error = "Pixelle task failed (no detail)"
        return VideoJobStatus(
            state=state, progress=progress, error=error,
        )

    # ── fetch ──────────────────────────────────────────

    def fetch(self, job_id: str, dest: Path) -> Path:
        """GET /api/videos/{id}/download → 下载 mp4 到 dest。

        注意：服务默认 24h 后清理；超时则需重提交。
        """
        url = f"{self._base}/api/videos/{job_id}/download"
        return self._download(url, dest, timeout=FETCH_TIMEOUT_S)

    # ── 便利：完整端到端 ────────────────────────────────

    def run_to_completion(
        self,
        req: VideoRequest,
        dest: Path,
    ) -> Path:
        """一站式：submit → 轮询到 done → fetch。

        - 404（任务丢失）→ 抛 CreateError("task lost")，编排层决定重提交
        - 总耗时 > timeout_s → CreateError
        - 中途 failed → CreateError 带 detail
        """
        job_id = self.submit(req)
        deadline = time.monotonic() + self._timeout_s
        attempt = 0
        while True:
            if time.monotonic() > deadline:
                raise CreateError(
                    f"Pixelle {job_id} timeout after {self._timeout_s}s"
                )
            try:
                status = self.poll(job_id)
            except CreateError as e:
                # 「task lost」一类不可恢复错 → 立即抛（编排层重提交）
                if "task lost" in str(e):
                    raise
                # 网络/解析类瞬态错 → 重试一次
                attempt += 1
                if attempt >= 2:
                    raise CreateError(
                        f"Pixelle {job_id} poll failed twice: {e}"
                    ) from e
                self._sleep(self._poll_interval)
                continue
            attempt = 0
            if status.state == "done":
                return self.fetch(job_id, dest)
            if status.state == "failed":
                raise CreateError(
                    f"Pixelle {job_id} failed: {status.error}"
                )
            # pending / running / unknown → 继续等
            self._sleep(self._poll_interval)


# ── 工厂 ─────────────────────────────────────────────


def build_pixelle_engine(
    cfg,
    *,
    http_post: Callable[..., dict] | None = None,
    http_get_json: Callable[..., tuple[int, dict | None]] | None = None,
    http_download: Callable[..., Path] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> PixelleEngine:
    """从 cfg.video 构造 PixelleEngine。"""
    base_url = "http://127.0.0.1:9000"
    poll_interval = 30
    timeout_s = 1800
    voice = DEFAULT_VOICE
    prompt_prefix = ""
    # cfg.video.pixelle 字段（M5-3 扩展；缺则用默认）
    pix = getattr(getattr(cfg, "video", None), "pixelle", None)
    if pix is not None:
        base_url = getattr(pix, "base_url", base_url)
        poll_interval = getattr(pix, "poll_interval_s", poll_interval)
        timeout_s = getattr(pix, "timeout_s", timeout_s)
        voice = getattr(pix, "voice", voice)
        prompt_prefix = getattr(pix, "prompt_prefix", prompt_prefix)
    return PixelleEngine(
        base_url=base_url,
        poll_interval_s=poll_interval,
        timeout_s=timeout_s,
        voice=voice,
        prompt_prefix=prompt_prefix,
        http_post=http_post,
        http_get_json=http_get_json,
        http_download=http_download,
        sleep_fn=sleep_fn,
    )


def is_pixelle_alive(
    base_url: str = "http://127.0.0.1:9000",
    *,
    timeout: float = 3.0,
) -> bool:
    """探活：HEAD / 或 GET /api/... → 2xx/4xx 都算 alive（服务在跑）。"""
    import httpx
    try:
        r = httpx.get(base_url, timeout=timeout)
        # 任何响应（除网络层错误）都算 alive——4xx 表示路由存在但请求缺参数
        return r.status_code < 500
    except Exception:
        return False


__all__ = [
    "PixelleEngine",
    "build_pixelle_engine",
    "is_pixelle_alive",
    "FRAME_TEMPLATE_BY_ASPECT",
    "DEFAULT_VOICE",
    "COMPLETION_TTL_HOURS",
]