from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline.webui import deps
from pipeline.webui.api import intake as intake_api
from pipeline.webui.api import master_documents as master_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_prepare_extracts_sources_and_does_not_pick_a_title(client, monkeypatch):
    monkeypatch.setattr(
        intake_api.intake,
        "fetch_url_material",
        lambda url: intake_api.intake.IntakeSource(
            kind="wechat",
            title="一篇公众号",
            reference=url,
            excerpt="公众号正文摘录。",
            failure=None,
        ),
    )

    response = client.post(
        "/api/v1/intake/prepare",
        data={
            "idea": "测试全绿了我还是不敢用",
            "urls": "https://mp.weixin.qq.com/s/abc",
        },
        files={"files": ("note.md", "# 笔记\n\n本地摘录。".encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["titles"] == []
    kinds = {item["kind"] for item in payload["sources"]}
    assert kinds == {"wechat", "markdown"}
    wechat = next(item for item in payload["sources"] if item["kind"] == "wechat")
    assert wechat["excerpt"] == "公众号正文摘录。"


def test_prepare_rejects_empty_intake(client):
    response = client.post("/api/v1/intake/prepare", data={"idea": "   "})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_intake"


def test_titles_come_from_the_finished_article(client, tmp_path, monkeypatch):
    created = client.post("/api/v1/projects", json={
        "title": "未命名文章",
        "idea": "一团还没整理的想法",
        "audience": "创作者",
        "goal": "一篇公众号草稿",
        "voice": "克制",
        "autonomy": "draft",
    })
    project_id = created.json()["id"]
    client.put(f"/api/v1/projects/{project_id}/master", json={
        "title": "工作标题",
        "body": "## 问题\n\n测试全绿了，我还是不敢把链接发给朋友。\n\n## 主张\n\n绿的是测试，不是产品。",
    })
    seen: dict[str, str] = {}

    def fake_complete(prompt, **kwargs):
        seen["prompt"] = prompt
        return ["绿的是测试，不是产品", "测试全绿了，我还是不敢发出去", "我不敢把链接发给朋友"]

    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", fake_complete)

    missing = client.post("/api/v1/projects/prj_missing/master/titles")
    assert missing.status_code == 404

    empty = client.post("/api/v1/projects/does-not/master/titles")
    assert empty.status_code in {400, 404}

    response = client.post(f"/api/v1/projects/{project_id}/master/titles")
    assert response.status_code == 200
    assert response.json()["titles"][0] == "绿的是测试，不是产品"
    assert "绿的是测试，不是产品" in seen["prompt"]
    assert "成稿正文" in seen["prompt"]

    applied = client.post(f"/api/v1/projects/{project_id}/master/title", json={"title": "绿的是测试，不是产品"})
    assert applied.status_code == 200
    assert applied.json()["title"] == "绿的是测试，不是产品"
    assert applied.json()["body"].startswith("## 问题")
    listed = client.get("/api/v1/projects").json()["items"]
    assert any(item["id"] == project_id and item["title"] == "绿的是测试，不是产品" for item in listed)
