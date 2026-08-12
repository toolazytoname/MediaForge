from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def test_creator_material_endpoints_allow_partial_failure_and_one_start_request(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app())

    text = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "text", "value": "我自己的记录"
    })
    bad = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "url", "value": "not a URL"
    })
    upload = client.post("/api/v1/creator-materials/upload", data={"draft_id": "draft_12345678"}, files={
        "file": ("reference.md", "# 参考\n\n真实材料".encode(), "text/markdown")
    })
    assert text.status_code == 201 and bad.status_code == 201 and upload.status_code == 201
    assert bad.json()["status"] == "failed"

    created = client.post("/api/v1/projects/creator-start", json={
        "prompt": "把我的观察写成文章", "draft_id": "draft_12345678",
        "material_ids": [text.json()["id"], upload.json()["id"]],
    })
    assert created.status_code == 201
    materials = client.get(f"/api/v1/projects/{created.json()['id']}/materials")
    assert [item["id"] for item in materials.json()["items"]] == [text.json()["id"], upload.json()["id"]]


def test_creator_material_api_rejects_invalid_file_without_losing_existing_items(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app())
    good = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "text", "value": "保留我"
    })
    bad = client.post("/api/v1/creator-materials/upload", data={"draft_id": "draft_12345678"}, files={
        "file": ("bad.pdf", b"broken", "application/pdf")
    })
    items = client.get("/api/v1/creator-materials/drafts/draft_12345678")
    assert good.status_code == 201 and bad.status_code == 400
    assert [item["id"] for item in items.json()["items"]] == [good.json()["id"]]


def test_creator_start_cleans_up_its_new_project_when_attachment_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app())
    item = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "text", "value": "我的资料"
    }).json()
    monkeypatch.setattr(projects_api.creator_materials, "attach_draft_materials", lambda *args, **kwargs: (_ for _ in ()).throw(projects_api.creator_materials.CreatorMaterialError("disk error")))

    response = client.post("/api/v1/projects/creator-start", json={
        "prompt": "一次创建", "draft_id": "draft_12345678", "material_ids": [item["id"]],
    })
    assert response.status_code == 400
    assert list((tmp_path / "projects").glob("prj_*/project.json")) == []
