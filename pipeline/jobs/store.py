"""SQLite persistence for RFC §5.6 durable_jobs.

request_json / identity columns are append-only. Mutable columns are
state, progress, error, finished_at, result_path, cost_usd, updated_at.
Unknown cost is NULL; writing 0 is refused.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from pipeline import db
from pipeline.utils.errors import UnpricedModelError
from pipeline.utils.ids import new_id
from pipeline.utils.redact import redact_value

JOB_STATES = frozenset({"queued", "running", "done", "failed", "cancelled"})
TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})
_JOB_KINDS = frozenset({"video_render", "image_gen", "delivery", "export"})
_MUTABLE = frozenset({
    "state", "progress", "error", "finished_at", "result_path", "cost_usd", "updated_at",
})


@dataclass(frozen=True)
class DurableJob:
    id: str
    kind: str
    project_id: str | None
    deliverable_id: str | None
    content_id: str | None
    engine: str | None
    state: str
    progress: float | None
    attempt: int
    idempotency_key: str
    request_json: str
    result_path: str | None
    error: str | None
    cost_usd: float | None
    created_at: str
    updated_at: str
    finished_at: str | None

    def request(self) -> dict[str, Any]:
        payload = json.loads(self.request_json)
        if not isinstance(payload, dict):
            raise ValueError("durable_jobs.request_json must be an object")
        return payload


def insert_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    idempotency_key: str,
    request: dict[str, Any],
    engine: str | None = None,
    project_id: str | None = None,
    deliverable_id: str | None = None,
    content_id: str | None = None,
    state: str = "queued",
    progress: float | None = None,
    job_id: str | None = None,
    now: str | None = None,
) -> DurableJob:
    if kind not in _JOB_KINDS:
        raise ValueError(f"invalid durable job kind: {kind}")
    if state not in JOB_STATES:
        raise ValueError(f"invalid durable job state: {state}")
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    sanitized = redact_value(request)
    body = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("request_json must be a JSON object")
    stamp = now or db.now_utc()
    job = DurableJob(
        job_id or new_id("job"), kind, project_id, deliverable_id, content_id, engine,
        state, progress, 1, idempotency_key, body, None, None, None, stamp, stamp, None,
    )
    conn.execute(
        """
        INSERT INTO durable_jobs (
            id, kind, project_id, deliverable_id, content_id, engine, state, progress,
            attempt, idempotency_key, request_json, result_path, error, cost_usd,
            created_at, updated_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.id, job.kind, job.project_id, job.deliverable_id, job.content_id,
            job.engine, job.state, job.progress, job.attempt, job.idempotency_key,
            job.request_json, job.result_path, job.error, job.cost_usd,
            job.created_at, job.updated_at, job.finished_at,
        ),
    )
    conn.commit()
    return job


def get_job(conn: sqlite3.Connection, job_id: str) -> DurableJob | None:
    row = conn.execute("SELECT * FROM durable_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def get_job_by_key(conn: sqlite3.Connection, idempotency_key: str) -> DurableJob | None:
    row = conn.execute(
        "SELECT * FROM durable_jobs WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    return _row_to_job(row) if row else None


def update_job_progress(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    state: str,
    now: str,
    progress: float | None = None,
    error: str | None = None,
) -> DurableJob | None:
    if state not in JOB_STATES - TERMINAL_STATES:
        raise ValueError(f"progress update cannot use terminal state {state}")
    conn.execute(
        """
        UPDATE durable_jobs
        SET state = ?, progress = ?, error = ?, updated_at = ?
        WHERE id = ? AND state NOT IN ('done', 'failed', 'cancelled')
        """,
        (state, progress, error, now, job_id),
    )
    conn.commit()
    return get_job(conn, job_id)


def try_finish_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    state: str,
    now: str,
    error: str | None = None,
    result_path: str | None = None,
    cost_usd: float | None = None,
    progress: float | None = None,
) -> DurableJob | None:
    if state not in TERMINAL_STATES:
        raise ValueError(f"finish state must be terminal, got {state}")
    if cost_usd == 0:
        raise UnpricedModelError("durable_job")
    if cost_usd is not None and cost_usd < 0:
        raise UnpricedModelError("durable_job")
    billed = None if state != "done" else cost_usd
    conn.execute(
        """
        UPDATE durable_jobs
        SET state = ?, progress = COALESCE(?, progress), error = ?,
            result_path = ?, cost_usd = ?, finished_at = ?, updated_at = ?
        WHERE id = ? AND state NOT IN ('done', 'failed', 'cancelled')
        """,
        (state, progress, error, result_path, billed, now, now, job_id),
    )
    conn.commit()
    return get_job(conn, job_id)


def _row_to_job(row: sqlite3.Row) -> DurableJob:
    return DurableJob(
        row["id"], row["kind"], row["project_id"], row["deliverable_id"],
        row["content_id"], row["engine"], row["state"], row["progress"],
        row["attempt"], row["idempotency_key"], row["request_json"],
        row["result_path"], row["error"], row["cost_usd"],
        row["created_at"], row["updated_at"], row["finished_at"],
    )


__all__ = [
    "JOB_STATES",
    "TERMINAL_STATES",
    "DurableJob",
    "get_job",
    "get_job_by_key",
    "insert_job",
    "try_finish_job",
    "update_job_progress",
    "_MUTABLE",
]
