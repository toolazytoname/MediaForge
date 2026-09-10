"""Execute scheduled account operations jobs. Never auto-enables accounts."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pipeline import research
from pipeline.account_bindings import bind_project_account
from pipeline.account_plans import load_plan, schedule_account
from pipeline.account_profiles import DEFAULT_ACCOUNTS_ROOT, load_profile, list_profiles
from pipeline.auto_create import run_auto_create
from pipeline.creators.source_fetcher import fetch_text
from pipeline.interviews import confirm_interview, save_interview
from pipeline.jobs import store as jobs_store
from pipeline.projects import DEFAULT_PROJECTS_ROOT, create_project


@dataclass(frozen=True)
class OpsTickResult:
    scheduled: int
    ran: int
    failed: int


def tick_operations(
    conn: sqlite3.Connection,
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    draft_fn: Callable[..., tuple[str, str]] | None = None,
    visual_fn: Callable[[str], None] | None = None,
    score_fn: Callable[[str, str], tuple[str, float]] | None = None,
) -> OpsTickResult:
    scheduled = 0
    for profile in list_profiles(accounts_root=accounts_root):
        if not profile.operations_enabled:
            continue
        before = conn.execute("SELECT count(*) AS n FROM durable_jobs").fetchone()["n"]
        schedule_account(conn, profile.id, now=now, accounts_root=accounts_root)
        after = conn.execute("SELECT count(*) AS n FROM durable_jobs").fetchone()["n"]
        scheduled += max(0, after - before)
    ran = failed = 0
    rows = conn.execute(
        "SELECT id FROM durable_jobs WHERE engine = 'ops' AND state = 'queued' ORDER BY created_at"
    ).fetchall()
    for row in rows:
        try:
            run_ops_job(
                conn, row["id"], now=now, accounts_root=accounts_root,
                projects_root=projects_root, draft_fn=draft_fn, visual_fn=visual_fn,
                score_fn=score_fn,
            )
            ran += 1
        except (OpsNotDue, OpsAccountDisabled):
            continue
        except Exception as error:
            jobs_store.try_finish_job(
                conn, row["id"], state="failed", now=now, error=str(error),
            )
            failed += 1
    return OpsTickResult(scheduled=scheduled, ran=ran, failed=failed)


class OpsNotDue(Exception):
    """Job exists but scheduled_at is still in the future."""


class OpsAccountDisabled(Exception):
    """Leave the job queued; operations were switched off after scheduling."""


class OpsDeliverError(Exception):
    """Automatic delivery failed or returned a non-success outcome."""


def run_ops_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    draft_fn: Callable[..., tuple[str, str]] | None = None,
    visual_fn: Callable[[str], None] | None = None,
    score_fn: Callable[[str, str], tuple[str, float]] | None = None,
) -> str:
    job = jobs_store.get_job(conn, job_id)
    if job is None:
        raise ValueError(f"ops job not found: {job_id}")
    request = job.request()
    account_id = str(request.get("account_id") or "")
    profile = load_profile(account_id, accounts_root=accounts_root)
    if not profile.operations_enabled:
        raise OpsAccountDisabled(f"operations disabled for {account_id}")
    due = _job_due_at(request, accounts_root=accounts_root)
    if due is None or _parse_iso(due) > _parse_iso(now):
        raise OpsNotDue(f"job {job_id} is not due until {due}")
    jobs_store.update_job_progress(conn, job.id, state="running", now=now, progress=0.1)
    try:
        slot = str(request.get("slot") or "slot")
        local_date = str(request.get("local_date") or now[:10])
        project = create_project(
            title=f"{profile.display_name} {local_date} {slot}",
            idea=profile.positioning or profile.display_name,
            audience=profile.audience or "读者",
            goal="按账号定位自动完成一篇可审阅图文",
            voice=profile.style or "清晰",
            autonomy="draft",
            now=now,
            projects_root=projects_root,
        )
        bind_project_account(
            project.id, platform=profile.platform, account_id=profile.id, now=now,
            projects_root=projects_root, accounts_root=accounts_root,
        )
        save_interview(
            project.id,
            viewpoint=profile.positioning or profile.display_name,
            motive=profile.audience or "读者",
            experience=profile.style or "克制",
            sources=tuple(
                {"kind": "url", "title": item, "reference": item, "excerpt": ""}
                for item in profile.references
            ),
            now=now, projects_root=projects_root,
        )
        confirm_interview(project.id, now=now, projects_root=projects_root)
        if profile.references:
            for item in profile.references:
                excerpt = None
                if item.startswith("http"):
                    try:
                        excerpt = fetch_text(item)
                    except Exception:
                        excerpt = None
                research.add_source(
                    project.id, title=item[:40], reference=item,
                    summary=(excerpt or "未读来源，不得假装读过")[:800],
                    now=now, projects_root=projects_root,
                )
        else:
            research.add_source(
                project.id, title=profile.display_name, reference="local:account",
                summary=profile.positioning or profile.style, now=now,
                projects_root=projects_root,
            )
        run_auto_create(
            project.id, now=now, projects_root=projects_root, draft_fn=draft_fn,
            visual_fn=visual_fn, score_fn=score_fn, account_id=profile.id,
            accounts_root=accounts_root,
        )
        _maybe_deliver(
            conn, project_id=project.id, profile=profile, now=now,
            projects_root=projects_root, accounts_root=accounts_root,
        )
        jobs_store.try_finish_job(
            conn, job.id, state="done", now=now, result_path=f"projects/{project.id}",
            cost_usd=None,
        )
        return project.id
    except (OpsNotDue, OpsAccountDisabled):
        raise
    except Exception as error:
        jobs_store.try_finish_job(
            conn, job.id, state="failed", now=now, error=str(error),
        )
        raise


def _maybe_deliver(
    conn: sqlite3.Connection, *, project_id: str, profile, now: str,
    projects_root: str | Path, accounts_root: str | Path,
) -> None:
    if profile.delivery_target not in {"draft", "direct"}:
        return
    if not profile.credentials_ref:
        raise OpsDeliverError("account is missing credentials_ref")
    from pipeline.webui import deps
    from pipeline.webui.api.delivery import _adapter_for
    from pipeline.deliverables import load_deliverables
    from pipeline.delivery.service import DeliveryError, create_draft, create_direct
    cfg, err = deps.get_config()
    if cfg is None:
        raise OpsDeliverError(err or "config missing")
    items = load_deliverables(project_id, projects_root=projects_root).items
    target = next((item for item in items if profile.platform in item.targets), None)
    if target is None:
        raise OpsDeliverError("deliverable missing for account platform")
    try:
        adapter, account = _adapter_for(cfg, profile.platform, account_id=profile.id)
        if profile.delivery_target == "draft":
            result = create_draft(
                conn, project_id=project_id, deliverable_id=target.id, actor="ops",
                adapter=adapter, account=account, publish_config=cfg.publish, cfg=cfg,
                projects_root=projects_root, path="auto", accounts_root=accounts_root,
            )
        else:
            result = create_direct(
                conn, project_id=project_id, deliverable_id=target.id, actor="ops",
                adapter=adapter, account=account, publish_config=cfg.publish, cfg=cfg,
                confirm_token="ops-auto", projects_root=projects_root,
                accounts_root=accounts_root, path="auto",
            )
    except DeliveryError as error:
        raise OpsDeliverError(str(error)) from error
    if result.attempt.outcome != "success":
        raise OpsDeliverError(result.attempt.error or result.attempt.outcome)


def _job_due_at(request: dict[str, Any], *, accounts_root: str | Path) -> str | None:
    due = request.get("scheduled_at")
    if isinstance(due, str) and due.strip():
        return due
    account_id = str(request.get("account_id") or "")
    slot = str(request.get("slot") or "")
    local_date = str(request.get("local_date") or "")
    if not account_id:
        return None
    try:
        plan = load_plan(account_id, accounts_root=accounts_root)
    except Exception:
        return None
    for item in plan.slots:
        if item.slot == slot and item.local_date == local_date:
            return item.scheduled_at
    return None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "OpsAccountDisabled",
    "OpsDeliverError",
    "OpsNotDue",
    "OpsTickResult",
    "run_ops_job",
    "tick_operations",
]
