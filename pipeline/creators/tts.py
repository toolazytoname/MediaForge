"""语音合成封装（edge-tts，TECH_SPEC HARD_PARTS §6 + §6.1）。

edge-tts（微软 Azure 语音合成的免费逆向封装库）给 MPT 引擎（服务端 TTS）
和 digitalhuman 引擎（客户端 TTS，见 HARD_PARTS §6.1）共享使用。这是
genuinely new 代码——MPT 的 TTS 是它自己服务端做的，客户端无可抽取实现。

edge-tts 原生 Python API 是异步的（`edge_tts.Communicate(text, voice).save(path)`
是协程）。本模块用 `asyncio.run(...)` 把它桥接成同步接口，不让异步契约泄漏
出去——其余引擎（mpt/pixelle/digitalhuman）的 submit/poll/fetch 都是同步接口。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from pipeline.utils.errors import CreateError

# 默认音色（M0-0 决策：zh-CN-YunxiNeural，A/B 后再定）
DEFAULT_VOICE = "zh-CN-YunxiNeural"


async def _real_synthesize_async(text: str, dest: Path, voice: str) -> None:
    """默认实现：调用 edge_tts.Communicate(...).save(...)。"""
    import edge_tts  # 延迟 import：本机未装该包时，只有真正调用才报错

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(dest))


def synthesize_speech(
    text: str,
    dest: Path,
    *,
    voice: str = DEFAULT_VOICE,
    synth_fn: Callable[[str, Path, str], Awaitable[None]] | None = None,
) -> Path:
    """把 text 合成语音写入 dest（mp3），返回 dest。失败抛 CreateError。

    synth_fn 可注入（测试用），签名为 `async def f(text, tmp_path, voice)`；
    默认调用 edge-tts。tmp → rename 落盘（HARD_PARTS §5 幂等模式）。
    """
    if not text or not text.strip():
        raise CreateError("tts synthesize: empty text")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    async_fn = synth_fn or _real_synthesize_async

    try:
        asyncio.run(async_fn(text, tmp, voice))
    except CreateError:
        if tmp.exists():
            tmp.unlink()
        raise
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        raise CreateError(f"tts synthesize failed: {e!r}") from e

    if not tmp.exists():
        raise CreateError("tts synthesize produced no output file")
    tmp.rename(dest)
    return dest


__all__ = ["synthesize_speech", "DEFAULT_VOICE"]
