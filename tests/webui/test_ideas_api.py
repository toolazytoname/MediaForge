from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline.webui import deps
from pipeline.webui.api import ideas as ideas_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(ideas_api, "_IDEAS_ROOT", tmp_path / "ideas")
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def _project_payload():
    return {
        "title": "创作工作台的第一步",
        "audience": "独立创作者",
        "goal": "完成一篇有依据的主稿",
        "voice": "清楚、克制",
        "autonomy": "collaborate",
    }


def test_create_list_and_promote_idea_idempotently(client):
    create = client.post("/api/v1/ideas", json={
        "input_type": "thought", "content": "创作者不该先学习状态机。", "title": "创作工作台的第一步",
    })
    assert create.status_code == 201
    idea_id = create.json()["id"]
    assert client.get("/api/v1/ideas").json()["total"] == 1

    first = client.post(f"/api/v1/ideas/{idea_id}/promote-to-project", json=_project_payload())
    assert first.status_code == 201
    assert first.json()["idea"]["project_id"] == first.json()["project"]["id"]

    again = client.post(f"/api/v1/ideas/{idea_id}/promote-to-project", json=_project_payload())
    assert again.status_code == 200
    assert again.json() == first.json()


def test_create_project_requires_all_fields_and_does_not_touch_sqlite(client, tmp_path):
    response = client.post("/api/v1/projects", json={"title": "only title"})

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_project_input"
    assert not (tmp_path / "projects").exists()


def test_create_project_writes_only_its_sidecar(client, tmp_path):
    response = client.post("/api/v1/projects", json={
        **_project_payload(), "idea": "从一条真实观察开始。",
    })

    assert response.status_code == 201
    project_id = response.json()["id"]
    assert (tmp_path / "projects" / project_id / "project.json").is_file()
    assert not (tmp_path / "ideas").exists()


def test_idea_input_validation_uses_error_envelope(client):
    response = client.post("/api/v1/ideas", json={
        "input_type": "url", "content": "not-a-url", "title": "坏链接",
    })

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_idea_input"
