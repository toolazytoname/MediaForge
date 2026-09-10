"""UX-07: exceptions on Today with actions; verify does not resend."""
from __future__ import annotations

from unittest.mock import MagicMock

from pipeline import db
from pipeline.account_authorization import record_quality_result
from pipeline.account_profiles import create_profile
from pipeline.delivery.store import insert_attempt
from pipeline.projects import create_project
from pipeline.today_queue import load_today, resolve_today_item
from pipeline.delivery import service as delivery_service

NOW = "2026-09-10T09:00:00+00:00"


def _project(root):
    return create_project(
        title="待办稿", idea="想法", audience="读者", goal="写出能署名的稿",
        voice="清晰", autonomy="collaborate", now=NOW,
        project_id="prj_exc00001", projects_root=root,
    )


def test_missing_materials_and_login_expired_and_quality(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    projects = tmp_path / "projects"
    accounts = tmp_path / "accounts"
    project = _project(projects)
    insert_attempt(
        conn, project_id=project.id, deliverable_id="dlv_article_wechat_mp",
        deliverable_version=1, approval_fingerprint="fp", platform="wechat_mp",
        account_id="main", mode="draft", outcome="failure",
        idempotency_key="k-login", request_hash_value="h1", actor="lazy",
        error="LoginExpired: cookie expired",
    )
    profile = create_profile(
        platform="wechat_mp", display_name="号", positioning="定位",
        audience="读者", style="克制", references=(), creation_mode="auto",
        frequency="off", timezone="Asia/Shanghai", budget_usd=1,
        delivery_target="draft", credentials_ref="secrets/wechat_mp_main.json",
        config_account_id="main", now=NOW, accounts_root=accounts,
        profile_id="acc_exc00001",
    )
    record_quality_result(
        profile.id, project_id=project.id, score=3.0, verdict="fail",
        now=NOW, accounts_root=accounts,
    )
    snap = load_today(
        conn, projects_root=projects, accounts_root=accounts,
        today_root=tmp_path / "today",
    )
    kinds = {item.kind for item in snap.exceptions}
    assert "login_expired" in kinds
    assert "missing_materials" in kinds
    assert "quality_failed" in kinds
    login = next(item for item in snap.exceptions if item.kind == "login_expired")
    assert "verify" in login.actions
    assert "retry" in login.actions


def test_verify_does_not_call_publish(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    projects = tmp_path / "projects"
    project = _project(projects)
    attempt = insert_attempt(
        conn, project_id=project.id, deliverable_id="dlv_article_wechat_mp",
        deliverable_version=1, approval_fingerprint="fp", platform="wechat_mp",
        account_id="main", mode="direct", outcome="unknown",
        idempotency_key="k-unknown", request_hash_value="h2", actor="lazy",
        error="unknown receipt",
    )
    spy = MagicMock(wraps=delivery_service.safe_publish)
    monkeypatch.setattr(delivery_service, "safe_publish", spy)
    resolved = resolve_today_item(
        conn, ref_id=attempt.id, action="verify", now=NOW,
        projects_root=projects, today_root=tmp_path / "today",
    )
    assert resolved.action == "verify"
    spy.assert_not_called()
    snap = load_today(conn, projects_root=projects, today_root=tmp_path / "today")
    assert any(item.ref_id == attempt.id for item in snap.exceptions)
