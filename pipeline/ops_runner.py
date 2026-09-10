"""Execute scheduled account operations jobs. Never auto-enables accounts."""
from __future__ import annotations

import os
import sqlite3
import threading
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
from pipeline.interviews import InterviewError, confirm_interview, load_interview, save_interview
from pipeline.jobs import store as jobs_store
from pipeline.utils.flock import LockHeld, acquire, release
from pipeline.projects import (
    DEFAULT_PROJECTS_ROOT, ProjectManifestError, create_project, list_projects, load_project,
)


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
        "SELECT id FROM durable_jobs WHERE engine = 'ops' AND state IN ('queued', 'running') "
        "ORDER BY created_at"
    ).fetchall()
    for row in rows:
        try:
            run_ops_job(
                conn, row["id"], now=now, accounts_root=accounts_root,
                projects_root=projects_root, draft_fn=draft_fn, visual_fn=visual_fn,
                score_fn=score_fn,
            )
            ran += 1
        except (OpsNotDue, OpsAccountDisabled, OpsJobUnavailable):
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


class OpsJobUnavailable(Exception):
    """A live worker owns this account, or the job is already terminal."""


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
    profile = load_profile(str(job.request().get("account_id") or ""), accounts_root=accounts_root)
    # flock is released by the OS on process death, so only abandoned jobs resume.
    # Serialize the account too: different slots share sidecars and a budget.
    lock_path = Path(accounts_root) / profile.id / "locks" / "operations.lock"
    try:
        acquire(lock_path)
    except LockHeld as error:
        raise OpsJobUnavailable("account operations already running") from error
    try:
        return _run_ops_job_locked(
            conn, job_id, now=now, accounts_root=accounts_root, projects_root=projects_root,
            draft_fn=draft_fn, visual_fn=visual_fn, score_fn=score_fn,
        )
    finally:
        release(lock_path)


def _run_ops_job_locked(
    conn: sqlite3.Connection, job_id: str, *, now: str,
    accounts_root: str | Path, projects_root: str | Path,
    draft_fn: Callable | None, visual_fn: Callable | None, score_fn: Callable | None,
) -> str:
    job = jobs_store.get_job(conn, job_id)
    if job is None:
        raise ValueError(f"ops job not found: {job_id}")
    if job.state in jobs_store.TERMINAL_STATES:
        raise OpsJobUnavailable(f"ops job {job.id} is terminal: {job.state}")
    request = job.request()
    account_id = str(request.get("account_id") or "")
    profile = load_profile(account_id, accounts_root=accounts_root)
    if not profile.operations_enabled:
        raise OpsAccountDisabled(f"operations disabled for {account_id}")
    due = _job_due_at(request, accounts_root=accounts_root)
    if due is None or _parse_iso(due) > _parse_iso(now):
        raise OpsNotDue(f"job {job_id} is not due until {due}")
    if job.state != "running":
        jobs_store.update_job_progress(conn, job.id, state="running", now=now, progress=0.1)
    try:
        slot = str(request.get("slot") or "slot")
        local_date = str(request.get("local_date") or now[:10])
        project = _existing_ops_project(job, projects_root=projects_root)
        if project is None:
            theme = select_ops_theme(
                profile, local_date=local_date, slot=slot, projects_root=projects_root,
            )
            project = create_project(
                title=f"{profile.display_name} {local_date} {slot}",
                idea=theme,
                audience=profile.audience or "读者",
                goal="按账号定位自动完成一篇可审阅图文",
                voice=profile.style or "清晰",
                autonomy="draft",
                now=now,
                projects_root=projects_root,
            )
        jobs_store.bind_job_project(conn, job.id, project_id=project.id, now=now)
        bind_project_account(
            project.id, platform=profile.platform, account_id=profile.id, now=now,
            projects_root=projects_root, accounts_root=accounts_root,
        )
        try:
            load_interview(project.id, projects_root=projects_root)
            has_interview = True
        except InterviewError:
            has_interview = False
        if not has_interview:
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
        board = research.load_research(project.id, projects_root=projects_root)
        if not board.sources:
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
        current = jobs_store.get_job(conn, job.id)
        if current is None or current.state in jobs_store.TERMINAL_STATES:
            raise OpsJobUnavailable("ops job became terminal before delivery")
        _maybe_deliver(
            conn, project_id=project.id, profile=profile, now=now,
            projects_root=projects_root, accounts_root=accounts_root,
        )
        jobs_store.try_finish_job(
            conn, job.id, state="done", now=now, result_path=f"projects/{project.id}",
            cost_usd=None,
        )
        return project.id
    except (OpsNotDue, OpsAccountDisabled, OpsJobUnavailable):
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


def _existing_ops_project(job, *, projects_root: str | Path):
    project_id = job.project_id
    if not project_id and isinstance(job.result_path, str) and job.result_path.startswith("projects/"):
        project_id = job.result_path.split("/", 1)[1]
    if not project_id:
        return None
    try:
        return load_project(project_id, projects_root=projects_root)
    except ProjectManifestError:
        return None


def select_ops_theme(profile, *, local_date: str, slot: str, projects_root: str | Path) -> str:
    from pipeline.master_documents import load_master
    used = set()
    for other in list_projects(projects_root=projects_root):
        if load_master(other.id, projects_root=projects_root) is None:
            continue
        idea = other.idea.strip()
        if idea:
            used.add(idea)
    positioning = (profile.positioning or profile.display_name).strip()
    candidates: list[str] = []
    for index, ref in enumerate(profile.references):
        label = str(ref).strip()[:40] or f"来源{index + 1}"
        candidates.append(f"{positioning} · {label}")
    for angle in ("边界怎么定", "先核对再加速", "读者要的证据", "失败里能署名的部分"):
        candidates.append(f"{positioning} · {angle}")
    candidates.append(f"{positioning} · {local_date} {slot}")
    for item in candidates:
        text = item.strip()
        if text and text not in used:
            return text
    raise OpsDeliverError("no unused theme for this account")


def ops_tick_interval_sec() -> int:
    raw = os.environ.get("MEDIAFORGE_OPS_TICK_SEC")
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    if os.environ.get("PYTEST_VERSION"):
        return 0
    return 60


def start_ops_ticker(*, interval_sec: int | None = None) -> Callable[[], None]:
    """Run tick_operations in a daemon thread. Never auto-enables accounts."""
    seconds = ops_tick_interval_sec() if interval_sec is None else max(0, int(interval_sec))
    if seconds <= 0:
        return lambda: None
    stop = threading.Event()

    def loop() -> None:
        from pipeline import db
        from pipeline.webui import deps
        while not stop.wait(seconds):
            conn = db.connect(deps._DB_PATH)
            try:
                db.init_db(conn)
                tick_operations(conn, now=db.now_utc())
            except Exception:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=loop, name="ops-ticker", daemon=True)
    thread.start()
    return stop.set


__all__ = [
    "OpsAccountDisabled",
    "OpsDeliverError",
    "OpsNotDue",
    "OpsTickResult",
    "ops_tick_interval_sec",
    "run_ops_job",
    "select_ops_theme",
    "start_ops_ticker",
    "tick_operations",
]
