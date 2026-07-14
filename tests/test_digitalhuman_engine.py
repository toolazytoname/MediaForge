"""M12-1 DigitalHumanEngine（LatentSync 自托管数字人口播引擎）测试。

覆盖契约（HARD_PARTS §6.1 + TASKS.md M12-1 验收标准）：
- avatar_template 缺失/未知名字 → CreateError（不静默 fallback）
- submit 正常路径：TTS 函数 mock 返回假音频路径，http post mock 返回假
  prediction id，断言 job_id 正确
- poll：starting/processing/succeeded/failed/canceled → state 映射，
  progress 恒为 None（不被假象百分比骗，同 pixelle.py 教训）
- poll 404 → CreateError（task lost 语义）
- fetch：tmp → rename 落地到 dest（同时覆盖 data URI 和 URL 两种 output）
- 工厂：build_video_engine 按 engine="digitalhuman" 能构造出
  DigitalHumanEngine；且 digitalhuman 初始化失败不影响 mpt/pixelle 引擎

全部 mock 掉外部依赖（HTTP + TTS），不发真实网络请求，不依赖本机是否
装了 edge-tts。
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest

from pipeline.config import AppConfig
from pipeline.creators.video.base import VideoRequest
from pipeline.creators.video.digitalhuman import (
    DigitalHumanEngine,
    build_digitalhuman_engine,
    is_digitalhuman_alive,
)
from pipeline.creators.video import build_video_engine, _ENGINE_BUILDERS
from pipeline.utils.errors import CreateError


# ── helpers ─────────────────────────────────────────────


def _fake_tts(text, dest, *, voice):
    """假 TTS：不发真实网络请求，直接写个假音频文件。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"FAKEMP3" + text.encode("utf-8")[:20])
    return dest


def _fake_post_ok(url, *, json_body, timeout):
    return {"id": "pred_abc123", "status": "starting"}


def _fake_post_no_id(url, *, json_body, timeout):
    return {"status": "starting"}   # 缺 id


def _fake_get_with_status(status, error=None, output=None):
    def _f(url, *, timeout):
        payload = {"status": status}
        if error:
            payload["error"] = error
        if output is not None:
            payload["output"] = output
        return (200, payload)
    return _f


def _fake_get_404(url, *, timeout):
    return (404, None)


def _fake_get_500(url, *, timeout):
    return (500, None)


def _fake_download_factory():
    def _f(url, dest, *, timeout):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
        return dest
    return _f


def _sleep_zero(_):
    return None


# 真实存在的临时"形象素材"文件（避免 submit 内部 read_bytes 时因文件不存在
# 而 FileNotFoundError；形象素材是用户侧资产，测试用一个假 mp4 占位）
_AVATAR_DIR = tempfile.mkdtemp(prefix="mf_test_avatar_")
_AVATAR_PATH = str(Path(_AVATAR_DIR) / "default.mp4")
Path(_AVATAR_PATH).write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100)


def _req() -> VideoRequest:
    return VideoRequest(
        content_id="c_dh_test",
        script="今天讲一个 AI 新工具。它能在三秒内生成完整视频。",
        duration_s=60,
        aspect="9:16",
        style={"avatar_template": "default"},
    )


def _engine(**overrides) -> DigitalHumanEngine:
    kwargs = dict(
        avatar_templates={"default": _AVATAR_PATH},
        http_post=_fake_post_ok,
        tts_synthesize=_fake_tts,
        sleep_fn=_sleep_zero,
    )
    kwargs.update(overrides)
    return DigitalHumanEngine(**kwargs)


# ── submit：avatar_template 校验 ─────────────────────────


def test_submit_without_avatar_template_raises() -> None:
    eng = _engine()
    req = VideoRequest(
        content_id="c1", script="脚本", duration_s=30, aspect="9:16",
        style={},   # 没有 avatar_template
    )
    with pytest.raises(CreateError, match="avatar_template"):
        eng.submit(req)


def test_submit_with_unknown_avatar_template_raises() -> None:
    eng = _engine()
    req = VideoRequest(
        content_id="c1", script="脚本", duration_s=30, aspect="9:16",
        style={"avatar_template": "nonexistent"},
    )
    with pytest.raises(CreateError, match="not configured"):
        eng.submit(req)


# ── submit：正常路径 ─────────────────────────────────────


def test_submit_returns_job_id() -> None:
    eng = _engine()
    job_id = eng.submit(_req())
    assert job_id == "pred_abc123"


def test_submit_calls_tts_with_script() -> None:
    captured = {}

    def capture_tts(text, dest, *, voice):
        captured["text"] = text
        captured["voice"] = voice
        dest.write_bytes(b"FAKEMP3")
        return dest

    eng = _engine(tts_synthesize=capture_tts, tts_voice="zh-CN-XiaoxiaoNeural")
    eng.submit(_req())
    assert captured["text"] == _req().script
    assert captured["voice"] == "zh-CN-XiaoxiaoNeural"


def test_submit_posts_video_and_audio_input() -> None:
    captured = {}

    def capture_post(url, *, json_body, timeout):
        captured.update(json_body)
        return {"id": "pred_1"}

    eng = _engine(http_post=capture_post)
    eng.submit(_req())
    assert "input" in captured
    assert "video" in captured["input"]
    assert "audio" in captured["input"]
    # 本地文件走 data URI（不共享文件系统的防御性选择）
    assert captured["input"]["audio"].startswith("data:")


def test_submit_without_job_id_raises() -> None:
    eng = _engine(http_post=_fake_post_no_id)
    with pytest.raises(CreateError, match="no prediction id"):
        eng.submit(_req())


# ── poll：状态映射 ───────────────────────────────────────


@pytest.mark.parametrize(
    "cog_status,expected_state",
    [
        ("starting", "running"),
        ("processing", "running"),
        ("succeeded", "done"),
        ("failed", "failed"),
        ("canceled", "failed"),
    ],
)
def test_poll_maps_cog_status_to_state(cog_status, expected_state) -> None:
    eng = _engine(http_get_json=_fake_get_with_status(cog_status))
    status = eng.poll("pred_1")
    assert status.state == expected_state


def test_poll_progress_is_always_none() -> None:
    """Cog predictions API 无进度百分比字段 → 强制 None。"""
    eng = _engine(http_get_json=_fake_get_with_status("processing"))
    status = eng.poll("pred_1")
    assert status.progress is None


def test_poll_failed_carries_error() -> None:
    eng = _engine(
        http_get_json=_fake_get_with_status("failed", error="GPU OOM"),
    )
    status = eng.poll("pred_1")
    assert status.state == "failed"
    assert "GPU OOM" in status.error


def test_poll_404_raises_task_lost() -> None:
    eng = _engine(http_get_json=_fake_get_404)
    with pytest.raises(CreateError, match="task lost"):
        eng.poll("pred_1")


def test_poll_500_raises_create_error() -> None:
    eng = _engine(http_get_json=_fake_get_500)
    with pytest.raises(CreateError):
        eng.poll("pred_1")


# ── fetch：tmp → rename ──────────────────────────────────


def test_fetch_downloads_url_output(tmp_path: Path) -> None:
    eng = _engine(
        http_get_json=_fake_get_with_status(
            "succeeded", output="http://127.0.0.1:5000/out.mp4",
        ),
        http_download=_fake_download_factory(),
    )
    dest = tmp_path / "video.mp4"
    out = eng.fetch("pred_1", dest)
    assert out == dest
    assert dest.exists()
    assert not (tmp_path / "video.mp4.tmp").exists()   # tmp 已 rename 干净


def test_fetch_decodes_data_uri_output(tmp_path: Path) -> None:
    raw = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 50
    data_uri = "data:video/mp4;base64," + base64.b64encode(raw).decode("ascii")
    eng = _engine(
        http_get_json=_fake_get_with_status("succeeded", output=data_uri),
    )
    dest = tmp_path / "video.mp4"
    out = eng.fetch("pred_1", dest)
    assert out == dest
    assert dest.read_bytes() == raw
    assert not (tmp_path / "video.mp4.tmp").exists()


def test_fetch_no_output_raises() -> None:
    eng = _engine(
        http_get_json=_fake_get_with_status("succeeded", output=None),
    )
    with pytest.raises(CreateError, match="no usable output"):
        eng.fetch("pred_1", Path("/tmp/whatever.mp4"))


def test_fetch_task_lost_raises() -> None:
    eng = _engine(http_get_json=_fake_get_404)
    with pytest.raises(CreateError, match="task lost"):
        eng.fetch("pred_1", Path("/tmp/whatever.mp4"))


# ── run_to_completion ────────────────────────────────────


def test_run_to_completion_happy_path(tmp_path: Path) -> None:
    poll_count = {"n": 0}

    def poll_then_done(url, *, timeout):
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return (200, {"status": "processing"})
        return (200, {"status": "succeeded", "output": "http://x/out.mp4"})

    eng = _engine(
        http_get_json=poll_then_done,
        http_download=_fake_download_factory(),
    )
    out = eng.run_to_completion(_req(), tmp_path / "v.mp4")
    assert out.exists()
    # 2 次 poll（running→succeeded）+ 1 次 fetch 内部再 GET 一次拿 output
    # （digitalhuman 没有独立下载端点，output 字段就在 predictions 记录里）
    assert poll_count["n"] == 3


def test_run_to_completion_failed_raises(tmp_path: Path) -> None:
    eng = _engine(
        http_get_json=_fake_get_with_status("failed", error="bad avatar"),
    )
    with pytest.raises(CreateError, match="bad avatar"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


def test_run_to_completion_timeout_raises(tmp_path: Path) -> None:
    eng = _engine(
        http_get_json=_fake_get_with_status("processing"),
        timeout_s=2,
        poll_interval_s=1,
    )
    with pytest.raises(CreateError, match="timeout after 2s"):
        eng.run_to_completion(_req(), tmp_path / "v.mp4")


# ── 工厂 / 降级 ──────────────────────────────────────────


def test_build_digitalhuman_engine_from_config() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {
            "engine": "digitalhuman",
            "digitalhuman": {
                "base_url": "http://10.0.0.5:5000",
                "poll_interval_s": 15,
                "timeout_s": 900,
                "tts_voice": "zh-CN-XiaoxiaoNeural",
                "avatar_templates": {"default": "assets/avatars/default.mp4"},
            },
        },
    })
    eng = build_digitalhuman_engine(cfg)
    assert eng._base == "http://10.0.0.5:5000"
    assert eng._poll_interval == 15
    assert eng._timeout_s == 900
    assert eng._tts_voice == "zh-CN-XiaoxiaoNeural"
    assert eng._avatar_templates == {"default": "assets/avatars/default.mp4"}


def test_build_video_engine_digitalhuman_when_selected() -> None:
    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "digitalhuman"},
    })
    eng = build_video_engine(cfg)
    assert eng is not None
    assert eng.name == "digitalhuman"


def test_all_three_engines_registered_in_builders() -> None:
    assert set(_ENGINE_BUILDERS.keys()) >= {"mpt", "pixelle", "digitalhuman"}


def test_digitalhuman_init_failure_does_not_affect_other_engines(monkeypatch) -> None:
    """digitalhuman 工厂初始化失败（如依赖缺失）→ build_video_engine 返回
    None，不往上抛异常；且不影响 mpt/pixelle 引擎的可构造性。"""
    import pipeline.creators.video as video_mod

    def boom(cfg, **kwargs):
        raise RuntimeError("GPU/cog service missing")

    monkeypatch.setitem(video_mod._ENGINE_BUILDERS, "digitalhuman", boom)

    cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "digitalhuman"},
    })
    eng = video_mod.build_video_engine(cfg)
    assert eng is None   # 不往上抛，降级为 None

    # mpt / pixelle 仍能正常构造，不受影响
    mpt_cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "mpt"},
    })
    mpt_eng = video_mod.build_video_engine(mpt_cfg)
    assert mpt_eng is not None
    assert mpt_eng.name == "mpt"

    px_cfg = AppConfig.model_validate({
        "pillars": [{"id": "ai_daily", "name": "x",
                     "description": "y", "scoring_hint": "z"}],
        "llm": {"tiers": {"cheap": "h", "creative": "s", "critical": "s"}},
        "video": {"engine": "pixelle"},
    })
    px_eng = video_mod.build_video_engine(px_cfg)
    assert px_eng is not None
    assert px_eng.name == "pixelle"


def test_is_digitalhuman_alive_handles_errors(monkeypatch) -> None:
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            httpx.ConnectError("nope"),
                        ))
    assert is_digitalhuman_alive("http://127.0.0.1:1", timeout=0.1) is False
