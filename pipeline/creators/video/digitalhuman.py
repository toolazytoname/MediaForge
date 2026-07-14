"""LatentSync 数字人口播引擎（TECH_SPEC §5.6 + HARD_PARTS §6.1 + M12-1）。

数字人口播不是单一服务能搞定的——它是三段拼接：
  1. TTS（文字 → 音频）：复用 pipeline.creators.tts（edge-tts）
  2. 形象素材（一段真人循环讲话/待机视频）：用户侧资产，config 里按
     name → 本地路径映射（DigitalHumanConfig.avatar_templates）
  3. 唇形同步：本地自托管 `bytedance/LatentSync`（Apache-2.0，Cog 封装），
     `cog build` 起本地 HTTP predictions 服务（标准 Cog predictions API）

原计划的 `aigcpanel`（modstart-lib/aigcpanel）核实为 Electron 桌面应用
（AGPL-3.0），不是无头服务，无法被 cron 流水线远程调用——已废弃，改用
LatentSync（决策记录见 HARD_PARTS §6.1）。

Cog predictions API 契约（标准形状，非 LatentSync 专属）：
- POST /predictions
  body: {"input": {"video": <data URI 或 URL>, "audio": <data URI 或 URL>}}
  响应: {"id": "...", "status": "starting", ...}（异步，不阻塞等待完成）
- GET /predictions/{id} → {"status": ..., "output": ..., "error": ...}
  status: starting/processing → running；succeeded → done；
          failed/canceled → failed
  progress：predictions API 不提供百分比字段 —— 强制 None
          （同 Pixelle "不被假象百分比骗"的教训，见 pixelle.py）
  404 = 任务丢失（服务重启 / 记录过期）→ CreateError，编排层决定重提交
- output 字段的 wire 格式不确定（本地部署方式而定，可能是 URL 也可能是
  base64 data URI）→ fetch() 防御性地同时兼容两种格式

**契约不变**：VideoEngine ABC（TECH_SPEC §5.6）— submit/poll/fetch 三段式，
不新增/改动方法签名。LatentSync 只做唇形同步，不产出人物形象、不产出
语音、不写文案——文案仍来自我方 LLM 派生（HARD_PARTS §6 教训）。
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
import tempfile
import time
from pathlib import Path
from typing import Callable

from pipeline.creators.tts import DEFAULT_VOICE, synthesize_speech
from pipeline.creators.video.base import (
    VideoEngine,
    VideoJobStatus,
    VideoRequest,
)
from pipeline.utils.errors import CreateError


# ── 常量 ─────────────────────────────────────────────

# Cog predictions 状态映射（与我方内部 state 对齐）
_COG_STATE_MAP = {
    "starting": "running",
    "processing": "running",
    "succeeded": "done",
    "failed": "failed",
    "canceled": "failed",
}

# fetch 下载超时（视频通常几 MB～几十 MB）
FETCH_TIMEOUT_S = 300.0


# ── HTTP 客户端（可注入，照抄 pixelle.py 模式） ─────────


def _real_http_post(url: str, *, json_body: dict, timeout: float) -> dict:
    """默认 httpx POST → 解析 JSON。"""
    import httpx
    try:
        resp = httpx.post(url, json=json_body, timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise CreateError(
            f"digitalhuman (LatentSync) service unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise CreateError(
            f"digitalhuman submit HTTP {resp.status_code}: {snippet}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise CreateError(
            f"digitalhuman submit non-JSON (HTTP {resp.status_code}): "
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
            f"digitalhuman poll unreachable: {url}: {e!r}"
        ) from e
    if resp.status_code == 404:
        return (404, None)
    if resp.status_code >= 400:
        return (resp.status_code, None)
    try:
        return (resp.status_code, resp.json())
    except ValueError:
        return (resp.status_code, None)


def _real_http_download(url: str, dest: Path, *, timeout: float) -> Path:
    """下载 mp4 到 dest，tmp → rename（幂等，HARD_PARTS §5）。"""
    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    try:
        with httpx.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code >= 400:
                if tmp.exists():
                    tmp.unlink()
                raise CreateError(
                    f"digitalhuman download HTTP {resp.status_code}"
                )
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        tmp.rename(dest)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        if tmp.exists():
            tmp.unlink()
        raise CreateError(
            f"digitalhuman download failed: {url}: {e!r}"
        ) from e
    return dest


def _real_tts_synthesize(text: str, dest: Path, *, voice: str) -> Path:
    """默认 TTS 实现：委托 pipeline.creators.tts（edge-tts）。"""
    return synthesize_speech(text, dest, voice=voice)


# ── 本地文件 <-> data URI（Cog predictions 的 Path 输入/输出格式） ──


def _local_file_to_data_uri(path: Path, *, default_mime: str) -> str:
    """把本地文件编码成 base64 data URI，作为 Cog predictions 的 input。

    WHY：Cog predictions 的 input 字段类型是 Path，官方支持传 URL 或
    data URI；本地形象素材/TTS 音频大概率与 LatentSync cog 容器不共享
    文件系统（HARD_PARTS §6.1 未指明具体部署方式），data URI 是唯一
    不依赖额外文件服务器就能把本地文件内容传过去的方式，故选它作为
    防御性默认实现。
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        raise CreateError(
            f"digitalhuman: cannot read local file {path}: {e!r}"
        ) from e
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or default_mime
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _write_data_uri(data_uri: str, dest: Path) -> Path:
    """把 output 的 data URI 解码写入 dest，tmp → rename。"""
    try:
        _header, b64data = data_uri.split(",", 1)
        raw = base64.b64decode(b64data)
    except (ValueError, binascii.Error) as e:
        raise CreateError(
            f"digitalhuman: malformed data URI output: {e!r}"
        ) from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.rename(dest)
    return dest


# ── DigitalHumanEngine ───────────────────────────────────


class DigitalHumanEngine(VideoEngine):
    """LatentSync 自托管数字人口播引擎客户端（TECH_SPEC §5.6）。"""

    name = "digitalhuman"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:5000",
        poll_interval_s: int = 20,
        timeout_s: int = 1200,   # 20 min
        tts_voice: str = DEFAULT_VOICE,
        avatar_templates: dict[str, str] | None = None,
        http_post: Callable[..., dict] | None = None,
        http_get_json: Callable[..., tuple[int, dict | None]] | None = None,
        http_download: Callable[..., Path] | None = None,
        tts_synthesize: Callable[..., Path] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._poll_interval = poll_interval_s
        self._timeout_s = timeout_s
        self._tts_voice = tts_voice
        self._avatar_templates = dict(avatar_templates or {})
        self._post = http_post or _real_http_post
        self._get = http_get_json or _real_http_get_json
        self._download = http_download or _real_http_download
        self._tts = tts_synthesize or _real_tts_synthesize
        self._sleep = sleep_fn or time.sleep

    # ── submit ──────────────────────────────────────────

    def submit(self, req: VideoRequest) -> str:
        """script → TTS 音频 + 形象模板 → POST /predictions → 拿 job_id。

        关键契约（HARD_PARTS §6.1 决策）：
        - avatar_template 必须显式指定且必须在 config 里配置过，缺失/未知
          名字一律 CreateError，不允许静默 fallback 到某个默认值（避免
          用错形象出成片）
        - 文案主权：script 来自我方 LLM 派生，只喂给 TTS，不让引擎自己写
        """
        avatar_name = req.style.get("avatar_template")
        if not avatar_name:
            raise CreateError(
                "digitalhuman submit requires req.style['avatar_template']; "
                "configure config.video.digitalhuman.avatar_templates and "
                "pass a template name explicitly (no silent default)"
            )
        avatar_path = self._avatar_templates.get(avatar_name)
        if not avatar_path:
            raise CreateError(
                f"avatar_template {avatar_name!r} not configured in "
                f"config.video.digitalhuman.avatar_templates "
                f"(known: {list(self._avatar_templates.keys())}); "
                "add assets/avatars/<name>.mp4 and register it in config"
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_tmp = Path(tmpdir) / f"{req.content_id}.mp3"
            audio_path = self._tts(req.script, audio_tmp, voice=self._tts_voice)

            video_input = _local_file_to_data_uri(
                Path(avatar_path), default_mime="video/mp4",
            )
            audio_input = _local_file_to_data_uri(
                audio_path, default_mime="audio/mpeg",
            )

            url = f"{self._base}/predictions"
            body = {"input": {"video": video_input, "audio": audio_input}}
            try:
                resp = self._post(url, json_body=body, timeout=30.0)
            except CreateError:
                raise

        job_id = resp.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise CreateError(
                f"digitalhuman submit returned no prediction id: {resp!r}"
            )
        return job_id

    # ── poll ──────────────────────────────────────────

    def poll(self, job_id: str) -> VideoJobStatus:
        """GET /predictions/{job_id} → 标准化 VideoJobStatus。

        - progress：Cog predictions API 不提供进度百分比 → 恒 None
          （不被假象百分比骗，同 pixelle.py 教训）
        - 404 = 任务丢失（服务重启/记录过期）→ CreateError("task lost")，
          编排层决定重提交
        """
        url = f"{self._base}/predictions/{job_id}"
        status_code, payload = self._get(url, timeout=15.0)
        if status_code == 404:
            raise CreateError(
                f"digitalhuman task lost (404): job_id={job_id}; "
                "cog service restarted or prediction expired. Resubmit."
            )
        if payload is None:
            raise CreateError(
                f"digitalhuman poll got HTTP {status_code} without JSON"
            )
        raw_status = payload.get("status", "unknown")
        state = _COG_STATE_MAP.get(str(raw_status).lower(), "unknown")
        # Cog predictions API 无进度百分比字段 → 强制 None
        progress = None
        error = payload.get("error")
        if state == "failed" and not error:
            error = f"digitalhuman task {raw_status} (no detail)"
        return VideoJobStatus(state=state, progress=progress, error=error)

    # ── fetch ──────────────────────────────────────────

    def fetch(self, job_id: str, dest: Path) -> Path:
        """下载 predictions 的 output 到 dest（tmp → rename）。

        output 的 wire 格式不确定（HARD_PARTS §6.1）：可能是可下载 URL，
        也可能是 base64 data URI —— 两种都兼容处理。
        """
        url = f"{self._base}/predictions/{job_id}"
        status_code, payload = self._get(url, timeout=15.0)
        if status_code == 404 or payload is None:
            raise CreateError(
                f"digitalhuman fetch: task lost (HTTP {status_code}) "
                f"for job_id={job_id}"
            )
        output = payload.get("output")
        if not output or not isinstance(output, str):
            raise CreateError(
                f"digitalhuman fetch: no usable output for {job_id}: "
                f"{payload!r}"
            )
        if output.startswith("data:"):
            return _write_data_uri(output, dest)
        if output.startswith(("http://", "https://")):
            return self._download(output, dest, timeout=FETCH_TIMEOUT_S)
        raise CreateError(
            f"digitalhuman fetch: unrecognized output format: {output[:100]!r}"
        )

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
                    f"digitalhuman {job_id} timeout after {self._timeout_s}s"
                )
            try:
                status = self.poll(job_id)
            except CreateError as e:
                if "task lost" in str(e):
                    raise
                attempt += 1
                if attempt >= 2:
                    raise CreateError(
                        f"digitalhuman {job_id} poll failed twice: {e}"
                    ) from e
                self._sleep(self._poll_interval)
                continue
            attempt = 0
            if status.state == "done":
                return self.fetch(job_id, dest)
            if status.state == "failed":
                raise CreateError(
                    f"digitalhuman {job_id} failed: {status.error}"
                )
            # running / unknown → 继续等
            self._sleep(self._poll_interval)


# ── 工厂（带服务挂掉/模板缺失降级） ───────────────────


def build_digitalhuman_engine(
    cfg,
    *,
    http_post: Callable[..., dict] | None = None,
    http_get_json: Callable[..., tuple[int, dict | None]] | None = None,
    http_download: Callable[..., Path] | None = None,
    tts_synthesize: Callable[..., Path] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> DigitalHumanEngine:
    """从 cfg.video.digitalhuman 构造 DigitalHumanEngine。

    不在此探活/校验形象素材是否存在于磁盘（构造阶段只读 config；
    真正校验在 submit() 里，模板名不存在才报错，见 HARD_PARTS §6.1）。
    """
    dh_cfg = getattr(cfg.video, "digitalhuman", None) if hasattr(cfg, "video") else None
    base_url = getattr(dh_cfg, "base_url", "http://127.0.0.1:5000") if dh_cfg else "http://127.0.0.1:5000"
    poll_interval = getattr(dh_cfg, "poll_interval_s", 20) if dh_cfg else 20
    timeout_s = getattr(dh_cfg, "timeout_s", 1200) if dh_cfg else 1200
    tts_voice = getattr(dh_cfg, "tts_voice", DEFAULT_VOICE) if dh_cfg else DEFAULT_VOICE
    avatar_templates = getattr(dh_cfg, "avatar_templates", {}) if dh_cfg else {}
    return DigitalHumanEngine(
        base_url=base_url,
        poll_interval_s=poll_interval,
        timeout_s=timeout_s,
        tts_voice=tts_voice,
        avatar_templates=avatar_templates,
        http_post=http_post,
        http_get_json=http_get_json,
        http_download=http_download,
        tts_synthesize=tts_synthesize,
        sleep_fn=sleep_fn,
    )


def is_digitalhuman_alive(
    base_url: str = "http://127.0.0.1:5000",
    *,
    timeout: float = 3.0,
) -> bool:
    """探活：GET / 或 /predictions → 2xx/4xx 都算 alive（服务在跑）。

    GPU 环境缺失时 LatentSync cog 服务通常压根起不来，探活会直接失败——
    交由工厂/编排层降级，不影响 mpt/pixelle 链路。
    """
    import httpx
    try:
        r = httpx.get(base_url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


__all__ = [
    "DigitalHumanEngine",
    "build_digitalhuman_engine",
    "is_digitalhuman_alive",
]
