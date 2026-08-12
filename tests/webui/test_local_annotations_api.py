import pytest
from fastapi.testclient import TestClient

from pipeline import master_documents, projects as project_store, visuals
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    project = project_store.create_project(title="API 项目", idea="观察", audience="创作者", goal="主稿", voice="克制", autonomy="collaborate", now="2026-08-12T10:00:00+00:00", project_id="prj_local_note", projects_root=root)
    master_documents.save_manual(project.id, title="正式标题", body="第一段：真实写作。\n\n第二段：保留判断。", now="2026-08-12T10:00:00+00:00", projects_root=root)
    visuals.save_plan(project.id, bible={}, slots=[{"id": "vsl_cover", "purpose": "cover", "paragraph_anchor": "第一段：真实写作。", "direction": "编辑插画", "aspect_ratio": "16:9"}], projects_root=root)
    asset = visuals.record_asset(project.id, slot_id="vsl_cover", prompt="封面", model="test", size="16:9", cost_usd=0, file_path="assets/vas_note.png", status="candidate", now="2026-08-12T10:00:00+00:00", asset_id="vas_note", projects_root=root)
    visuals.select_asset(project.id, asset.id, reason="文章封面", rating=None, projects_root=root)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_local_text_annotation_is_persisted_with_safe_evidence_and_can_be_removed(client, tmp_path):
    root = tmp_path / "projects"; _project(root)
    created = client.post("/api/v1/projects/prj_local_note/article/annotations/text", json={"excerpt": "保留判断", "feedback": "这句话更具体", "categories": ["style"]})
    assert created.status_code == 201
    item = created.json()
    assert item["kind"] == "text" and item["excerpt"] == "保留判断"
    assert item["structural_anchor"].startswith("body:") and item["source_hash"]
    listed = client.get("/api/v1/projects/prj_local_note/article/annotations")
    assert listed.status_code == 200 and listed.json()["items"][0]["id"] == item["id"]
    assert client.delete(f"/api/v1/projects/prj_local_note/article/annotations/{item['id']}").status_code == 204


def test_image_annotation_uses_existing_asset_and_never_calls_llm(client, tmp_path):
    root = tmp_path / "projects"; _project(root)
    created = client.post("/api/v1/projects/prj_local_note/article/annotations/image", json={"asset_id": "vas_note", "feedback": "主体换为真人", "categories": ["subject", "composition"]})
    assert created.status_code == 201
    assert created.json()["asset_id"] == "vas_note"
    assert created.json()["paragraph_anchor"] == "第一段：真实写作。"


def test_annotation_input_is_strict_and_bad_selection_does_not_create_record(client, tmp_path):
    root = tmp_path / "projects"; _project(root)
    bad = client.post("/api/v1/projects/prj_local_note/article/annotations/text", json={"excerpt": "不存在", "feedback": "意见", "categories": [], "unexpected": True})
    assert bad.status_code == 400
    assert client.get("/api/v1/projects/prj_local_note/article/annotations").json()["items"] == []
