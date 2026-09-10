"""AUTO-02: account cadence, durable jobs, restart dedupe, budget pause."""
from __future__ import annotations

from pipeline import db
from pipeline.account_plans import (
    load_plan,
    record_spend,
    schedule_account,
)
from pipeline.account_profiles import create_profile
from pipeline.jobs import store as jobs_store
from pipeline.today_queue import load_today

NOW = "2026-09-10T08:00:00+00:00"  # Thursday 16:00 Shanghai if +08, actually 08:00Z = 16:00 CST


def _profile(root, *, enabled=True, frequency="2/week", budget=10.0, profile_id="acc_plan0001"):
    return create_profile(
        platform="wechat_mp", display_name="运营号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="auto",
        frequency=frequency, timezone="Asia/Shanghai", budget_usd=budget,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main", now=NOW, accounts_root=root,
        profile_id=profile_id, operations_enabled=enabled,
    )


def test_operations_disabled_does_not_schedule(tmp_path):
    accounts = tmp_path / "accounts"
    profile = _profile(accounts, enabled=False)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert result.jobs == ()
    plan = load_plan(profile.id, accounts_root=accounts)
    assert plan.slots == ()
    assert conn.execute("SELECT count(*) AS n FROM durable_jobs").fetchone()["n"] == 0


def test_restart_does_not_insert_second_job_for_same_slot(tmp_path):
    accounts = tmp_path / "accounts"
    profile = _profile(accounts, enabled=True, frequency="2/week")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    first = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert len(first.jobs) >= 1
    assert all(job.request().get("scheduled_at") for job in first.jobs)
    keys = {job.idempotency_key for job in first.jobs}
    second = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert {job.idempotency_key for job in second.jobs} == keys
    assert conn.execute("SELECT count(*) AS n FROM durable_jobs").fetchone()["n"] == len(keys)
    for job in second.jobs:
        stored = jobs_store.get_job_by_key(conn, job.idempotency_key)
        assert stored is not None
        assert stored.id == job.id


def test_budget_exceeded_pauses_and_skips_new_jobs(tmp_path):
    accounts = tmp_path / "accounts"
    profile = _profile(accounts, enabled=True, budget=1.0)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    record_spend(profile.id, amount=1.5, now=NOW, accounts_root=accounts)
    result = schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    assert result.jobs == ()
    plan = load_plan(profile.id, accounts_root=accounts)
    assert plan.paused is True
    assert plan.pause_reason and "budget" in plan.pause_reason
    assert conn.execute("SELECT count(*) AS n FROM durable_jobs").fetchone()["n"] == 0


def test_today_reads_next_plans(tmp_path):
    accounts = tmp_path / "accounts"
    projects = tmp_path / "projects"
    profile = _profile(accounts, enabled=True, frequency="daily")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    schedule_account(conn, profile.id, now=NOW, accounts_root=accounts)
    snap = load_today(conn, projects_root=projects, accounts_root=accounts)
    assert any(item.kind == "next_plan" for item in snap.next_plans)
