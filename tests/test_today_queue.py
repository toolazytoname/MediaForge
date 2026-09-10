from fastapi.testclient import TestClient

from pipeline import db
from pipeline.delivery.store import insert_attempt
from pipeline.projects import create_project
from pipeline.today_queue import load_today
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


NOW = "2026-09-10T07:00:00+00:00"


def test_empty_queue_has_no_static_advice(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    snap = load_today(conn, projects_root=tmp_path / "projects")
    assert snap.todos == ()
    assert snap.exceptions == ()
    assert snap.next_plans == ()


def test_failed_delivery_becomes_exception(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    project = create_project(
        title="验证顺序", idea="想法", audience="读者", goal="写出能署名的稿",
        voice="清晰", autonomy="collaborate", now=NOW,
        project_id="prj_today001", projects_root=tmp_path / "projects",
    )
    insert_attempt(
        conn, project_id=project.id, deliverable_id="dlv_article_wechat_mp",
        deliverable_version=1, approval_fingerprint="fp", platform="wechat_mp",
        account_id="main", mode="draft", outcome="failure",
        idempotency_key="k1", request_hash_value="h1", actor="lazy",
        error="login expired",
    )
    snap = load_today(conn, projects_root=tmp_path / "projects")
    assert any(item.kind == "continue_project" for item in snap.todos)
    assert any(item.kind == "login_expired" and "login expired" in item.detail for item in snap.exceptions)


def test_today_api_returns_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    create_project(
        title="验证顺序", idea="想法", audience="读者", goal="写出能署名的稿",
        voice="清晰", autonomy="collaborate", now=NOW,
        project_id="prj_todayapi1", projects_root=tmp_path / "projects",
    )
    client = TestClient(create_app())
    response = client.get("/api/v1/today")
    assert response.status_code == 200
    body = response.json()
    assert body["next_plans"] == []
    assert any(item["kind"] == "continue_project" for item in body["todos"])
