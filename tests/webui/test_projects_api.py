"""R3 project API tests: sidecar manifests are exposed read-only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pipeline import projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def _create_project(root, project_id: str, updated_at: str):
    return project_store.create_project(
        title=f"{project_id} title",
        idea="一个值得写下来的观察",
        audience="独立创作者",
        goal="完成一篇有依据的主稿",
        voice="清楚、克制",
        autonomy="collaborate",
        now="2026-08-09T08:00:00+00:00",
        project_id=project_id,
        projects_root=root,
    ) if updated_at == "2026-08-09T08:00:00+00:00" else project_store.update_project(
        project_store.create_project(
            title=f"{project_id} title",
            idea="一个值得写下来的观察",
            audience="独立创作者",
            goal="完成一篇有依据的主稿",
            voice="清楚、克制",
            autonomy="collaborate",
            now="2026-08-09T08:00:00+00:00",
            project_id=project_id,
            projects_root=root,
        ),
        now=updated_at,
        projects_root=root,
    )


def test_list_projects_returns_empty_collection(client):
    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_list_projects_is_ordered_by_recent_update(client, tmp_path):
    root = tmp_path / "projects"
    _create_project(root, "prj_older", "2026-08-09T08:00:00+00:00")
    _create_project(root, "prj_newer", "2026-08-09T09:00:00+00:00")

    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == ["prj_newer", "prj_older"]


def test_get_project_returns_sidecar_data(client, tmp_path):
    project = _create_project(
        tmp_path / "projects", "prj_detail", "2026-08-09T08:00:00+00:00"
    )

    response = client.get(f"/api/v1/projects/{project.id}")

    assert response.status_code == 200
    assert response.json()["id"] == project.id
    assert response.json()["title"] == project.title
    assert response.json()["content_ids"] == []


def test_missing_project_returns_error_envelope(client):
    response = client.get("/api/v1/projects/prj_missing")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "project_not_found"


def test_invalid_manifest_returns_visible_server_error(client, tmp_path):
    manifest = tmp_path / "projects" / "prj_broken" / "project.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"not": "a project"}), encoding="utf-8")

    response = client.get("/api/v1/projects")

    assert response.status_code == 500
    detail = response.json()["detail"]["error"]
    assert detail["code"] == "project_manifest_invalid"
    assert "project collection" in detail["message"]
