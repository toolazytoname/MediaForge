"""RV-05: article-local image candidate selection is explicit and recoverable."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from pipeline import master_documents, projects as project_store, visuals
from pipeline.creators import image_gen
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _project(root):
    project = project_store.create_project(
        title="换图", idea="真实想法", audience="读者", goal="文章", voice="克制",
        autonomy="collaborate", now="2026-08-12T10:00:00+00:00", project_id="prj_image_revision", projects_root=root,
    )
    visuals.save_plan(project.id, bible={}, slots=[
        {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "安静", "aspect_ratio": "16:9"},
        {"id": "vsl_inline", "purpose": "插图", "paragraph_anchor": "正文", "direction": "真实", "aspect_ratio": "4:3"},
    ], projects_root=root)
    return project


def _asset(root, project_id, asset_id, slot_id, *, selected=False):
    path = visuals.asset_path(project_id, asset_id, projects_root=root)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(PNG)
    asset = visuals.record_asset(project_id, slot_id=slot_id, prompt=f"{asset_id} prompt", model="fake", size="16:9",
        cost_usd=0.01, now="2026-08-12T10:00:00+00:00", file_path=f"assets/{asset_id}.png", status="candidate", asset_id=asset_id, projects_root=root)
    return visuals.select_asset(project_id, asset.id, reason="initial", rating=None, projects_root=root) if selected else asset


def _seed(root):
    project = _project(root)
    old = _asset(root, project.id, "vas_original", "vsl_cover", selected=True)
    inline = _asset(root, project.id, "vas_inline", "vsl_inline", selected=True)
    old_markdown = f"![封面](/output/projects/{project.id}/{old.file_path})"
    inline_markdown = f"![插图](/output/projects/{project.id}/{inline.file_path})"
    master_documents.save_manual(project.id, title="文章", body=f"{old_markdown}\n\n正文\n\n{inline_markdown}", now="2026-08-12T10:00:01+00:00", projects_root=root)
    return project, old, inline, old_markdown, inline_markdown


def test_candidate_is_not_auto_accepted_and_explicit_replace_updates_only_target_markdown(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; project, old, inline, old_markdown, inline_markdown = _seed(root)
    provider = image_gen.OpenAIImageProvider("fake")
    monkeypatch.setattr(provider, "edit", lambda *args, **kwargs: [PNG]); image_gen.set_provider(provider)

    candidate = client.post(f"/api/v1/projects/{project.id}/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "更明亮", "reference_asset_id": old.id})
    assert candidate.status_code == 201
    candidate = candidate.json()
    assert candidate["status"] == "candidate" and candidate["reference_asset_id"] == old.id
    before = client.get(f"/api/v1/projects/{project.id}/master").json()["master"]
    assert old_markdown in before["body"] and inline_markdown in before["body"]

    applied = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": old.id, "candidate_asset_id": candidate["id"]})
    assert applied.status_code == 200
    after = applied.json()["master"]
    assert f"assets/{candidate['id']}.png" in after["body"]
    assert f"assets/{old.id}.png" not in after["body"]
    assert inline_markdown in after["body"]
    assert after["version"] == before["version"] + 1
    plan = client.get(f"/api/v1/projects/{project.id}/visuals").json()
    statuses = {item["id"]: item["status"] for item in plan["assets"]}
    assert statuses[old.id] == "candidate" and statuses[candidate["id"]] == "selected"
    assert (root / project.id / old.file_path).read_bytes() == PNG


def test_replace_rejects_unknown_candidate_and_stale_current_reference_without_changing_article(client, tmp_path):
    root = tmp_path / "projects"; project, old, _inline, old_markdown, _inline_markdown = _seed(root)
    candidate = _asset(root, project.id, "vas_candidate", "vsl_cover")
    unknown = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": old.id, "candidate_asset_id": "vas_missing"})
    assert unknown.status_code == 400
    master_documents.save_manual(project.id, title="文章", body="正文已手工改变", now="2026-08-12T10:01:00+00:00", projects_root=root)
    stale = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": old.id, "candidate_asset_id": candidate.id})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/projects/{project.id}/master").json()["master"]["body"] == "正文已手工改变"
    # Merely closing/cancelling a candidate is intentionally a no-op: original assets stay available.
    assert (root / project.id / old.file_path).exists()
    assert old_markdown not in "正文已手工改变"


def test_original_can_be_explicitly_restored_and_provider_failure_leaves_auditable_candidate_history(client, tmp_path, monkeypatch):
    root = tmp_path / "projects"; project, old, _inline, old_markdown, _inline_markdown = _seed(root)
    provider = image_gen.OpenAIImageProvider("fake")
    monkeypatch.setattr(provider, "edit", lambda *args, **kwargs: [PNG]); image_gen.set_provider(provider)
    candidate = client.post(f"/api/v1/projects/{project.id}/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "候选", "reference_asset_id": old.id}).json()
    first = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": old.id, "candidate_asset_id": candidate["id"]})
    assert first.status_code == 200
    restored = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": candidate["id"], "candidate_asset_id": old.id})
    assert restored.status_code == 200
    assert old_markdown in restored.json()["master"]["body"]
    assert restored.json()["master"]["history"][-1]["reason"].startswith("image:")

    # A provider error creates a failed record but cannot touch the selected
    # original, the master body, or a path outside this project's asset jail.
    monkeypatch.setattr(provider, "edit", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad image response")))
    failed = client.post(f"/api/v1/projects/{project.id}/visuals/assets/edit", json={"slot_id": "vsl_cover", "prompt": "失败", "reference_asset_id": old.id})
    assert failed.status_code == 502
    plan = client.get(f"/api/v1/projects/{project.id}/visuals").json()
    assert plan["assets"][-1]["status"] == "failed"
    assert client.get(f"/api/v1/projects/{project.id}/master").json()["master"]["body"] == restored.json()["master"]["body"]
    traversal = client.post(f"/api/v1/projects/{project.id}/article/images/replace", json={"current_asset_id": "../../etc/passwd", "candidate_asset_id": old.id})
    assert traversal.status_code == 400


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    image_gen._PROVIDER = None
    return TestClient(create_app())
