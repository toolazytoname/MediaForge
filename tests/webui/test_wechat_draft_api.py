from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import projects as project_store
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.api import wechat_draft as draft_api
from pipeline.webui.app import create_app
from pipeline.wechat_project_draft import WechatDraftReceipt


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_send_wechat_draft_returns_receipt_and_does_not_mark_published(client, tmp_path, monkeypatch):
    project_store.create_project(
        title="标题", idea="想法", audience="读者", goal="草稿", voice="克制",
        autonomy="draft", now="2026-08-14T12:00:00+00:00", project_id="prj_send01",
        projects_root=tmp_path / "projects",
    )

    def fake_send(project_id, **kwargs):
        return WechatDraftReceipt(
            project_id=project_id,
            title="标题",
            media_id="media_live_1",
            destination="wechat_draft",
            published=False,
            sent_at="2026-08-14T12:00:00+00:00",
            message="已送进公众号草稿箱，不会群发。请到微信公众平台 → 草稿箱核对。",
        )

    monkeypatch.setattr(draft_api, "send_project_wechat_draft", fake_send)

    empty = client.get("/api/v1/projects/prj_send01/wechat-draft")
    assert empty.status_code == 200
    assert empty.json() == {"receipt": None}

    sent = client.post("/api/v1/projects/prj_send01/wechat-draft")
    assert sent.status_code == 200
    payload = sent.json()
    assert payload["media_id"] == "media_live_1"
    assert payload["published"] is False
    assert payload["destination"] == "wechat_draft"
