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


def test_prepare_returns_ai_titles_and_extracted_sources(client, monkeypatch):
    monkeypatch.setattr(intake_api, "_llm_is_configured", lambda: True)
    seen: dict[str, str] = {}

    def fake_complete(prompt, **kwargs):
        seen["prompt"] = prompt
        return ["工具更快了，人为什么更喘不过气", "绿的是测试，不是产品", "我还是不敢用自己做的东西"]

    monkeypatch.setattr(intake_api.llm, "complete_json", fake_complete)
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
    assert payload["titles"][0].startswith("工具更快了")
    kinds = {item["kind"] for item in payload["sources"]}
    assert kinds == {"wechat", "markdown"}
    wechat = next(item for item in payload["sources"] if item["kind"] == "wechat")
    assert wechat["excerpt"] == "公众号正文摘录。"
    assert "作者还没有标题" in seen["prompt"]
    assert "公众号正文摘录" in seen["prompt"]


def test_prepare_without_llm_still_returns_usable_titles(client, monkeypatch):
    monkeypatch.setattr(intake_api, "_llm_is_configured", lambda: False)

    response = client.post("/api/v1/intake/prepare", data={"idea": "普通人怎样和 AI 一起重新建立工作"})

    assert response.status_code == 200
    titles = response.json()["titles"]
    assert len(titles) >= 3
    assert all(isinstance(item, str) and item.strip() for item in titles)


def test_prepare_rejects_empty_intake(client):
    response = client.post("/api/v1/intake/prepare", data={"idea": "   "})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_intake"


def test_compose_keeps_the_title_the_author_picked(client, tmp_path, monkeypatch):
    created = client.post("/api/v1/projects", json={
        "title": "作者亲手挑的标题",
        "idea": "一团还没整理的想法",
        "audience": "创作者",
        "goal": "一篇公众号草稿",
        "voice": "克制",
        "autonomy": "draft",
    })
    project_id = created.json()["id"]
    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(
        master_api.llm,
        "complete_json",
        lambda prompt, **kwargs: {"title": "模型自己起的标题", "body": "## 问题\n\n正文。"},
    )

    response = client.post(f"/api/v1/projects/{project_id}/compose")

    assert response.status_code == 200
    assert response.json()["title"] == "作者亲手挑的标题"
    stored = client.get(f"/api/v1/projects/{project_id}/master").json()["master"]
    assert stored["title"] == "作者亲手挑的标题"
