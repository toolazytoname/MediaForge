from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from pipeline import projects as project_store
from pipeline.creators import image_gen
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    return project_store.create_project(title="视觉 API", idea="观察", audience="创作者", goal="主稿", voice="克制", autonomy="collaborate", now="2026-08-09T10:00:00+00:00", project_id="prj_visual_api", projects_root=root)


def _slot():
    return {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "安静的编辑插画", "aspect_ratio": "16:9"}


def test_visual_plan_no_provider_and_fake_generation_are_auditable(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    saved = client.put("/api/v1/projects/prj_visual_api/visuals", json={"bible": {"style": "克制"}, "slots": [_slot()]})
    assert saved.status_code == 200
    unavailable = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_cover", "prompt": "封面"})
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["error"]["code"] == "image_provider_unavailable"
    unavailable_asset = client.get("/api/v1/projects/prj_visual_api/visuals").json()["assets"][0]
    assert unavailable_asset["status"] == "failed"
    assert unavailable_asset["failure"] == "GPT Image 2 is unavailable. Configure OPENAI_API_KEY in Settings."
    bad_prompt = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_cover", "prompt": "   "})
    bad_reference = client.post("/api/v1/projects/prj_visual_api/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "edit", "reference_asset_id": 1})
    assert bad_prompt.status_code == 400 and bad_reference.status_code == 400

    provider = image_gen.OpenAIImageProvider("fake")
    monkeypatch.setattr(provider, "call", lambda *args, **kwargs: [b"fake png"])
    image_gen.set_provider(provider)
    generated = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_cover", "prompt": "封面"})
    assert generated.status_code == 201
    asset = generated.json()
    assert asset["status"] == "candidate" and asset["cost_usd"] > 0
    assert (root / "prj_visual_api" / asset["file_path"]).read_bytes() == b"fake png"
    assert client.get("/api/v1/projects/prj_visual_api/visuals").json()["assets"][-1]["id"] == asset["id"]

    selected = client.post(f"/api/v1/projects/prj_visual_api/visuals/assets/{asset['id']}/select", json={"reason": "贴合主张", "rating": 5})
    assert selected.status_code == 200 and selected.json()["status"] == "selected"


def test_visual_failure_is_recorded_without_writing_master_or_sqlite(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    client.put("/api/v1/projects/prj_visual_api/visuals", json={"bible": {}, "slots": [_slot()]})
    provider = image_gen.OpenAIImageProvider("fake")
    monkeypatch.setattr(provider, "call", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad response")))
    image_gen.set_provider(provider)
    failed = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_cover", "prompt": "封面"})
    assert failed.status_code == 502
    plan = client.get("/api/v1/projects/prj_visual_api/visuals").json()
    assert plan["assets"][0]["status"] == "failed"
    assert plan["assets"][0]["failure"] == "bad response"
    assert not (root / "prj_visual_api" / "master.json").exists()


def test_visual_edit_retries_uses_existing_candidate_and_rejects_unknown_slot(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; _project(root)
    client.put("/api/v1/projects/prj_visual_api/visuals", json={"bible": {}, "slots": [_slot()]})
    provider = image_gen.OpenAIImageProvider("fake")
    calls = {"count": 0}
    def fake_call(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise image_gen.RetryableError("rate limited")
        return [b"source"]
    monkeypatch.setattr(provider, "call", fake_call)
    monkeypatch.setattr(provider, "edit", lambda *args, **kwargs: [b"edited"])
    monkeypatch.setattr("pipeline.webui.api.visuals.time.sleep", lambda _: None)
    image_gen.set_provider(provider)
    source = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_cover", "prompt": "封面"}).json()
    assert calls["count"] == 2
    edited = client.post("/api/v1/projects/prj_visual_api/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "改成深色", "reference_asset_id": source["id"]})
    assert edited.status_code == 201 and edited.json()["reference_asset_id"] == source["id"]
    bad = client.post("/api/v1/projects/prj_visual_api/visuals/assets", json={"slot_id": "vsl_missing", "prompt": "x"})
    assert bad.status_code == 400
    unknown_reference = client.post("/api/v1/projects/prj_visual_api/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "x", "reference_asset_id": "vas_missing"})
    assert unknown_reference.status_code == 400
    assert unknown_reference.json()["detail"]["error"]["code"] == "invalid_visual_request"


def test_provider_status_and_local_png_import_make_missing_key_recoverable(
    client, tmp_path
):
    root = tmp_path / "projects"
    _project(root)
    client.put(
        "/api/v1/projects/prj_visual_api/visuals",
        json={"bible": {"style": "克制"}, "slots": [_slot()]},
    )
    status = client.get("/api/v1/projects/prj_visual_api/visuals/provider")
    assert status.status_code == 200
    assert status.json()["available"] is False
    # A minimal valid 1x1 PNG. Import is local-only and records zero API cost.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    imported = client.post(
        "/api/v1/projects/prj_visual_api/visuals/assets/import",
        json={
            "slot_id": "vsl_cover",
            "prompt": "由创作者导入的真实封面",
            "file_name": "cover.png",
            "data_base64": base64.b64encode(png).decode(),
        },
    )
    assert imported.status_code == 201
    assert imported.json()["model"] == "local-import"
    assert imported.json()["cost_usd"] == 0
    assert (root / "prj_visual_api" / imported.json()["file_path"]).read_bytes() == png


import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    image_gen._PROVIDER = None
    return TestClient(create_app())
