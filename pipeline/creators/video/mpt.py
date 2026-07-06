"""MoneyPrinterTurbo HTTP 客户端（TECH_SPEC §5.6 + HARD_PARTS §6）。

MPT 是独立服务（FastAPI + 任务队列），生成一条视频 2-10 分钟。
**我们不部署它**，只写一个薄客户端。docker-compose 起服务在 127.0.0.1:8080。

接口（按 MPT v1 API）：
- POST /api/v1/videos          提交任务 → task_id
- GET  /api/v1/tasks/{task_id} 查状态 → state + progress
- GET  /api/v1/videos/{task_id}/download  下载成品 mp4

实测字段命名细节（MPT 1.2.x 之前）：
- 提交 body: {"video_script": "...", "video_subject": "...",
              "voice_name": "zh-CN-YunxiNeural", "video_aspect": "9:16",
              "keywords": [...], "pexels_api_keys": "..."}
- 响应: {"task_id": "..."}（HTTP 200）或 {"code": 400, "msg": "..."}
- 状态: {"state": "pending|running|done|failed", "progress": 0.0-1.0}

**关键不变量**：
- MPT 挂了 → CreateError（**不**抛 SystemExit，编排层 catch 后该 content
  标 failed，图文格式不受影响）
- 轮询超时 → CreateError（默认 20min，与 config.video.mpt.timeout_s 一致）
- HTTP 客户端可注入（测试 mock）
- 路径 / 端口 / 音色 / 关键词全部走 config
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from pipeline.creators.video.base import (
    VideoEngine,
    VideoJobStatus,
    VideoRequest,
)
from pipeline.utils.errors import CreateError


# ── 常量 ───────────────────────────────────────────────

# MPT 任务状态映射（与我方内部 state 对齐）
_MPT_STATE_MAP = {
    "pending": "pending",
    "running": "running",
    "done": "done",
    "failed": "failed",
    # MPT 偶有 alias
    "processing": "running",
    "completed": "done",
    "error": "failed",
}


# ── HTTP 客户端（可注入） ─────────────────────────────


def _real_http_post(
    url: str,
    *,
    json_body: dict,
    timeout: float,
) -> dict:
    """默认 httpx POST → 解析 JSON。

    网络层异常 / 非 2xx / 非 dict 响应 → 抛 CreateError。
    """
    import httpx
    try:
        resp = httpx.post(url, json=json_body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise CreateError(
            f"MPT service unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise CreateError(
            f"MPT submit HTTP {resp.status_code}: {snippet}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise CreateError(
            f"MPT submit non-JSON response (HTTP {resp.status_code}): "
            f"{resp.text[:200]!r}"
        ) from e


def _real_http_get_json(url: str, *, timeout: float) -> dict:
    """默认 httpx GET → 解析 JSON。"""
    import httpx
    try:
        resp = httpx.get(url, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise CreateError(
            f"MPT poll unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code >= 400:
        raise CreateError(
            f"MPT poll HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise CreateError(
            f"MPT poll non-JSON: {resp.text[:200]!r}"
        ) from e


def _real_http_download(url: str, dest: Path, *, timeout: float) -> Path:
    """下载 mp4 到 dest，tmp → rename（幂等）。"""
    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    try:
        with httpx.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code >= 400:
                raise CreateError(
                    f"MPT download HTTP {resp.status_code}"
                )
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        tmp.rename(dest)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        if tmp.exists():
            tmp.unlink()
        raise CreateError(
            f"MPT download failed: {url}: {e!r}"
        ) from e
    return dest


# ── MPTEngine ────────────────────────────────────────


class MPTEngine(VideoEngine):
    """MoneyPrinterTurbo 引擎客户端（TECH_SPEC §5.6）。"""

    name = "mpt"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        poll_interval_s: int = 30,
        timeout_s: int = 1200,   # 20 min
        http_post: Callable[..., dict] | None = None,
        http_get_json: Callable[..., dict] | None = None,
        http_download: Callable[..., Path] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._poll_interval = poll_interval_s
        self._timeout_s = timeout_s
        self._post = http_post or _real_http_post
        self._get = http_get_json or _real_http_get_json
        self._download = http_download or _real_http_download
        self._sleep = sleep_fn or time.sleep

    # ── submit ──────────────────────────────────────────

    def submit(self, req: VideoRequest) -> str:
        """POST /api/v1/videos → 拿 task_id。

        body 字段对齐 MPT 1.2.x：video_script / voice_name / video_aspect /
        keywords / pexels_api_keys。
        """
        url = f"{self._base}/api/v1/videos"
        # 从 style 里取 Pexels key / voice；缺省值由 config.video.mpt 提供
        pexels_key = req.style.get("pexels_api_key", "")
        voice = req.style.get("voice", "zh-CN-YunxiNeural")
        keywords = req.style.get("keywords", [])

        body = {
            "video_subject": req.script[:60],   # MPT 要求 subject 短摘要
            "video_script": req.script,
            "voice_name": voice,
            "video_aspect": req.aspect,
            "keywords": keywords,
            "pexels_api_keys": pexels_key,
        }
        try:
            resp = self._post(
                url, json_body=body, timeout=30.0,
            )
        except CreateError:
            raise  # 透传（编排层 catch → content 标 failed）
        task_id = resp.get("task_id") or resp.get("id") or resp.get("data")
        if not isinstance(task_id, str) or not task_id:
            raise CreateError(
                f"MPT submit returned no task_id: {resp!r}"
            )
        return task_id

    # ── poll ──────────────────────────────────────────

    def poll(self, job_id: str) -> VideoJobStatus:
        """GET /api/v1/tasks/{job_id} → 标准化 VideoJobStatus。

        MPT 1.2.x 响应：{"state": "running", "progress": 0.45, ...}
        """
        url = f"{self._base}/api/v1/tasks/{job_id}"
        resp = self._get(url, timeout=15.0)
        raw_state = resp.get("state") or resp.get("status") or "unknown"
        state = _MPT_STATE_MAP.get(str(raw_state).lower(), "unknown")
        progress = resp.get("progress")
        if progress is not None:
            try:
                progress = float(progress)
                if progress > 1.0:   # MPT 有时给百分比
                    progress = progress / 100.0
            except (ValueError, TypeError):
                progress = None
        error = resp.get("error") or resp.get("msg")
        if state == "failed" and not error:
            error = "MPT task failed (no detail)"
        return VideoJobStatus(
            state=state, progress=progress, error=error,
        )

    # ── fetch ──────────────────────────────────────────

    def fetch(self, job_id: str, dest: Path) -> Path:
        """GET /api/v1/videos/{job_id}/download → 下载 mp4 到 dest。"""
        url = f"{self._base}/api/v1/videos/{job_id}/download"
        return self._download(url, dest, timeout=60.0)

    # ── 便利：完整端到端（submit + 轮询 + fetch）───────

    def run_to_completion(
        self,
        req: VideoRequest,
        dest: Path,
    ) -> Path:
        """一站式：submit → 轮询到 done → fetch。失败抛 CreateError。

        - 单次 poll 失败不算「任务失败」，重试一次
        - 总耗时 > timeout_s → CreateError
        - 中途 failed → CreateError 带 detail
        """
        job_id = self.submit(req)
        deadline = time.monotonic() + self._timeout_s
        attempt = 0
        while True:
            if time.monotonic() > deadline:
                raise CreateError(
                    f"MPT {job_id} timeout after {self._timeout_s}s"
                )
            try:
                status = self.poll(job_id)
            except CreateError as e:
                attempt += 1
                if attempt >= 2:
                    raise CreateError(
                        f"MPT {job_id} poll failed twice: {e}"
                    ) from e
                self._sleep(self._poll_interval)
                continue
            attempt = 0
            if status.state == "done":
                return self.fetch(job_id, dest)
            if status.state == "failed":
                raise CreateError(
                    f"MPT {job_id} failed: {status.error}"
                )
            if status.state in ("pending", "running", "unknown"):
                self._sleep(self._poll_interval)
                continue
            # 未知 state：当作 pending 等下一轮
            self._sleep(self._poll_interval)


# ── 工厂（带服务挂掉降级） ─────────────────────────────


def build_mpt_engine(
    cfg,
    *,
    http_post: Callable[..., dict] | None = None,
    http_get_json: Callable[..., dict] | None = None,
    http_download: Callable[..., Path] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> MPTEngine:
    """从 config 构造 MPTEngine。

    不在此探活（探活放到编排层 run_one 前；这里只保证能构造）。
    注入 http_* 用于测试。
    """
    mpt_cfg = getattr(cfg.video, "mpt", None) if hasattr(cfg, "video") else None
    base_url = getattr(mpt_cfg, "base_url", "http://127.0.0.1:8080") if mpt_cfg else "http://127.0.0.1:8080"
    poll_interval = getattr(mpt_cfg, "poll_interval_s", 30) if mpt_cfg else 30
    timeout_s = getattr(mpt_cfg, "timeout_s", 1200) if mpt_cfg else 1200
    return MPTEngine(
        base_url=base_url,
        poll_interval_s=poll_interval,
        timeout_s=timeout_s,
        http_post=http_post,
        http_get_json=http_get_json,
        http_download=http_download,
        sleep_fn=sleep_fn,
    )


def is_mpt_alive(
    base_url: str = "http://127.0.0.1:8080",
    *,
    timeout: float = 3.0,
) -> bool:
    """探活：HEAD / 或 GET / → 2xx = alive。

    用于编排层在 run_one 前判断是否走 mpt 引擎。
    """
    import httpx
    try:
        r = httpx.get(base_url, timeout=timeout)
        return 200 <= r.status_code < 400
    except Exception:
        return False


__all__ = [
    "MPTEngine",
    "build_mpt_engine",
    "is_mpt_alive",
    "_real_http_post",
    "_real_http_get_json",
    "_real_http_download",
]