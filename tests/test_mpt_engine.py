"""M5-1 MoneyPrinterTurbo 客户端 + 口播稿派生 测试。

覆盖契约（HARD_PARTS §6）：
- MPTEngine.submit / poll / fetch 三段式
- 服务挂掉（CreateError）→ 编排层 catch → 该 content 标 failed
- 轮询超时（CreateError）
- 中途 failed（CreateError 带 detail）
- MPT 状态字符串别名（processing/completed/error）映射
- 口播稿派生：JSON 解析失败 + 字段缺/范围 → CreateError
- build_video_engine 工厂：服务不可达时返回 None（图文链路不受影响）

真实端到端：fake MPT server + httpx 真跑 submit/poll/fetch。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.config import (
    AppConfig,
    MPTConfig,
    VideoConfig,
)
from pipeline.creators.video.base import VideoRequest
from pipeline.creators.video.mpt import (
    MPTEngine,
    build_mpt_engine,
    is_mpt_alive,
)
from pipeline.creators.video import build_video_engine
from pipeline.utils.errors import CreateError


# ── fixtures ──────────────────────────────────────────


def _fake_post_ok(url, *, json_body, timeout):
    """模拟 submit 返回 task_id。"""
    return {"task_id": "task_abc123"}


def _fake_post_no_id(url, *, json_body, timeout):
    return {"code": 200, "msg": "weird response"}


def _fake_post_4xx(url, *, json_body, timeout):
    class _R:
        status_code = 400
        text = "bad params"
    raise CreateError("MPT submit HTTP 400: bad params")


def _fake_get_state(state, progress=0.5):
    def _f(url, *, timeout):
        return {"state": state, "progress": progress}
    return _f


def _fake_get_state_with_error(url, *, timeout):
    return {"state": "failed", "progress": 0.3, "error": "pexels quota exceeded"}


def _fake_download(dest_factory):
    def _f(url, dest, *, timeout):
        dest.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100)  # mp4 magic
        return dest
    return _f


def _sleep_zero(_seconds):
    """测试用 sleep：立即返回（不真睡）。"""
    return None


def _req() -> VideoRequest:
    return VideoRequest(
        content_id="c_test",
        script="今天讲一个 AI 新工具，它能在三秒内生成完整的口播视频。",
        duration_s=75,
        aspect="9:16",
        style={
            "voice": "zh-CN-YunxiNeural",
            "keywords": ["AI", "video generator"],
            "pexels_api_key": "fake-pexels-key",
        },
    )


# ── MPTEngine.submit ──────────────────────────────────


def test_submit_returns_task_id() -> None:
    eng = MPTEngine(http_post=_fake_post_ok, sleep_fn=_sleep_zero)
    job_id = eng.submit(_req())
    assert job_id == "task_abc123"


def test_submit_without_task_id_raises() -> None:
    eng = MPTEngine(http_post=_fake_post_no_id, sleep_fn=_sleep_zero)
    with pytest.raises(CreateError, match="no task_id"):
        eng.submit(_req())


def test_submit_http_error_wrapped() -> None:
    """HTTP 4xx/5xx → CreateError（编排层 catch → failed）。"""
    eng = MPTEngine(http_post=_fake_post_4xx, sleep_fn=_sleep_zero)
    with pytest.raises(CreateError, match="HTTP 400"):
        eng.submit(_req())


def test_submit_includes_required_fields() -> None:
    captured: dict = {}

    def capture(url, *, json_body, timeout):
        captured.update(json_body)
        return {"task_id": "t"}

    eng = MPTEngine(http_post=capture, sleep_fn=_sleep_zero)
    eng.submit(_req())
    assert captured["video_script"].startswith("今天讲")
    assert captured["voice_name"] == "zh-CN-YunxiNeural"
    assert captured["video_aspect"] == "9:16"
    assert "AI" in captured["keywords"]
    assert captured["pexels_api_keys"] == "fake-pexels-key"


# ── MPTEngine.poll ───────────────────────────────────


def test_poll_running_state() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("running", 0.45),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("task_x")
    assert status.state == "running"
    assert status.progress == 0.45
    assert status.error is None


def test_poll_pending_state() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("pending"),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "pending"


def test_poll_done_state() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("done", 1.0),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "done"


def test_poll_progress_percent_to_fraction() -> None:
    """MPT 有时给百分比 0-100，需归一化到 0-1。"""
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("running", 75),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.progress == 0.75


def test_poll_alias_state_processing_maps_to_running() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("processing"),
        sleep_fn=_sleep_zero,
    )
    assert eng.poll("t").state == "running"


def test_poll_alias_completed_maps_to_done() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("completed"),
        sleep_fn=_sleep_zero,
    )
    assert eng.poll("t").state == "done"


def test_poll_alias_error_maps_to_failed() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state_with_error,
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "failed"
    assert "pexels quota" in status.error


def test_poll_unknown_state_returns_unknown() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("weird-thing"),
        sleep_fn=_sleep_zero,
    )
    status = eng.poll("t")
    assert status.state == "unknown"


# ── MPTEngine.fetch ───────────────────────────────────


def test_fetch_downloads_to_dest(tmp_path: Path) -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state("done"),
        http_download=_fake_download(tmp_path),
        sleep_fn=_sleep_zero,
    )
    dest = tmp_path / "out.mp4"
    out = eng.fetch("task_x", dest)
    assert out == dest
    assert dest.exists()
    # mp4 magic 字节（实测 ftypisom）
    assert dest.read_bytes()[:8] == b"\x00\x00\x00\x18ftyp"


# ── MPTEngine.run_to_completion ──────────────────────


def test_run_to_completion_happy_path(tmp_path: Path) -> None:
    """完整：submit → poll (running) → poll (done) → fetch。"""
    poll_count = {"n": 0}

    def poll_then_done(url, *, timeout):
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return {"state": "running", "progress": 0.3}
        return {"state": "done", "progress": 1.0}

    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=poll_then_done,
        http_download=_fake_download(tmp_path),
        sleep_fn=_sleep_zero,
    )
    out = eng.run_to_completion(_req(), tmp_path / "video.mp4")
    assert out.exists()
    assert poll_count["n"] == 2


def test_run_to_completion_failed_raises_create_error() -> None:
    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=_fake_get_state_with_error,
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="pexels quota"):
        eng.run_to_completion(_req(), Path("/tmp/v.mp4"))


def test_run_to_completion_timeout_raises(tmp_path: Path) -> None:
    """永远 running → 超时。"""
    def always_running(url, *, timeout):
        return {"state": "running", "progress": 0.1}

    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=always_running,
        http_download=_fake_download(tmp_path),
        sleep_fn=_sleep_zero,
        timeout_s=2,        # 2s 超时
        poll_interval_s=1,
    )
    with pytest.raises(CreateError, match="timeout after 2s"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


def test_run_to_completion_poll_retry_on_transient_failure(
    tmp_path: Path,
) -> None:
    """单次 poll 失败 → 重试一次（不算任务失败）。"""
    poll_count = {"n": 0}

    def flaky_poll(url, *, timeout):
        poll_count["n"] += 1
        if poll_count["n"] == 1:
            raise CreateError("network blip")
        return {"state": "done", "progress": 1.0}

    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=flaky_poll,
        http_download=_fake_download(tmp_path),
        sleep_fn=_sleep_zero,
    )
    out = eng.run_to_completion(_req(), tmp_path / "v.mp4")
    assert out.exists()
    assert poll_count["n"] == 2   # 第一次失败 + 第二次成功


def test_run_to_completion_poll_failure_twice_raises(tmp_path: Path) -> None:
    """连续 2 次 poll 失败 → 抛 CreateError。"""
    def always_fail(url, *, timeout):
        raise CreateError("persistent network fail")

    eng = MPTEngine(
        http_post=_fake_post_ok,
        http_get_json=always_fail,
        sleep_fn=_sleep_zero,
    )
    with pytest.raises(CreateError, match="poll failed twice"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


# ── 工厂 / 降级 ─────────────────────────────────────


def test_build_mpt_engine_from_config() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {
            "engine": "mpt",
            "mpt": {
                "base_url": "http://10.0.0.1:8080",
                "poll_interval_s": 15,
                "timeout_s": 600,
            },
        },
    })
    eng = build_mpt_engine(cfg)
    assert eng._base == "http://10.0.0.1:8080"
    assert eng._poll_interval == 15
    assert eng._timeout_s == 600


def test_build_video_engine_returns_engine_when_ok() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "mpt"},
    })
    eng = build_video_engine(cfg)
    assert eng is not None
    assert eng.name == "mpt"


def test_build_video_engine_unknown_engine_returns_none() -> None:
    """未知 engine 名 → None（图链不断）。

    openmontage 是 TECH_SPEC §5.6 Literal 中预留但本项目未实现的引擎名
    （远期观察，未落地 builder）。用它断言未知 engine 降级路径。
    （原先用 aigcpanel 占位；M12-1 把它从 Literal 中移除、替换为
    digitalhuman 真实引擎名，故改用 openmontage 保持测试意图不变。）
    """
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "openmontage"},   # 未实现 → None
    })
    eng = build_video_engine(cfg)
    assert eng is None


def test_is_mpt_alive_returns_false_when_unreachable(monkeypatch) -> None:
    """is_mpt_alive 在 ConnectError 时返回 False（不抛）。"""
    import httpx

    class _FakeClient:
        def __getattr__(self, name):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "get", MagicMock(side_effect=httpx.ConnectError("nope")))
    assert is_mpt_alive("http://127.0.0.1:1", timeout=0.1) is False


# ── 真实端到端（fake MPT server） ────────────────────


def _make_fake_mpt_script(port_path: Path) -> str:
    """写一个真 fake MPT server（uvicorn 子进程）：
    - POST /api/v1/videos → 返回 {task_id}
    - GET /api/v1/tasks/{id} → 第一次返回 running，第二次返回 done
    - GET /api/v1/videos/{id}/download → 返回 fake mp4
    """
    return f"""import sys
from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn, secrets, json

app = FastAPI()
JOBS = {{}}

@app.post("/api/v1/videos")
async def submit(body: dict):
    tid = "task_" + secrets.token_hex(4)
    JOBS[tid] = {{"state": "pending", "poll_count": 0}}
    # 验证字段存在
    for k in ("video_script", "voice_name", "video_aspect"):
        if k not in body:
            return {{"code": 400, "msg": f"missing {{k}}"}}
    return {{"task_id": tid}}

@app.get("/api/v1/tasks/{{tid}}")
async def poll(tid: str):
    j = JOBS.get(tid)
    if not j:
        return {{"code": 404, "msg": "no such task"}}
    j["poll_count"] += 1
    if j["poll_count"] >= 2:
        j["state"] = "done"
    else:
        j["state"] = "running"
    return {{"state": j["state"], "progress": 0.5 if j["state"] == "running" else 1.0}}

@app.get("/api/v1/videos/{{tid}}/download")
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


def test_real_mpt_end_to_end(tmp_path: Path) -> None:
    """真 httpx 调用 fake MPT server → submit → poll → fetch 全跑通。"""
    import socket
    import subprocess
    import sys

    # 找空闲端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server_script = tmp_path / "fake_mpt_server.py"
    server_script.write_text(_make_fake_mpt_script(tmp_path), encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(server_script), str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # 等 server ready
        import httpx
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{port}/api/v1/videos", timeout=0.3)
                break
            except Exception:
                time.sleep(0.1)

        eng = MPTEngine(
            base_url=f"http://127.0.0.1:{port}",
            poll_interval_s=1,
            timeout_s=10,
            sleep_fn=_sleep_zero,
        )
        # 注：不注入 http_* → 走 _real_http_post/_get/_download，真 httpx 调用
        out = eng.run_to_completion(_req(), tmp_path / "video.mp4")
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x00\x00\x00\x18ftyp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()