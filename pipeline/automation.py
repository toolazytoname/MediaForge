"""Scheduled project preparation that stops at awaiting_approval (LAZY-88).

Research / draft / visual slots / platform variants follow the current
autonomy policy. This path never calls draft / direct / export adapters
and never writes delivery_attempts.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import (
    deliverables,
    projects as project_store,
    research,
    visuals,
)
from pipeline.autonomy import AutonomyPolicy, load_policy
from pipeline.budget import BudgetPaused, enforce_autonomy_budget
from pipeline.delivery import store as delivery_store
from pipeline.pack import prepare_candidates
from pipeline.utils.errors import UnpricedModelError
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "automation.json"
AWAITING_APPROVAL = "awaiting_approval"
PAUSED_BUDGET = "paused_budget"
PAUSED_UNPRICED = "paused_unpriced"
SKIPPED = "skipped"
_AUTO_PREPARE = frozenset({"draft", "pack"})
_DEFAULT_SLOTS = (
    {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "克制", "aspect_ratio": "16:9"},
    {"id": "vsl_one", "purpose": "正文插图一", "paragraph_anchor": "正文", "direction": "克制", "aspect_ratio": "16:9"},
    {"id": "vsl_two", "purpose": "正文插图二", "paragraph_anchor": "正文", "direction": "克制", "aspect_ratio": "16:9"},
)


class AutomationError(ValueError):
    """Scheduled prepare refused to continue."""


@dataclass(frozen=True)
class ProjectPrepareResult:
    project_id: str
    autonomy: str
    status: str
    reason: str | None
    terminal_status: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrepareDueResult:
    prepared: int
    paused: int
    skipped: int
    items: tuple[ProjectPrepareResult, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


def can_auto_prepare(policy: AutonomyPolicy) -> bool:
    return policy.key in _AUTO_PREPARE


def load_automation(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> dict[str, Any] | None:
    path = _path(projects_root, project_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AutomationError("invalid automation JSON")
    return raw


def prepare_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    now: str,
    actor: str = "cron",
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    estimated_cost: float | None = 0.0,
) -> ProjectPrepareResult:
    """Advance one project at most to awaiting_approval."""
    _timestamp(now)
    if not valid_sidecar_id(project_id, "prj_"):
        raise AutomationError("invalid project id")
    project, policy = load_policy(project_id, projects_root=projects_root)
    if not can_auto_prepare(policy):
        result = ProjectPrepareResult(project.id, project.autonomy, SKIPPED, "autonomy forbids scheduled prepare", None)
        _persist(projects_root, result, now=now, actor=actor)
        return result
    try:
        enforce_autonomy_budget(
            conn, project_id=project.id, policy=policy,
            estimated_cost=estimated_cost, model="scheduled",
        )
    except BudgetPaused as error:
        result = ProjectPrepareResult(
            project.id, project.autonomy, PAUSED_BUDGET, str(error), None,
        )
        _persist(projects_root, result, now=now, actor=actor)
        delivery_store.insert_audit(
            conn, actor=actor, action="automation.paused_budget",
            payload={"project_id": project.id, "used_usd": error.used_usd, "limit_usd": error.limit_usd},
            project_id=project.id, at=now,
        )
        return result
    except UnpricedModelError as error:
        result = ProjectPrepareResult(
            project.id, project.autonomy, PAUSED_UNPRICED, str(error), None,
        )
        _persist(projects_root, result, now=now, actor=actor)
        delivery_store.insert_audit(
            conn, actor=actor, action="automation.paused_unpriced",
            payload={"project_id": project.id, "error": str(error)},
            project_id=project.id, at=now,
        )
        return result

    _seed_research(project.id, now=now, projects_root=projects_root)
    _seed_visual_slots(project.id, projects_root=projects_root)
    prepared = prepare_candidates(project.id, now=now, projects_root=projects_root)
    if prepared.terminal_status not in {"drafting", "ready_for_approval"}:
        raise AutomationError(f"scheduled prepare exceeded approval: {prepared.terminal_status}")
    items = deliverables.load_deliverables(project.id, projects_root=projects_root).items
    if any(item.locked for item in items):
        # Human may have locked already; cron itself never locks.
        pass
    result = ProjectPrepareResult(
        project.id, project.autonomy, AWAITING_APPROVAL, None, prepared.terminal_status,
    )
    _persist(projects_root, result, now=now, actor=actor)
    delivery_store.insert_audit(
        conn, actor=actor, action="automation.prepare",
        payload={
            "project_id": project.id,
            "status": AWAITING_APPROVAL,
            "terminal_status": prepared.terminal_status,
            "created_platforms": list(prepared.created_platforms),
        },
        project_id=project.id, at=now,
    )
    return result


def run_prepare_due(
    conn: sqlite3.Connection,
    *,
    now: str,
    actor: str = "cron",
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> PrepareDueResult:
    items: list[ProjectPrepareResult] = []
    prepared = paused = skipped = 0
    for project in project_store.list_projects(projects_root=projects_root):
        item = prepare_project(
            conn, project.id, now=now, actor=actor, projects_root=projects_root,
        )
        items.append(item)
        if item.status == AWAITING_APPROVAL:
            prepared += 1
        elif item.status in {PAUSED_BUDGET, PAUSED_UNPRICED}:
            paused += 1
        else:
            skipped += 1
    return PrepareDueResult(prepared, paused, skipped, tuple(items))


def _seed_research(project_id: str, *, now: str, projects_root: str | Path) -> None:
    board = research.load_research(project_id, projects_root=projects_root)
    if len(board.sources) < 3:
        for index in range(3 - len(board.sources)):
            research.add_source(
                project_id,
                title=f"待确认来源 {index + 1}",
                reference=f"fixture://research/{project_id}/{index}",
                summary="定时任务写入的待确认研究笔记，不是已核查事实。",
                now=now,
                projects_root=projects_root,
            )
    board = research.load_research(project_id, projects_root=projects_root)
    if not any(item.kind == "judgment" for item in board.claims):
        research.add_claim(
            project_id,
            text="待确认判断：作者仍需亲自核对事实边界。",
            kind="judgment",
            source_ids=[],
            status="unverified",
            now=now,
            projects_root=projects_root,
        )


def _seed_visual_slots(project_id: str, *, projects_root: str | Path) -> None:
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    if plan.slots:
        return
    bible = plan.bible or {"style": "plain"}
    visuals.save_plan(project_id, bible=bible, slots=list(_DEFAULT_SLOTS), projects_root=projects_root)


def _persist(
    root: str | Path,
    result: ProjectPrepareResult,
    *,
    now: str,
    actor: str,
) -> None:
    path = _path(root, result.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_id": result.project_id,
        "status": result.status,
        "reason": result.reason,
        "terminal_status": result.terminal_status,
        "actor": actor,
        "updated_at": now,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _path(root: str | Path, project_id: str) -> Path:
    return Path(root) / project_id / _NAME


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise AutomationError("timestamp must include timezone")
    return value


__all__ = [
    "AWAITING_APPROVAL",
    "PAUSED_BUDGET",
    "PAUSED_UNPRICED",
    "SKIPPED",
    "AutomationError",
    "PrepareDueResult",
    "ProjectPrepareResult",
    "can_auto_prepare",
    "load_automation",
    "prepare_project",
    "run_prepare_due",
]
