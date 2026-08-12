from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline import master_documents, projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import master_documents as master_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    project = project_store.create_project(title="API 项目", idea="观察", audience="创作者", goal="主稿", voice="克制", autonomy="collaborate", now="2026-08-12T10:00:00+00:00", project_id="prj_feedback", projects_root=root)
    master_documents.save_manual(project.id, title="正式标题", body="正式正文保持不变。", now="2026-08-12T10:00:00+00:00", projects_root=root)


def test_whole_article_feedback_creates_review_only_proposal(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    seen = {}
    def fake_complete_json(prompt, **kwargs):
        seen.update(kwargs); seen["prompt"] = prompt
        return {"title": "建议标题", "body": "建议正文"}
    monkeypatch.setattr(master_api.llm, "complete_json", fake_complete_json)

    response = client.post("/api/v1/projects/prj_feedback/article/feedback", json={
        "feedback": "减少说教感，保留真实失败", "target": "更真诚", "readership": "普通人", "platform": "公众号", "values": "不制造焦虑",
    })
    assert response.status_code == 201
    proposal = response.json()
    assert proposal["status"] == "ready" and proposal["state"] == "current"
    assert proposal["feedback"] == "减少说教感，保留真实失败"
    assert "不制造焦虑" in seen["prompt"]
    assert seen["stage"] == "article_feedback_proposal"
    assert client.get("/api/v1/projects/prj_feedback/master").json()["master"]["body"] == "正式正文保持不变。"


def test_provider_error_persists_feedback_for_retry_without_master_write(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("relay failed")))
    failed = client.post("/api/v1/projects/prj_feedback/article/feedback", json={"feedback": "保留真实失败"})
    assert failed.status_code == 502
    feedback_id = failed.json()["detail"]["error"]["feedback_id"]
    listed = client.get("/api/v1/projects/prj_feedback/article/feedback").json()["items"]
    assert listed[0]["id"] == feedback_id and listed[0]["status"] == "failed"
    assert client.get("/api/v1/projects/prj_feedback/master").json()["master"]["version"] == 1

    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: {"title": "重试", "body": "建议"})
    retried = client.post(f"/api/v1/projects/prj_feedback/article/feedback/{feedback_id}/retry", json={})
    assert retried.status_code == 200 and retried.json()["status"] == "ready"
    assert retried.json()["feedback"] == "保留真实失败"


def test_invalid_feedback_never_calls_provider(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call")))
    response = client.post("/api/v1/projects/prj_feedback/article/feedback", json={"feedback": " "})
    assert response.status_code == 400


def test_too_long_feedback_never_calls_provider(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call")))
    response = client.post("/api/v1/projects/prj_feedback/article/feedback", json={"feedback": "x" * 8_001})
    assert response.status_code == 400


import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())
