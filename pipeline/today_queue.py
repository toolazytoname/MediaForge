"""Today page queue: real todos, exceptions, and upcoming plans.

This module never invents a static 'next step' card as the only content.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pipeline.account_plans import list_upcoming_plans
from pipeline.account_profiles import DEFAULT_ACCOUNTS_ROOT, list_profiles
from pipeline.delivery.store import get_attempt, latest_attempts
from pipeline.interviews import InterviewError, load_interview
from pipeline.jobs import store as jobs_store
from pipeline.projects import DEFAULT_PROJECTS_ROOT, list_projects
from pipeline.research import load_research

DEFAULT_TODAY_ROOT = Path("output/today")
_RESOLVE_ACTIONS = frozenset({"skip", "verify", "retry"})


@dataclass(frozen=True)
class TodayItem:
    kind: str
    title: str
    detail: str
    href: str
    project_id: str | None = None
    actions: tuple[str, ...] = ()
    ref_id: str | None = None


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


@dataclass(frozen=True)
class TodayResolution:
    ref_id: str
    action: str
    at: str


def load_today(
    conn: sqlite3.Connection,
    *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    today_root: str | Path = DEFAULT_TODAY_ROOT,
) -> TodaySnapshot:
    resolved = _load_resolutions(today_root)
    hidden = {
        key for key, item in resolved.items()
        if item.get("action") == "skip" or item.get("hidden") is True
    }
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
            actions=("edit",),
        ))
        if _missing_materials(project.id, projects_root):
            ref = f"missing:{project.id}"
            if ref not in hidden:
                exceptions.append(TodayItem(
                    kind="missing_materials",
                    title=f"{project.title} 缺资料",
                    detail="补访谈或来源后再写稿",
                    href=f"/projects/{project.id}",
                    project_id=project.id,
                    actions=("supplement", "edit", "skip"),
                    ref_id=ref,
                ))
        for attempt in latest_attempts(conn, project.id):
            if attempt.outcome not in {"failure", "unknown"}:
                continue
            if attempt.id in hidden:
                continue
            login = _is_login_error(attempt.error)
            kind = "login_expired" if login else (
                "unknown_receipt" if attempt.outcome == "unknown" else "delivery_failure"
            )
            title = f"{project.title} 登录过期" if login else (
                f"{project.title} 结果未知" if attempt.outcome == "unknown"
                else f"{project.title} 交付失败"
            )
            exceptions.append(TodayItem(
                kind=kind,
                title=title,
                detail=attempt.error or attempt.mode,
                href=f"/projects/{project.id}",
                project_id=project.id,
                actions=("edit", "retry", "skip", "verify"),
                ref_id=attempt.id,
            ))
    for profile in list_profiles(accounts_root=accounts_root):
        for result in _quality_failures(profile.id, accounts_root):
            ref = f"quality:{profile.id}:{result['project_id']}"
            if ref in hidden:
                continue
            exceptions.append(TodayItem(
                kind="quality_failed",
                title="质量不合格，已暂停",
                detail=f"{result['project_id']} score={result['score']}",
                href=f"/projects/{result['project_id']}",
                project_id=result["project_id"],
                actions=("edit", "skip", "verify"),
                ref_id=ref,
            ))
    try:
        rows = conn.execute(
            "SELECT * FROM durable_jobs WHERE state = 'failed' ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        job = jobs_store.get_job(conn, row["id"])
        if job is None or job.id in hidden:
            continue
        exceptions.append(TodayItem(
            kind="job_failure",
            title="后台任务失败",
            detail=job.error or job.kind,
            href=f"/projects/{job.project_id}" if job.project_id else "/runs",
            project_id=job.project_id,
            actions=("retry", "skip", "verify"),
            ref_id=job.id,
        ))
    next_plans: list[TodayItem] = []
    for plan in list_upcoming_plans(accounts_root=accounts_root):
        for slot in plan.slots:
            next_plans.append(TodayItem(
                kind="next_plan",
                title=f"{plan.account_id} 下次 {slot.slot}",
                detail=f"{slot.local_date} {slot.scheduled_at}",
                href="/accounts",
                project_id=None,
            ))
    return TodaySnapshot(tuple(todos), tuple(exceptions), tuple(next_plans))


def resolve_today_item(
    conn: sqlite3.Connection,
    *,
    ref_id: str,
    action: Literal["skip", "verify", "retry"],
    now: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    today_root: str | Path = DEFAULT_TODAY_ROOT,
    adapter: Any = None,
    actor: str = "local",
) -> TodayResolution:
    if action not in _RESOLVE_ACTIONS:
        raise ValueError(f"invalid today action: {action}")
    hide = action in {"skip"}
    if action == "verify" and not ref_id.startswith(("missing:", "quality:")):
        prior = get_attempt(conn, ref_id)
        if prior is None:
            raise ValueError("verify target not found")
        if adapter is not None and prior.outcome == "unknown":
            from pipeline.delivery.service import verify_direct_receipt
            verified = verify_direct_receipt(
                conn, attempt_id=ref_id, adapter=adapter, actor=actor, now=now,
            )
            hide = verified.attempt.outcome == "success"
        else:
            hide = False
    payload = _load_resolutions(today_root)
    payload[ref_id] = {"action": action, "at": now, "hidden": hide}
    path = Path(today_root) / "resolutions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return TodayResolution(ref_id=ref_id, action=action, at=now)


def _missing_materials(project_id: str, projects_root: str | Path) -> bool:
    try:
        interview = load_interview(project_id, projects_root=projects_root)
        if not interview.confirmed:
            return True
    except InterviewError:
        return True
    board = load_research(project_id, projects_root=projects_root)
    return not board.sources


def _is_login_error(error: str | None) -> bool:
    text = (error or "").lower()
    return any(token in text for token in ("login", "expired", "cookie"))


def _quality_failures(account_id: str, accounts_root: str | Path) -> list[dict[str, Any]]:
    path = Path(accounts_root) / account_id / "quality_results.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if str(item.get("verdict", "")).lower() in {"fail", "failed"}]


def _load_resolutions(today_root: str | Path) -> dict[str, Any]:
    path = Path(today_root) / "resolutions.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = [
    "TodayItem",
    "TodayResolution",
    "TodaySnapshot",
    "load_today",
    "resolve_today_item",
]

