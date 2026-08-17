"""Video creation bridge: script derive + durable submit/poll/cancel.

Jobs persist in durable_jobs. Engines are rebuilt from request_json + engine
name after process restart. Request body is JSON only — no in-memory objects.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from pipeline import db, deliverables
from pipeline.creators import derivative, llm
from pipeline.creators.video import (
    build_digitalhuman_engine,
    build_mpt_engine,
    build_pixelle_engine,
)
from pipeline.creators.video.base import VideoEngine, VideoRequest
from pipeline.creators.video.fake import build_fake_engine
from pipeline.jobs import store as job_store
from pipeline.models import Content, ContentStatus
from pipeline.projects import DEFAULT_PROJECTS_ROOT
from pipeline.utils.errors import BudgetExceeded, CreateError, UnpricedModelError
from pipeline.utils.ids import new_id
from pipeline.webui import serialize


class ContentNotFoundError(ValueError):
    """content_id 不存在 → 404。"""


class ContentStatusError(ValueError):
    """content status 不在允许集合 → 400。"""


class InvalidEngineError(ValueError):
    """engine 参数不在受支持集合 → 400。"""


class EngineUnavailableError(ValueError):
    """引擎工厂返回 None 或构造异常（服务不可用）→ 503。"""


class JobNotFoundError(ValueError):
    """job_id 不存在于 durable_jobs → 404。"""


_ALLOWED_STATUSES: frozenset[str] = frozenset({
    ContentStatus.DRAFT.value,
    ContentStatus.GATED.value,
    ContentStatus.APPROVED.value,
    ContentStatus.REJECTED_BY_HUMAN.value,
})

_ENGINE_BUILDERS: dict[str, Callable[..., VideoEngine]] = {
    "mpt": build_mpt_engine,
    "pixelle": build_pixelle_engine,
    "digitalhuman": build_digitalhuman_engine,
    "fake": build_fake_engine,
}

_ENGINE_STATE = {
    "pending": "queued",
    "queued": "queued",
    "running": "running",
    "done": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}

_SECRET_MARKERS = (
    "token", "secret", "password", "cookie", "authorization",
    "api_key", "apikey", "pexels",
)

_MAX_CANONICAL_CHARS = 4000
_CHARS_PER_SECOND = 4.5
_DEFAULT_TIMEOUT_S = 1200


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
    """把 content 的 canonical.md 派生成口播稿（LLM 二次改写）。"""
    del cfg
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


def _build_engine(cfg, engine: str) -> VideoEngine:
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
    return eng


def _redact_style(style: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in style.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            out[key] = "[redacted]"
        else:
            out[key] = value
    json.dumps(out)
    return out


def _timeout_s(cfg, engine: str, override: int | None) -> int:
    if override is not None and override > 0:
        return override
    video = getattr(cfg, "video", None)
    section = getattr(video, engine, None) if video is not None else None
    return int(getattr(section, "timeout_s", _DEFAULT_TIMEOUT_S) or _DEFAULT_TIMEOUT_S)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _timed_out(job: job_store.DurableJob, now: str) -> bool:
    request = job.request()
    timeout_s = request.get("timeout_s")
    if not isinstance(timeout_s, int) or timeout_s <= 0:
        return False
    deadline = _parse_iso(job.created_at) + timedelta(seconds=timeout_s)
    return _parse_iso(now) >= deadline


def _output_url(content: Content | None, engine: str | None) -> str | None:
    if content is None or not engine:
        return None
    prefix = serialize.content_output_url_prefix(content)
    if not prefix:
        return None
    return prefix + f"video_{engine}.mp4"


def _public_job(conn, job: job_store.DurableJob) -> dict[str, Any]:
    content = db.get_content(conn, job.content_id) if job.content_id else None
    return {
        "job_id": job.id,
        "id": job.id,
        "kind": job.kind,
        "content_id": job.content_id,
        "project_id": job.project_id,
        "deliverable_id": job.deliverable_id,
        "engine": job.engine,
        "state": job.state,
        "progress": job.progress,
        "error": job.error,
        "output_path": job.result_path,
        "output_url": _output_url(content, job.engine) if job.result_path else None,
        "result_path": job.result_path,
        "cost_usd": job.cost_usd,
        "idempotency_key": job.idempotency_key,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


def _attach_deliverable(
    *,
    project_id: str,
    deliverable_id: str,
    job_id: str,
    now: str,
    projects_root: str | Path,
) -> None:
    deliverables.attach_video_job(
        project_id, deliverable_id, job_id=job_id, now=now,
        projects_root=projects_root,
    )


def submit_video_job(
    conn,
    cfg,
    content_id: str,
    engine: str,
    script: str,
    duration_s: int,
    aspect: str,
    style: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    project_id: str | None = None,
    deliverable_id: str | None = None,
    timeout_s: int | None = None,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> dict[str, Any]:
    """Submit a video job and persist it. Duplicate idempotency_key returns the first row."""
    _get_content_or_raise(conn, content_id)
    if deliverable_id:
        if not project_id:
            raise deliverables.DeliverablesError("project_id is required to attach a video job")
        current = deliverables.get_deliverable(
            project_id, deliverable_id, projects_root=projects_root,
        )
        if current.kind != deliverables.KIND_VIDEO:
            raise deliverables.DeliverablesError("deliverable is not a video")

    key = idempotency_key or new_id("idem")
    existing = job_store.get_job_by_key(conn, key)
    if existing is not None:
        return _public_job(conn, existing)

    eng = _build_engine(cfg, engine)
    req = VideoRequest(
        content_id=content_id, script=script, duration_s=duration_s,
        aspect=aspect, style=dict(style),
    )
    engine_job_id = eng.submit(req)

    now = db.now_utc()
    request = {
        "content_id": content_id,
        "script": script,
        "duration_s": duration_s,
        "aspect": aspect,
        "style": _redact_style(dict(style)),
        "engine_job_id": engine_job_id,
        "timeout_s": _timeout_s(cfg, engine, timeout_s),
    }
    try:
        job = job_store.insert_job(
            conn,
            kind="video_render",
            idempotency_key=key,
            request=request,
            engine=engine,
            project_id=project_id,
            deliverable_id=deliverable_id,
            content_id=content_id,
            state="queued",
            now=now,
        )
    except sqlite3.IntegrityError:
        raced = job_store.get_job_by_key(conn, key)
        if raced is not None:
            return _public_job(conn, raced)
        raise

    if project_id and deliverable_id:
        _attach_deliverable(
            project_id=project_id, deliverable_id=deliverable_id,
            job_id=job.id, now=now, projects_root=projects_root,
        )
    return _public_job(conn, job)


def _dest_for(content: Content, engine: str) -> Path:
    out_dir = serialize._content_output_dir(content)
    if out_dir is None:
        out_dir = Path(content.canonical_path).parent
    dest = out_dir / f"video_{engine}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _fetch_and_finalize(
    conn, job: job_store.DurableJob, eng: VideoEngine, engine_job_id: str, now: str,
) -> dict[str, Any]:
    content_id = job.content_id
    if content_id is None:
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error="job has no content_id", now=now,
        )
        return _public_job(conn, finished or job)

    content = db.get_content(conn, content_id)
    if content is None:
        finished = job_store.try_finish_job(
            conn, job.id, state="failed",
            error=f"content {content_id} no longer exists", now=now,
        )
        return _public_job(conn, finished or job)

    dest = _dest_for(content, job.engine or "video")
    try:
        eng.fetch(engine_job_id, dest)
    except CreateError as e:
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error=str(e), now=now,
        )
        return _public_job(conn, finished or job)

    finished = job_store.try_finish_job(
        conn, job.id, state="done", result_path=str(dest),
        cost_usd=None, progress=1.0, now=now,
    )
    current = finished or job_store.get_job(conn, job.id) or job
    if current.state == "done" and current.result_path:
        derivative._update_formats_field(
            conn, content_id, (f"video_{job.engine}",), now,
        )
    elif current.state != "done" and current.result_path is None:
        dest.unlink(missing_ok=True)
    return _public_job(conn, current)


def poll_video_job(conn, cfg, job_id: str, *, now: str | None = None) -> dict[str, Any]:
    """Query job status from SQLite; rebuild the engine from request_json."""
    job = job_store.get_job(conn, job_id)
    if job is None:
        raise JobNotFoundError(f"video job {job_id} not found")

    stamp = now or db.now_utc()
    if job.state in job_store.TERMINAL_STATES:
        return _public_job(conn, job)

    if _timed_out(job, stamp):
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error="timeout: exceeded timeout_s", now=stamp,
        )
        return _public_job(conn, finished or job)

    request = job.request()
    engine_job_id = request.get("engine_job_id")
    if not isinstance(engine_job_id, str) or not engine_job_id:
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error="request_json missing engine_job_id",
            now=stamp,
        )
        return _public_job(conn, finished or job)

    eng = _build_engine(cfg, job.engine or "")
    try:
        status = eng.poll(engine_job_id)
    except CreateError as e:
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error=str(e), now=stamp,
        )
        return _public_job(conn, finished or job)

    mapped = _ENGINE_STATE.get(str(status.state).lower(), "running")
    if mapped == "done":
        return _fetch_and_finalize(conn, job, eng, engine_job_id, stamp)
    if mapped == "failed":
        finished = job_store.try_finish_job(
            conn, job.id, state="failed", error=status.error, now=stamp,
        )
        return _public_job(conn, finished or job)
    if mapped == "cancelled":
        finished = job_store.try_finish_job(
            conn, job.id, state="cancelled", error=status.error or "cancelled", now=stamp,
        )
        return _public_job(conn, finished or job)

    updated = job_store.update_job_progress(
        conn, job.id, state=mapped, progress=status.progress,
        error=status.error, now=stamp,
    )
    return _public_job(conn, updated or job)


def cancel_video_job(conn, job_id: str, *, now: str | None = None) -> dict[str, Any]:
    """Mark a non-terminal job cancelled. Later engine success is ignored."""
    job = job_store.get_job(conn, job_id)
    if job is None:
        raise JobNotFoundError(f"video job {job_id} not found")
    stamp = now or db.now_utc()
    if job.state in job_store.TERMINAL_STATES:
        return _public_job(conn, job)
    finished = job_store.try_finish_job(
        conn, job.id, state="cancelled", error="cancelled", now=stamp,
    )
    return _public_job(conn, finished or job)


__all__ = [
    "derive_video_script",
    "submit_video_job",
    "poll_video_job",
    "cancel_video_job",
    "ContentNotFoundError",
    "ContentStatusError",
    "InvalidEngineError",
    "EngineUnavailableError",
    "JobNotFoundError",
    "BudgetExceeded",
    "CreateError",
    "UnpricedModelError",
]
