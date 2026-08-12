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


def test_project_material_parse_is_explicit_traceable_and_keeps_bad_url_not_used(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app())
    note = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "text", "value": "第一段真实事实。\n\n第二段。"
    }).json()
    bad_url = client.post("/api/v1/creator-materials", json={
        "draft_id": "draft_12345678", "kind": "url", "value": "http://127.0.0.1/private"
    }).json()
    project = client.post("/api/v1/projects/creator-start", json={
        "prompt": "带资料的文章", "draft_id": "draft_12345678", "material_ids": [note["id"], bad_url["id"]]
    }).json()

    usable = client.post(f"/api/v1/projects/{project['id']}/materials/{note['id']}/parse")
    rejected = client.post(f"/api/v1/projects/{project['id']}/materials/{bad_url['id']}/parse")
    listed = client.get(f"/api/v1/projects/{project['id']}/materials")

    assert usable.status_code == 200 and usable.json()["segments"][0]["citation"] == f"{note['id']}:1"
    assert rejected.status_code == 200 and rejected.json()["status"] == "not_used"
    assert {item["id"]: item["analysis"]["status"] for item in listed.json()["items"]} == {
        note["id"]: "used", bad_url["id"]: "not_used"
    }
