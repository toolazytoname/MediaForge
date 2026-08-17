"""LAZY-88 web API: review-loop metrics and insight cards."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline import db
from pipeline.delivery.store import insert_attempt
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app
from pipeline import projects as project_store, visuals


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_review_loop_reads_fixture_metrics_and_pending_suggestions(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    project_store.create_project(
        title="复盘页", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="pack", now="2026-08-17T00:00:00+00:00",
        project_id="prj_review", projects_root=root,
    )
    visuals.save_plan(
        "prj_review", bible={"style": "plain"},
        slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "克制", "aspect_ratio": "16:9"}],
        projects_root=root,
    )
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    attempt = insert_attempt(
        conn,
        project_id="prj_review",
        deliverable_id="dlv_article_toutiao",
        deliverable_version=1,
        approval_fingerprint="b" * 64,
        platform="toutiao",
        account_id="main",
        mode="export",
        outcome="success",
        idempotency_key="review-key",
        request_hash_value="c" * 64,
        actor="lazy",
        created_at="2026-08-17T08:00:00+00:00",
    )
    client = _client(tmp_path, monkeypatch)
    recorded = client.post(
        "/api/v1/analytics/delivery-metrics",
        json={
            "delivery_attempt_id": attempt.id,
            "source": "fixture",
            "collected_at": "2026-08-17T09:00:00+00:00",
            "views": None,
            "likes": 3,
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["source"] == "fixture"
    assert recorded.json()["views"] is None

    generated = client.post("/api/v1/projects/prj_review/insights/generate")
    assert generated.status_code == 201, generated.text
    card = next(item for item in generated.json()["suggestions"] if item["kind"] == "brand_rule")
    assert card["status"] == "pending"

    loop = client.get("/api/v1/analytics/review-loop")
    assert loop.status_code == 200
    assert any(item["id"] == recorded.json()["id"] for item in loop.json()["metrics"])
    assert any(item["status"] == "pending" for item in loop.json()["suggestions"])

    decided = client.post(
        f"/api/v1/projects/prj_review/insights/{card['id']}/decide",
        json={"accepted": True, "actor": "lazy"},
    )
    assert decided.status_code == 200
    accepted = next(item for item in decided.json()["suggestions"] if item["id"] == card["id"])
    assert accepted["status"] == "accepted"
    project = project_store.load_project("prj_review", projects_root=root)
    assert project.voice == "清晰"
    assert visuals.load_visuals("prj_review", projects_root=root).bible == {"style": "plain"}
