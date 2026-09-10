"""Today page queue: real todos, exceptions, and upcoming plans.

Plans stay empty until AUTO-02 writes schedule sidecars. This module never
invents a static 'next step' card as the only content.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pipeline.delivery.store import latest_attempts
from pipeline.jobs import store as jobs_store
from pipeline.projects import DEFAULT_PROJECTS_ROOT, list_projects


@dataclass(frozen=True)
class TodayItem:
    kind: str
    title: str
    detail: str
    href: str
    project_id: str | None = None


@dataclass(frozen=True)
class TodaySnapshot:
    todos: tuple[TodayItem, ...]
    exceptions: tuple[TodayItem, ...]
    next_plans: tuple[TodayItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "todos": [asdict(item) for item in self.todos],
            "exceptions": [asdict(item) for item in self.exceptions],
            "next_plans": [asdict(item) for item in self.next_plans],
        }


def load_today(
    conn: sqlite3.Connection,
    *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> TodaySnapshot:
    projects = list_projects(projects_root=projects_root)
    todos: list[TodayItem] = []
    exceptions: list[TodayItem] = []
    for project in projects:
        todos.append(TodayItem(
            kind="continue_project",
            title=f"继续《{project.title}》",
            detail=project.goal,
            href=f"/projects/{project.id}",
            project_id=project.id,
        ))
        for attempt in latest_attempts(conn, project.id):
            if attempt.outcome == "failure":
                exceptions.append(TodayItem(
                    kind="delivery_failure",
                    title=f"{project.title} 交付失败",
                    detail=attempt.error or attempt.mode,
                    href=f"/projects/{project.id}",
                    project_id=project.id,
                ))
    try:
        rows = conn.execute(
            "SELECT * FROM durable_jobs WHERE state = 'failed' ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        job = jobs_store.get_job(conn, row["id"])
        if job is None:
            continue
        exceptions.append(TodayItem(
            kind="job_failure",
            title="后台任务失败",
            detail=job.error or job.kind,
            href=f"/projects/{job.project_id}" if job.project_id else "/runs",
            project_id=job.project_id,
        ))
    return TodaySnapshot(tuple(todos), tuple(exceptions), ())


__all__ = ["TodayItem", "TodaySnapshot", "load_today"]
