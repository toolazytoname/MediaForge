"""pipeline/creators/tts.py 测试（edge-tts 同步封装，M12-1 附带产物）。

不发真实网络请求、不依赖本机是否装了 edge-tts —— synth_fn 全部注入 mock。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.creators.tts import DEFAULT_VOICE, synthesize_speech
from pipeline.utils.errors import CreateError


async def _fake_synth_ok(text: str, dest: Path, voice: str) -> None:
    dest.write_bytes(f"AUDIO:{voice}:{text}".encode("utf-8"))


async def _fake_synth_raises(text: str, dest: Path, voice: str) -> None:
    raise RuntimeError("edge-tts network error")


def test_synthesize_speech_writes_dest(tmp_path: Path) -> None:
    dest = tmp_path / "out.mp3"
    out = synthesize_speech("你好世界", dest, synth_fn=_fake_synth_ok)
    assert out == dest
    assert dest.exists()
    assert b"AUDIO:" in dest.read_bytes()


def test_synthesize_speech_uses_default_voice(tmp_path: Path) -> None:
    dest = tmp_path / "out.mp3"
    synthesize_speech("你好", dest, synth_fn=_fake_synth_ok)
    assert DEFAULT_VOICE.encode("utf-8") in dest.read_bytes()


def test_synthesize_speech_custom_voice(tmp_path: Path) -> None:
    dest = tmp_path / "out.mp3"
    synthesize_speech(
        "你好", dest, voice="zh-CN-XiaoxiaoNeural", synth_fn=_fake_synth_ok,
    )
    assert b"zh-CN-XiaoxiaoNeural" in dest.read_bytes()


def test_synthesize_speech_empty_text_raises(tmp_path: Path) -> None:
    dest = tmp_path / "out.mp3"
    with pytest.raises(CreateError, match="empty text"):
        synthesize_speech("   ", dest, synth_fn=_fake_synth_ok)


def test_synthesize_speech_wraps_exception_in_create_error(tmp_path: Path) -> None:
    dest = tmp_path / "out.mp3"
    with pytest.raises(CreateError, match="tts synthesize failed"):
        synthesize_speech("你好", dest, synth_fn=_fake_synth_raises)
    # 失败时不留下垃圾临时文件 / 目标文件
    assert not dest.exists()
    assert not (tmp_path / "out.mp3.tmp").exists()


def test_synthesize_speech_creates_parent_dirs(tmp_path: Path) -> None:
    dest = tmp_path / "nested" / "dir" / "out.mp3"
    synthesize_speech("你好", dest, synth_fn=_fake_synth_ok)
    assert dest.exists()
