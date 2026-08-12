from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import local_annotations, master_documents, projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import master_documents as master_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    p = project_store.create_project(title="项目", idea="想法", audience="读者", goal="目标", voice="克制", autonomy="collaborate", now="2026-08-12T10:00:00+00:00", project_id="prj_localapi", projects_root=root)
    master_documents.save_manual(p.id, title="标题", body="把这句写具体。", now="2026-08-12T10:00:00+00:00", projects_root=root)
    return local_annotations.create_text_annotation(p.id, excerpt="写具体", feedback="加一个动作", categories=("text",), now="2026-08-12T10:00:00+00:00", projects_root=root, annotation_id="lan_api")


def test_explicit_local_proposal_generates_review_only_diff(client, tmp_path, monkeypatch):
    annotation = _project(tmp_path / "projects")
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: {"title": "标题", "body": "把今天的一个动作写具体。"})
    response = client.post(f"/api/v1/projects/prj_localapi/article/annotations/{annotation.id}/propose", json={})
    assert response.status_code == 201
    proposal = response.json()
    assert proposal["scope"] == "local_text" and proposal["annotation_excerpt"] == "写具体"
    assert client.get("/api/v1/projects/prj_localapi/master").json()["master"]["version"] == 1


def test_local_proposal_provider_failure_is_retryable_and_orphan_blocks(client, tmp_path, monkeypatch):
    annotation = _project(tmp_path / "projects")
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    failed = client.post(f"/api/v1/projects/prj_localapi/article/annotations/{annotation.id}/propose", json={})
    assert failed.status_code == 502
    proposal_id = failed.json()["detail"]["error"]["feedback_id"]
    captured = []
    def local_retry(prompt, *args, **kwargs):
        captured.append(prompt)
        return {"title": "标题", "body": "建议"}
    monkeypatch.setattr(master_api.llm, "complete_json", local_retry)
    retried = client.post(f"/api/v1/projects/prj_localapi/article/feedback/{proposal_id}/retry", json={})
    assert retried.status_code == 200
    assert retried.json()["scope"] == "local_text"
    assert "所选文本：写具体" in captured[0]
    assert "局部范围：" in captured[0]
    master_documents.save_manual("prj_localapi", title="标题", body="目标已经没了", now="2026-08-12T10:03:00+00:00", projects_root=tmp_path / "projects")
    assert client.post(f"/api/v1/projects/prj_localapi/article/annotations/{annotation.id}/propose", json={}).status_code == 409


def test_failed_local_retry_rejects_an_orphaned_annotation(client, tmp_path, monkeypatch):
    annotation = _project(tmp_path / "projects")
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    failed = client.post(f"/api/v1/projects/prj_localapi/article/annotations/{annotation.id}/propose", json={})
    proposal_id = failed.json()["detail"]["error"]["feedback_id"]
    master_documents.save_manual("prj_localapi", title="标题", body="选中文本已删除", now="2026-08-12T10:04:00+00:00", projects_root=tmp_path / "projects")
    retry = client.post(f"/api/v1/projects/prj_localapi/article/feedback/{proposal_id}/retry", json={})
    assert retry.status_code == 409
    assert retry.json()["detail"]["error"]["code"] == "annotation_obsolete"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db")); monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())
