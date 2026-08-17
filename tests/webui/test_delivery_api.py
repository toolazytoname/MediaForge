from fastapi.testclient import TestClient

from pipeline import projects
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def test_capabilities_hide_wechat_direct_and_unapproved_draft_is_409(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    projects.create_project(
        title="项目", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now="2026-08-09T00:00:00+00:00",
        project_id="prj_delivery_api", projects_root=tmp_path / "projects",
    )
    client = TestClient(create_app())
    caps = client.get("/api/v1/capabilities")
    assert caps.status_code == 200
    wechat = next(item for item in caps.json()["items"] if item["platform"] == "wechat_mp")
    toutiao = next(item for item in caps.json()["items"] if item["platform"] == "toutiao")
    assert wechat["delivery"]["direct"] is False
    assert toutiao["delivery"]["direct"] is False
    assert toutiao["delivery"]["export"] is True
    denied = client.post(
        "/api/v1/projects/prj_delivery_api/deliverables/dlv_article_wechat_mp/draft",
        json={"actor": "lazy"},
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["error"]["code"] == "not_approved"
    export = client.post("/api/v1/projects/prj_delivery_api/export")
    assert export.status_code == 409
    assert export.json()["detail"]["error"]["code"] == "not_approved"
    xhs = next(item for item in caps.json()["items"] if item["platform"] == "xiaohongshu")
    assert xhs["delivery"]["direct"] is False
    assert xhs["delivery"]["export"] is True
    hidden = client.post(
        "/api/v1/projects/prj_delivery_api/deliverables/dlv_article_wechat_mp/direct",
        json={"actor": "lazy"},
    )
    assert hidden.status_code == 403
    assert hidden.json()["detail"]["error"]["code"] == "direct_hidden"
