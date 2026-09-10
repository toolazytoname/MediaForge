"""AUTO-02 execution: scheduled ops jobs actually create work."""
from __future__ import annotations

from pipeline import db
from pipeline.account_plans import schedule_account
from pipeline.account_profiles import create_profile
from pipeline.jobs import store as jobs_store
from pipeline.ops_runner import run_ops_job, tick_operations
from pipeline.projects import list_projects
from tests.test_auto_create import NOW, _draft_fn, _visual_fn


def _profile(root, *, enabled=True):
    return create_profile(
        platform="wechat_mp", display_name="运营号", positioning="边界比速度重要",
        audience="同行编辑", style="克制写实", references=("https://example.com/a",),
        creation_mode="auto", frequency="2/week", timezone="Asia/Shanghai",
        budget_usd=10, delivery_target="draft",
        credentials_ref="secrets/wechat_mp_main.json", config_account_id="main",
        now=NOW, accounts_root=root, profile_id="acc_ops00001",
        operations_enabled=enabled,
    )


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


def test_run_ops_job_creates_project_from_account(tmp_path):
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    profile = _profile(accounts, enabled=True)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    scheduled = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert scheduled.jobs
    job = scheduled.jobs[0]
    project_id = run_ops_job(
        conn, job.id, now=NOW, accounts_root=accounts, projects_root=projects,
        draft_fn=_draft_fn, visual_fn=lambda pid: _visual_fn(pid, projects),
        score_fn=lambda title, body: ("pass", 8.0),
    )
    items = list_projects(projects_root=projects)
    assert any(item.id == project_id for item in items)
    finished = jobs_store.get_job(conn, job.id)
    assert finished is not None
    assert finished.state == "done"
