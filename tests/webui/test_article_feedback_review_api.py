from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline import article_feedback, master_documents, projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    project = project_store.create_project(title="API 项目", idea="观察", audience="创作者", goal="主稿", voice="克制", autonomy="collaborate", now="2026-08-12T10:00:00+00:00", project_id="prj_reviewapi", projects_root=root)
    master_documents.save_manual(project.id, title="正式标题", body="第一段原文。\n\n第二段原文。", now="2026-08-12T10:00:00+00:00", projects_root=root)
    return article_feedback.create_proposal(project.id, feedback="更具体", target=None, readership=None, platform=None, values=None, proposed_title="建议标题", proposed_body="第一段建议。\n\n第二段原文。", now="2026-08-12T10:01:00+00:00", projects_root=root, proposal_id="afp_reviewapi")


def test_accept_and_reject_are_explicit_and_refuse_stale(client, tmp_path):
    root = tmp_path / "projects"; proposal = _project(root)
    rejected = client.post(f"/api/v1/projects/prj_reviewapi/article/feedback/{proposal.id}/reject", json={})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.get("/api/v1/projects/prj_reviewapi/master").json()["master"]["version"] == 1

    ready = article_feedback.create_proposal("prj_reviewapi", feedback="另一条", target=None, readership=None, platform=None, values=None, proposed_title="另一建议", proposed_body="另一建议正文", now="2026-08-12T10:02:00+00:00", projects_root=root, proposal_id="afp_acceptapi")
    accepted = client.post(f"/api/v1/projects/prj_reviewapi/article/feedback/{ready.id}/accept", json={"title": "作者标题", "body": "作者编辑后的建议"})
    assert accepted.status_code == 200
    assert accepted.json()["master"]["version"] == 2
    assert accepted.json()["proposal"]["accepted_body"] == "作者编辑后的建议"

    stale = article_feedback.create_proposal("prj_reviewapi", feedback="过期", target=None, readership=None, platform=None, values=None, proposed_title="过期", proposed_body="过期正文", now="2026-08-12T10:03:00+00:00", projects_root=root, proposal_id="afp_staleapi")
    master_documents.save_manual("prj_reviewapi", title="并发标题", body="并发正文", now="2026-08-12T10:04:00+00:00", projects_root=root)
    response = client.post(f"/api/v1/projects/prj_reviewapi/article/feedback/{stale.id}/accept", json={"title": "不应写入", "body": "不应写入"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "feedback_obsolete"


def test_review_reject_requires_empty_body_and_malformed_sidecar_is_not_accepted(client, tmp_path):
    root = tmp_path / "projects"; proposal = _project(root)
    assert client.post(f"/api/v1/projects/prj_reviewapi/article/feedback/{proposal.id}/reject", json={"bad": True}).status_code == 400
    (root / "prj_reviewapi" / "article_feedback.json").write_text("{}", encoding="utf-8")
    assert client.get("/api/v1/projects/prj_reviewapi/article/feedback").status_code == 500


import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())
