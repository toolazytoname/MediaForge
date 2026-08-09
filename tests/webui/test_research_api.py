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


def _create_project(root):
    return project_store.create_project(
        title="研究项目", idea="一条观察", audience="创作者", goal="主稿", voice="克制",
        autonomy="collaborate", now="2026-08-09T10:00:00+00:00", project_id="prj_research",
        projects_root=root,
    )


def test_research_api_creates_reads_and_replaces_project_scoped_records(client, tmp_path):
    root = tmp_path / "projects"
    _create_project(root)
    source = client.post("/api/v1/projects/prj_research/research/sources", json={
        "title": "一手研究", "reference": "https://example.com", "summary": "方法说明"
    })
    assert source.status_code == 201
    source_id = source.json()["id"]
    claim = client.post("/api/v1/projects/prj_research/research/claims", json={
        "text": "需要复核的事实", "kind": "fact", "source_ids": [source_id],
        "status": "unverified", "limitation": "样本有限"
    })
    assert claim.status_code == 201
    replacement = client.put(f"/api/v1/projects/prj_research/research/claims/{claim.json()['id']}", json={
        "text": "已复核的事实", "kind": "fact", "source_ids": [source_id], "status": "verified"
    })
    assert replacement.status_code == 200
    board = client.get("/api/v1/projects/prj_research/research")
    assert board.status_code == 200
    assert board.json()["claims"][0]["status"] == "verified"
    assert (root / "prj_research" / "research.json").is_file()


def test_research_api_uses_explicit_error_envelopes(client):
    missing = client.get("/api/v1/projects/prj_missing/research")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "project_not_found"
    invalid = client.post("/api/v1/projects/prj_missing/research/sources", json={"title": "only"})
    assert invalid.status_code == 404
    assert invalid.json()["detail"]["error"]["code"] == "project_not_found"


def test_research_api_exposes_a_corrupt_manifest_without_silent_recovery(client, tmp_path):
    root = tmp_path / "projects"
    _create_project(root)
    (root / "prj_research" / "research.json").write_text(json.dumps({"unknown": True}), encoding="utf-8")

    response = client.get("/api/v1/projects/prj_research/research")

    assert response.status_code == 500
    assert response.json()["detail"]["error"]["code"] == "research_manifest_invalid"
