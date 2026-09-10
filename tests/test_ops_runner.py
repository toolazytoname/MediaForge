"""AUTO-02 execution: scheduled ops jobs actually create work."""
from __future__ import annotations

import pytest

from pipeline import db, research
from pipeline.account_plans import schedule_account
from pipeline.account_profiles import create_profile, update_profile
from pipeline.jobs import store as jobs_store
from pipeline.ops_runner import OpsDeliverError, OpsNotDue, run_ops_job, tick_operations
from pipeline.projects import list_projects
from tests.test_auto_create import NOW, _draft_fn, _visual_fn


def _profile(root, *, enabled=True, target="export", creds="secrets/wechat_mp_main.json"):
    return create_profile(
        platform="wechat_mp", display_name="运营号", positioning="边界比速度重要",
        audience="同行编辑", style="克制写实", references=("https://example.com/a",),
        creation_mode="auto", frequency="2/week", timezone="Asia/Shanghai",
        budget_usd=10, delivery_target=target,
        credentials_ref=creds, config_account_id="main",
        now=NOW, accounts_root=root, profile_id="acc_ops00001",
        operations_enabled=enabled,
    )


def _due(job) -> str:
    return str(job.request()["scheduled_at"])


def test_tick_does_not_run_disabled_accounts(tmp_path):
    accounts = tmp_path / "accounts"
    _profile(accounts, enabled=False)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = tick_operations(
        conn, now=NOW, accounts_root=accounts, projects_root=tmp_path / "projects",
        draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, tmp_path / "projects"),
    )
    assert result.scheduled == 0
    assert result.ran == 0
    assert list_projects(projects_root=tmp_path / "projects") == ()


def test_run_ops_job_creates_project_from_account(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pipeline.ops_runner.fetch_text",
        lambda url: "公开页面正文：边界比速度更值得署名。",
    )
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    profile = _profile(accounts, enabled=True, target="export")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    scheduled = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert scheduled.jobs
    job = scheduled.jobs[0]
    assert job.request()["scheduled_at"]
    project_id = run_ops_job(
        conn, job.id, now=_due(job), accounts_root=accounts, projects_root=projects,
        draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, projects),
        score_fn=lambda title, body: ("pass", 8.0),
    )
    items = list_projects(projects_root=projects)
    assert any(item.id == project_id for item in items)
    board = research.load_research(project_id, projects_root=projects)
    assert any("公开页面正文" in (item.summary or "") for item in board.sources)
    finished = jobs_store.get_job(conn, job.id)
    assert finished is not None
    assert finished.state == "done"


def test_tick_does_not_run_jobs_before_scheduled_at(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.ops_runner.fetch_text", lambda url: "摘录")
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    _profile(accounts, enabled=True, target="export")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = tick_operations(
        conn, now=NOW, accounts_root=accounts, projects_root=projects,
        draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, projects),
        score_fn=lambda title, body: ("pass", 8.0),
    )
    assert result.scheduled >= 1
    assert result.ran == 0
    assert list_projects(projects_root=projects) == ()
    queued = conn.execute(
        "SELECT count(*) AS n FROM durable_jobs WHERE state = 'queued'",
    ).fetchone()["n"]
    assert queued >= 1


def test_disabled_account_does_not_run_already_queued_job(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.ops_runner.fetch_text", lambda url: "摘录")
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    profile = _profile(accounts, enabled=True, target="export")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    scheduled = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    job = scheduled.jobs[0]
    update_profile(
        profile, now=NOW, accounts_root=accounts, operations_enabled=False,
    )
    result = tick_operations(
        conn, now=_due(job), accounts_root=accounts, projects_root=projects,
        draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, projects),
        score_fn=lambda title, body: ("pass", 8.0),
    )
    assert result.ran == 0
    assert result.failed == 0
    stored = jobs_store.get_job(conn, job.id)
    assert stored is not None
    assert stored.state == "queued"
    assert list_projects(projects_root=projects) == ()


def test_delivery_failure_does_not_mark_ops_job_done(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.ops_runner.fetch_text", lambda url: "摘录")
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    profile = _profile(accounts, enabled=True, target="draft", creds=None)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    scheduled = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    job = scheduled.jobs[0]
    with pytest.raises(OpsDeliverError, match="credentials"):
        run_ops_job(
            conn, job.id, now=_due(job), accounts_root=accounts, projects_root=projects,
            draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, projects),
            score_fn=lambda title, body: ("pass", 8.0),
        )
    stored = jobs_store.get_job(conn, job.id)
    assert stored is not None
    assert stored.state == "failed"


def test_run_ops_job_before_due_leaves_queued(tmp_path):
    accounts = tmp_path / "accounts"
    profile = _profile(accounts, enabled=True, target="export")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    scheduled = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    job = scheduled.jobs[0]
    with pytest.raises(OpsNotDue):
        run_ops_job(
            conn, job.id, now=NOW, accounts_root=accounts,
            projects_root=tmp_path / "projects",
            draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, tmp_path / "projects"),
        )
    stored = jobs_store.get_job(conn, job.id)
    assert stored is not None
    assert stored.state == "queued"
