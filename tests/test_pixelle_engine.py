"""M5-3 Pixelle-Video 第二引擎 测试。

覆盖契约（evaluation-notes §4 + M0-0 DECISION）：
- mode="fixed" 注入我方脚本（文案主权）
- title 必传（不传会触发 Pixelle LLM 生成）
- frame_template 必填（aspect 9:16/16:9/1:1 映射）
- progress 字段对 video 任务未接线 → 强制 None
- 404（任务丢失）→ CreateError（"task lost"），编排层重提交
- 24h 清理预警（COMPLETION_TTL_HOURS = 24）
- fetch 用 tmp→rename

真实端到端：fake Pixelle server + httpx 真跑 submit/poll/fetch。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pipeline.config import (
    AppConfig,
    PixelleConfig,
    VideoConfig,
)
from pipeline.creators.video.base import VideoRequest
from pipeline.creators.video.pixelle import (
    COMPLETION_TTL_HOURS,
    DEFAULT_VOICE,
    FRAME_TEMPLATE_BY_ASPECT,
    PixelleEngine,
    build_pixelle_engine,
    is_pixelle_alive,
)
from pipeline.creators.video import build_video_engine
from pipeline.utils.errors import CreateError


# ── helpers ─────────────────────────────────────────────


def _fake_post_ok(url, *, json_body, timeout):
    return {"task_id": "task_xyz789"}


def _fake_post_no_id(url, *, json_body, timeout):
    return {"code": 200, "msg": "no task_id"}


def _fake_get_with_state(state, error=None):
    def _f(url, *, timeout):
        payload = {"state": state}
        if error:
            payload["error"] = error
        return (200, payload)
    return _f


def _fake_get_404(url, *, timeout):
    """模拟任务丢失（服务重启或 24h 清理）。"""
    return (404, None)


def _fake_get_500(url, *, timeout):
    return (500, None)


def _fake_download_factory():
    def _f(url, dest, *, timeout):
        dest.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
        return dest
    return _f


def _sleep_zero(_):
    return None


def _req(aspect: str = "9:16") -> VideoRequest:
    """构造测试用 VideoRequest（title 走 style["title"]）。"""
    return VideoRequest(
        content_id="c_px_test",
        script="今天讲一个 AI 新工具。\n\n它能在三秒内生成完整视频。",
        duration_s=75,
        aspect=aspect,
        style={
            "voice": "zh-CN-YunxiNeural",
            "title": "AI 新工具实测",  # 注入 title（Pixelle 必传）
        },
    )


# ── FRAME_TEMPLATE_BY_ASPECT 常量 ────────────────────────


def test_frame_template_mapping() -> None:
    assert FRAME_TEMPLATE_BY_ASPECT["9:16"] == "1080x1920"
    assert FRAME_TEMPLATE_BY_ASPECT["16:9"] == "1920x1080"
    assert FRAME_TEMPLATE_BY_ASPECT["1:1"] == "1080x1080"


def test_completion_ttl_is_24h() -> None:
    """HARD_PARTS §6：服务默认 24h 后清理 → 及时 fetch 警示。"""
    assert COMPLETION_TTL_HOURS == 24


def test_default_voice_is_yunxi() -> None:
    assert DEFAULT_VOICE == "zh-CN-YunxiNeural"


# ── PixelleEngine.submit ────────────────────────────────


def test_submit_returns_task_id() -> None:
    eng = PixelleEngine(http_post=_fake_post_ok, sleep_fn=_sleep_zero)
    job_id = eng.submit(_req())
    assert job_id == "task_xyz789"


def test_submit_uses_mode_fixed_for_text_sovereignty() -> None:
    """mode="fixed" 跳过 Pixelle LLM 写稿（HARD_PARTS §6 决策 2）。"""
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng.submit(_req())
    assert captured["mode"] == "fixed"
    # 显式传 title（避免 Pixelle LLM 生成）
    assert captured["title"] == "AI 新工具实测"


def test_submit_maps_aspect_to_frame_template() -> None:
    """aspect → frame_template 映射。"""
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng.submit(_req("9:16"))
    assert captured["frame_template"] == "1080x1920"

    eng2 = PixelleEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng2.submit(_req("16:9"))
    assert captured["frame_template"] == "1920x1080"


def test_submit_rejects_unsupported_aspect() -> None:
    eng = PixelleEngine(http_post=_fake_post_ok, sleep_fn=_sleep_zero)
    with pytest.raises(CreateError, match="not supported"):
        eng.submit(_req("4:3"))  # 不在映射表


def test_submit_without_task_id_raises() -> None:
    eng = PixelleEngine(http_post=_fake_post_no_id, sleep_fn=_sleep_zero)
    with pytest.raises(CreateError, match="no task_id"):
        eng.submit(_req())


def test_submit_uses_script_as_text() -> None:
    """script → text 字段（M0-0 决策：文案主权在创作管道）。"""
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng.submit(_req())
    # 双换行分段 = 分镜边界
    assert captured["text"] == _req().script
    assert "\n\n" in captured["text"]


def test_submit_includes_voice() -> None:
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(
        http_post=capture, sleep_fn=_sleep_zero,
        voice="zh-CN-XiaoxiaoNeural",
    )
    eng.submit(_req())
    assert captured["voice"] == "zh-CN-XiaoxiaoNeural"


def test_submit_includes_prompt_prefix_when_set() -> None:
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(
        http_post=capture, sleep_fn=_sleep_zero,
        prompt_prefix="现代简约风格，",
    )
    eng.submit(_req())
    assert captured["prompt_prefix"] == "现代简约风格，"


def test_submit_omits_prompt_prefix_when_empty() -> None:
    """空 prompt_prefix 不应出现在 body（保持请求干净）。"""
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = PixelleEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng.submit(_req())
    assert "prompt_prefix" not in captured


# ── PixelleEngine.poll ─────────────────────────────────


def test_poll_running_returns_progress_none() -> None:
    """progress 对 video 任务未接线 → 强制 None（不依赖百分比）。"""
    eng = PixelleEngine(
        http_get_json=_fake_get_with_state("running"),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "running"
    assert status.progress is None   # 关键：不被假象百分比骗


def test_poll_done_returns_done_state() -> None:
    eng = PixelleEngine(
        http_get_json=_fake_get_with_state("done"),
        sleep_fn=_sleep_zero,
    )
    assert eng.poll("t").state == "done"


def test_poll_failed_returns_error() -> None:
    eng = PixelleEngine(
        http_get_json=_fake_get_with_state("failed", "t2i quota exceeded"),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "failed"
    assert "t2i quota" in status.error


def test_poll_404_raises_task_lost() -> None:
    """404 = 任务丢失（服务重启 / 24h 清理）→ CreateError。"""
    eng = PixelleEngine(
        http_get_json=_fake_get_404,
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="task lost"):
        eng.poll("t")


def test_poll_500_raises_create_error() -> None:
    eng = PixelleEngine(
        http_get_json=_fake_get_500,
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="500"):
        eng.poll("t")


def test_poll_alias_processing_maps_to_running() -> None:
    eng = PixelleEngine(
        http_get_json=_fake_get_with_state("processing"),
        sleep_fn=_sleep_zero,
    )
    assert eng.poll("t").state == "running"


def test_poll_alias_success_maps_to_done() -> None:
    eng = PixelleEngine(
        http_get_json=_fake_get_with_state("success"),
        sleep_fn=_sleep_zero,
    )
    assert eng.poll("t").state == "done"


# ── PixelleEngine.fetch ─────────────────────────────────


def test_fetch_downloads_to_dest(tmp_path: Path) -> None:
    eng = PixelleEngine(
        http_download=_fake_download_factory(),
        sleep_fn=_sleep_zero,
    )
    dest = tmp_path / "video.mp4"
    out = eng.fetch("task_x", dest)
    assert out == dest
    assert dest.read_bytes()[:8] == b"\x00\x00\x00\x18ftyp"


# ── PixelleEngine.run_to_completion ─────────────────────


def test_run_to_completion_happy_path(tmp_path: Path) -> None:
    poll_count = {"n": 0}

    def poll_then_done(url, *, timeout):
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return (200, {"state": "running"})
        return (200, {"state": "done"})

    eng = PixelleEngine(
        http_post=_fake_post_ok,
        http_get_json=poll_then_done,
        http_download=_fake_download_factory(),
        sleep_fn=_sleep_zero,
    )
    out = eng.run_to_completion(_req(), tmp_path / "v.mp4")
    assert out.exists()
    assert poll_count["n"] == 2


def test_run_to_completion_task_lost_raises_immediately(tmp_path: Path) -> None:
    """404 task lost → 立即抛（编排层决定重提交，不静默重试）。"""
    eng = PixelleEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_404,
        http_download=_fake_download_factory(),
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="task lost"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


def test_run_to_completion_failed_raises(tmp_path: Path) -> None:
    eng = PixelleEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_with_state("failed", "service busy"),
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="service busy"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


def test_run_to_completion_timeout_raises(tmp_path: Path) -> None:
    eng = PixelleEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_with_state("running"),
        sleep_fn=_sleep_zero,
        timeout_s=2,
        poll_interval_s=1,
    )
    with pytest.raises(CreateError, match="timeout after 2s"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


def test_run_to_completion_poll_retry_on_transient(tmp_path: Path) -> None:
    """瞬态错（非 task lost）→ 重试一次。"""
    poll_count = {"n": 0}

    def flaky(url, *, timeout):
        poll_count["n"] += 1
        if poll_count["n"] == 1:
            raise CreateError("network blip")
        return (200, {"state": "done"})

    eng = PixelleEngine(
        http_post=_fake_post_ok,
        http_get_json=flaky,
        http_download=_fake_download_factory(),
        sleep_fn=_sleep_zero,
    )
    out = eng.run_to_completion(_req(), tmp_path / "v.mp4")
    assert out.exists()
    assert poll_count["n"] == 2


# ── 工厂 / 降级 ─────────────────────────────────────


def test_build_pixelle_engine_from_config() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {
            "engine": "pixelle",
            "pixelle": {
                "base_url": "http://10.0.0.5:9000",
                "poll_interval_s": 20,
                "timeout_s": 1200,
                "voice": "zh-CN-XiaoxiaoNeural",
                "prompt_prefix": "温暖插画风",
            },
        },
    })
    eng = build_pixelle_engine(cfg)
    assert eng._base == "http://10.0.0.5:9000"
    assert eng._poll_interval == 20
    assert eng._timeout_s == 1200
    assert eng._voice == "zh-CN-XiaoxiaoNeural"
    assert eng._prompt_prefix == "温暖插画风"


def test_build_video_engine_pixelle_when_selected() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "pixelle"},
    })
    eng = build_video_engine(cfg)
    assert eng is not None
    assert eng.name == "pixelle"


def test_build_video_engine_returns_mpt_by_default() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
    })
    eng = build_video_engine(cfg)
    assert eng is not None
    assert eng.name == "mpt"


def test_is_pixelle_alive_handles_errors(monkeypatch) -> None:
    """服务不可达 → False（不抛）。"""
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            httpx.ConnectError("nope"),
                        ))
    assert is_pixelle_alive("http://127.0.0.1:1", timeout=0.1) is False


# ── 真实端到端（fake Pixelle server） ────────────────


def _make_fake_pixelle_script(port_path: Path) -> str:
    return f"""import sys
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import uvicorn, secrets, json

app = FastAPI()
JOBS = {{}}

@app.post("/api/video/generate/async")
async def submit(body: dict):
    # 验证契约字段
    if body.get("mode") != "fixed":
        return {{"code": 400, "msg": "mode must be fixed"}}
    if not body.get("title"):
        return {{"code": 400, "msg": "title required"}}
    if not body.get("frame_template"):
        return {{"code": 400, "msg": "frame_template required"}}
    if not body.get("text"):
        return {{"code": 400, "msg": "text required"}}
    tid = "task_" + secrets.token_hex(4)
    JOBS[tid] = {{"state": "pending", "poll_count": 0}}
    return {{"task_id": tid}}

@app.get("/api/tasks/{{tid}}")
async def poll(tid: str):
    j = JOBS.get(tid)
    if not j:
        raise HTTPException(status_code=404, detail="task not found")
    j["poll_count"] += 1
    # 第二次 poll 返回 done；progress 永远 None（未接线）
    if j["poll_count"] >= 2:
        j["state"] = "done"
    else:
        j["state"] = "running"
    return {{"state": j["state"], "progress": None}}

@app.get("/api/videos/{{tid}}/download")
async def download(tid: str):
    out = "{str(port_path / 'mock_video.mp4')}"
    import pathlib
    p = pathlib.Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(b"\\x00\\x00\\x00\\x18ftypisom" + b"\\x00" * 200)
    return FileResponse(out, media_type="video/mp4", filename="out.mp4")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
"""


def test_real_pixelle_end_to_end(tmp_path: Path) -> None:
    """真 httpx → fake Pixelle → submit / poll / fetch 全跑通。"""
    import socket
    import subprocess
    import sys

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server_script = tmp_path / "fake_pixelle_server.py"
    server_script.write_text(_make_fake_pixelle_script(tmp_path),
                             encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(server_script), str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        import httpx
        for _ in range(50):
            try:
                # fake server /api/tasks/ 返回 404 = 服务在跑
                httpx.get(f"http://127.0.0.1:{port}/api/tasks/probe",
                          timeout=0.3)
                break
            except httpx.HTTPStatusError:
                break  # 404 也算 alive
            except Exception:
                time.sleep(0.1)

        eng = PixelleEngine(
            base_url=f"http://127.0.0.1:{port}",
            poll_interval_s=1,
            timeout_s=10,
            sleep_fn=_sleep_zero,
        )
        out = eng.run_to_completion(_req(), tmp_path / "video.mp4")
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x00\x00\x00\x18ftyp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()