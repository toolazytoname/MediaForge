from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline import master_documents, projects, variants, visuals
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _ready(root):
    projects.create_project(title="API", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_approval_api", projects_root=root)
    master_documents.save_manual("prj_approval_api", title="主稿", body="正文", now="2026-08-09T00:01:00+00:00", projects_root=root)
    visuals.save_plan("prj_approval_api", bible={"style": "plain"}, slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "方向", "aspect_ratio": "16:9"}], projects_root=root)
    asset = visuals.record_asset("prj_approval_api", slot_id="vsl_cover", prompt="cover", model="fake", size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00", file_path="assets/vas_api.png", status="candidate", asset_id="vas_api", projects_root=root)
    visuals.select_asset("prj_approval_api", asset.id, reason="fit", rating=4, projects_root=root)
    for platform in ("wechat_mp", "toutiao"): variants.create_from_master("prj_approval_api", platform, now="2026-08-09T00:03:00+00:00", projects_root=root)


def test_approval_api_is_human_only_and_has_no_publication_side_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db")); monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app()); _ready(tmp_path / "projects")
    before = (tmp_path / "state.db").read_bytes() if (tmp_path / "state.db").exists() else b""
    rechecked = client.post("/api/v1/projects/prj_approval_api/approval/recheck", json={"actor": "lazy"})
    assert rechecked.status_code == 200 and rechecked.json()["ready"]
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        result = client.post(f"/api/v1/projects/prj_approval_api/approval/checks/{check}", json={"approved": True, "actor": "lazy", "note": "ok"})
        assert result.status_code == 200
    assert result.json()["complete"] and client.get("/api/v1/projects/prj_approval_api/approval").json()["complete"]
    after = (tmp_path / "state.db").read_bytes() if (tmp_path / "state.db").exists() else b""
    assert before == after


def test_approval_api_errors_and_upstream_change_requires_recheck(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db")); monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    client = TestClient(create_app()); root = tmp_path / "projects"; _ready(root)
    assert client.post("/api/v1/projects/prj_approval_api/approval/recheck", json={"actor": "lazy", "extra": 1}).status_code == 400
    assert client.post("/api/v1/projects/prj_approval_api/approval/checks/nope", json={"approved": True, "actor": "lazy"}).status_code == 400
    client.post("/api/v1/projects/prj_approval_api/approval/recheck", json={"actor": "lazy"})
    master_documents.save_manual("prj_approval_api", title="新", body="新正文", now="2026-08-09T00:04:00+00:00", projects_root=root)
    stale = client.get("/api/v1/projects/prj_approval_api/approval")
    assert stale.status_code == 200 and stale.json()["stale"]
    rejected = client.post("/api/v1/projects/prj_approval_api/approval/checks/master", json={"approved": True, "actor": "lazy"})
    assert rejected.status_code == 400 and rejected.json()["detail"]["error"]["code"] == "invalid_approval_request"
    revoked = client.post("/api/v1/projects/prj_approval_api/approval/checks/master", json={"approved": False, "actor": "lazy", "note": "stale"})
    assert revoked.status_code == 400 and "recheck" in revoked.json()["detail"]["error"]["message"]
