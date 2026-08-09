from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import master_documents as master_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    return project_store.create_project(title="API 项目", idea="观察", audience="创作者", goal="主稿", voice="克制",
                                        autonomy="collaborate", now="2026-08-09T10:00:00+00:00",
                                        project_id="prj_master", projects_root=root)


def test_master_api_manual_suggestion_reject_accept_and_restore(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root)
    first = client.put("/api/v1/projects/prj_master/master", json={"title": "标题", "body": "原文需要更清楚。"})
    assert first.status_code == 200 and first.json()["version"] == 1

    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    seen = {}
    def fake_complete(prompt, **kwargs):
        seen["prompt"] = prompt
        return "改得更清楚。"
    monkeypatch.setattr(master_api.llm, "complete", fake_complete)
    suggestion = client.post("/api/v1/projects/prj_master/master/suggestions", json={"action": "clarify", "selection": "原文需要更清楚。"})
    assert suggestion.status_code == 201
    assert "Audience: 创作者" in seen["prompt"]
    assert client.get("/api/v1/projects/prj_master/master").json()["master"]["body"] == "原文需要更清楚。"

    rejected = client.post(f"/api/v1/projects/prj_master/master/suggestions/{suggestion.json()['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    second_suggestion = client.post("/api/v1/projects/prj_master/master/suggestions", json={"action": "shorten"})
    accepted = client.post(f"/api/v1/projects/prj_master/master/suggestions/{second_suggestion.json()['id']}/accept")
    assert accepted.status_code == 200 and accepted.json()["version"] == 2
    restored = client.post("/api/v1/projects/prj_master/master/versions/1/restore")
    assert restored.status_code == 200 and restored.json()["version"] == 3


def test_suggestion_provider_failure_has_visible_error_and_never_writes(client, tmp_path):
    root = tmp_path / "projects"
    _project(root)
    client.put("/api/v1/projects/prj_master/master", json={"title": "标题", "body": "原文"})
    response = client.post("/api/v1/projects/prj_master/master/suggestions", json={"action": "clarify"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "llm_provider_unavailable"
    assert client.get("/api/v1/projects/prj_master/master/suggestions").json() == {"items": []}
    assert client.get("/api/v1/projects/prj_master/master").json()["master"]["version"] == 1


def test_invalid_suggestion_input_never_calls_the_provider(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root)
    client.put("/api/v1/projects/prj_master/master", json={"title": "标题", "body": "原文"})
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(
        master_api.llm, "complete",
        lambda *args, **kwargs: pytest.fail("invalid suggestion called LLM"),
    )

    invalid_action = client.post(
        "/api/v1/projects/prj_master/master/suggestions", json={"action": "invent"}
    )
    missing_selection = client.post(
        "/api/v1/projects/prj_master/master/suggestions",
        json={"action": "clarify", "selection": "does not exist"},
    )

    assert invalid_action.status_code == 400
    assert missing_selection.status_code == 400
    assert client.get("/api/v1/projects/prj_master/master/suggestions").json() == {"items": []}


def test_master_api_errors_are_explicit(client):
    missing = client.get("/api/v1/projects/prj_missing/master")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "project_not_found"


import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())
