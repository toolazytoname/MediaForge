"""M12-3：视频创作向导薄 bridge 层（脚本派生 + 提交/轮询视频生成任务）。

不重写 VideoEngine 编排逻辑，只做：
  - content 存在性 + 状态校验（与 derivative_bridge 相同白名单）
  - 调 pipeline.creators.llm.complete 派生口播稿（脚本主权在我方 LLM，
    不让引擎自己编文案，同 HARD_PARTS §6 / M12-1 教训）
  - 用 pipeline.creators.video 的引擎工厂 submit/poll/fetch，任务状态
    用进程内内存字典追踪（不落库——视频任务是 UI 触发的临时任务，
    TECH_SPEC 冻结 schema 不允许新增表/字段）
  - poll 到 done 时下载成品 + 复用 derivative._update_formats_field 写回
    contents.formats（已有的冻结列，不新增字段）

并发注意：FastAPI 同步 handler 跑在线程池里，_JOBS 字典用 threading.Lock
保护；每次更新走"整体替换新 dict"（immutable 更新模式），不原地改字段。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from pipeline import db
from pipeline.creators import derivative, llm
from pipeline.creators.video import (
    build_digitalhuman_engine,
    build_mpt_engine,
    build_pixelle_engine,
)
from pipeline.creators.video.base import VideoEngine, VideoRequest
from pipeline.models import Content, ContentStatus
from pipeline.utils.errors import BudgetExceeded, CreateError
from pipeline.utils.ids import new_id
from pipeline.webui import serialize


# ── Exceptions（API 层映射：404 / 400 / 400 / 503 / 404）────────


class ContentNotFoundError(ValueError):
    """content_id 不存在 → 404。"""


class ContentStatusError(ValueError):
    """content status 不在允许集合 → 400。"""


class InvalidEngineError(ValueError):
    """engine 参数不在受支持集合 → 400。"""


class EngineUnavailableError(ValueError):
    """引擎工厂返回 None 或构造异常（服务不可用）→ 503。

    与 CreateError（engine.submit 内的业务性失败，如缺形象模板）区分：
    前者是"引擎服务没起来"，后者是"服务在跑但这次提交参数有问题"。
    """


class JobNotFoundError(ValueError):
    """job_id 不存在于内存字典 → 404。"""


# ── 状态白名单（与 derivative_bridge 完全相同）─────────────


_ALLOWED_STATUSES: frozenset[str] = frozenset({
    ContentStatus.DRAFT.value,
    ContentStatus.GATED.value,
    ContentStatus.APPROVED.value,
    ContentStatus.REJECTED_BY_HUMAN.value,
})


def _check_status(c: Content) -> None:
    if c.status not in _ALLOWED_STATUSES:
        raise ContentStatusError(
            f"content {c.id} status={c.status!r} not in allowed "
            f"{sorted(_ALLOWED_STATUSES)}"
        )


def _get_content_or_raise(conn, content_id: str) -> Content:
    c = db.get_content(conn, content_id)
    if c is None:
        raise ContentNotFoundError(f"content {content_id} not found")
    _check_status(c)
    return c


# ── 内存任务字典（不落库；FastAPI 线程池并发访问需加锁） ────


_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

_ENGINE_BUILDERS: dict[str, Callable[..., VideoEngine]] = {
    "mpt": build_mpt_engine,
    "pixelle": build_pixelle_engine,
    "digitalhuman": build_digitalhuman_engine,
}


def _public_job(record: dict[str, Any]) -> dict[str, Any]:
    """过滤掉下划线前缀的内部字段（engine 实例 / engine 内部 job_id）。"""
    return {k: v for k, v in record.items() if not k.startswith("_")}


# ── 1) 口播稿派生（LLM，脚本主权在我方） ────────────────────


_MAX_CANONICAL_CHARS = 4000
# 中文语速估算：4-5 字/秒，取中值 4.5
_CHARS_PER_SECOND = 4.5


def _build_script_prompt(canonical_text: str, target_chars: int) -> str:
    return (
        "请把下面这篇长文改写成一段适合数字人/短视频口播的讲稿。\n"
        "要求：\n"
        f"1. 目标字数约 {target_chars} 字（按中文语速 4-5 字/秒估算，不要"
        "过度偏离）。\n"
        "2. 口语化、可直接朗读，避免书面语和长难句。\n"
        "3. 不要输出任何 markdown 标记（不要 #、*、-、代码块等），只输出"
        "纯文本口播稿。\n"
        "4. 不要输出任何解释或前后缀说明，只输出口播稿正文本身。\n\n"
        f"原文：\n{canonical_text}"
    )


def derive_video_script(
    conn,
    cfg,
    content_id: str,
    duration_s: int,
) -> str:
    """把 content 的 canonical.md 派生成口播稿（LLM 二次改写）。

    Args:
        conn: SQLite 连接。
        cfg: AppConfig（当前未直接使用，保留供未来按 pillar 定制 prompt）。
        content_id: 内容 id。
        duration_s: 目标时长（秒），用于估算目标字数。

    Returns:
        LLM 产出的口播稿文本。

    Raises:
        ContentNotFoundError / ContentStatusError
        CreateError: canonical.md 读取失败。
        BudgetExceeded: 原样上抛（系统级）。
    """
    del cfg  # 当前未使用，保留签名以备未来按 pillar 定制 prompt
    c = _get_content_or_raise(conn, content_id)

    canonical_path = Path(c.canonical_path)
    try:
        full_text = canonical_path.read_text(encoding="utf-8")
    except OSError as e:
        raise CreateError(
            f"cannot read canonical.md for {content_id}: {e!r}"
        ) from e
    truncated = full_text[:_MAX_CANONICAL_CHARS]

    target_chars = max(1, round(duration_s * _CHARS_PER_SECOND))
    prompt = _build_script_prompt(truncated, target_chars)
    return llm.complete(
        prompt, stage="video_script", ref_id=content_id,
        model_tier="creative", conn=conn,
    )


# ── 2) 提交视频生成任务 ─────────────────────────────────────


def submit_video_job(
    conn,
    cfg,
    content_id: str,
    engine: str,
    script: str,
    duration_s: int,
    aspect: str,
    style: dict[str, Any],
) -> dict[str, Any]:
    """提交一次视频生成任务（不落库，进程内内存字典追踪）。

    Raises:
        ContentNotFoundError / ContentStatusError
        InvalidEngineError: engine 不在 {"mpt","pixelle","digitalhuman"}。
        EngineUnavailableError: 引擎工厂构造失败或返回 None（服务不可用）。
        CreateError: engine.submit() 业务性失败（如数字人缺形象模板）—
            原样上抛，不在此处捕获，交由 API 层映射 400。
    """
    _get_content_or_raise(conn, content_id)

    builder = _ENGINE_BUILDERS.get(engine)
    if builder is None:
        raise InvalidEngineError(
            f"unknown engine {engine!r}; must be one of "
            f"{sorted(_ENGINE_BUILDERS)}"
        )

    try:
        eng = builder(cfg)
    except Exception as e:
        raise EngineUnavailableError(
            f"engine {engine!r} unavailable: {type(e).__name__}: {e}"
        ) from e
    if eng is None:
        raise EngineUnavailableError(
            f"engine {engine!r} builder returned None (service unavailable)"
        )

    req = VideoRequest(
        content_id=content_id, script=script, duration_s=duration_s,
        aspect=aspect, style=dict(style),
    )
    # CreateError 原样上抛（业务性失败，如缺形象模板），不在此处捕获
    engine_job_id = eng.submit(req)

    job_id = new_id("vjob")
    now = db.now_utc()
    record: dict[str, Any] = {
        "job_id": job_id,
        "content_id": content_id,
        "engine": engine,
        "state": "submitted",
        "progress": None,
        "error": None,
        "output_path": None,
        "output_url": None,
        "created_at": now,
        "updated_at": now,
        "_engine": eng,
        "_engine_job_id": engine_job_id,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = record
    return _public_job(record)


# ── 3) 轮询任务状态（幂等；done 时触发 fetch + 写回 formats） ─


def _fetch_and_finalize(
    conn, record: dict[str, Any], now: str,
) -> dict[str, Any]:
    """poll 到 done 且 output_path 还没填过时：fetch 成片 + 写回 formats。

    失败（CreateError）→ 标记 failed，返回新 record（不抛出，poll 语义
    是"查询状态"，失败结果体现在返回的 dict 里）。
    """
    content_id = record["content_id"]
    engine_name = record["engine"]
    eng: VideoEngine = record["_engine"]
    engine_job_id = record["_engine_job_id"]

    content = db.get_content(conn, content_id)
    if content is None:
        return {
            **record, "state": "failed",
            "error": f"content {content_id} no longer exists",
            "updated_at": now,
        }

    out_dir = serialize._content_output_dir(content)
    if out_dir is None:
        out_dir = Path(content.canonical_path).parent
    dest = out_dir / f"video_{engine_name}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        eng.fetch(engine_job_id, dest)
    except CreateError as e:
        return {
            **record, "state": "failed", "error": str(e), "updated_at": now,
        }

    output_url = (
        serialize.content_output_url_prefix(content)
        + f"video_{engine_name}.mp4"
    )
    derivative._update_formats_field(
        conn, content_id, (f"video_{engine_name}",), now,
    )
    return {
        **record,
        "state": "done",
        "output_path": str(dest),
        "output_url": output_url,
        "updated_at": now,
    }


def poll_video_job(conn, cfg, job_id: str) -> dict[str, Any]:
    """查询任务状态；done 时触发 fetch（首次）+ 写回 contents.formats。

    幂等：已是终态（done/failed）直接返回当前记录，不重复调用引擎。

    Raises:
        JobNotFoundError: job_id 不存在。
    """
    del cfg  # 引擎实例已缓存在 record 里，无需重新读 config

    with _JOBS_LOCK:
        record = _JOBS.get(job_id)
    if record is None:
        raise JobNotFoundError(f"video job {job_id} not found")

    if record["state"] in ("done", "failed"):
        return _public_job(record)

    eng: VideoEngine = record["_engine"]
    engine_job_id = record["_engine_job_id"]
    now = db.now_utc()

    try:
        status = eng.poll(engine_job_id)
    except CreateError as e:
        new_record = {**record, "state": "failed", "error": str(e), "updated_at": now}
        with _JOBS_LOCK:
            _JOBS[job_id] = new_record
        return _public_job(new_record)

    new_record = {
        **record,
        "state": status.state,
        "progress": status.progress,
        "error": status.error,
        "updated_at": now,
    }

    if status.state == "done" and new_record["output_path"] is None:
        new_record = _fetch_and_finalize(conn, new_record, now)

    with _JOBS_LOCK:
        _JOBS[job_id] = new_record
    return _public_job(new_record)


__all__ = [
    "derive_video_script",
    "submit_video_job",
    "poll_video_job",
    "ContentNotFoundError",
    "ContentStatusError",
    "InvalidEngineError",
    "EngineUnavailableError",
    "JobNotFoundError",
    "BudgetExceeded",  # re-export 方便调用方
    "CreateError",  # re-export 方便调用方
]
