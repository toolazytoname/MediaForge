from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from pipeline import projects as project_store
from pipeline.creators import image_gen, llm
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _project(root):
    return project_store.create_project(title="项目", idea="我对 AI 的真实观察", audience="读者", goal="文章", voice="真实", autonomy="collaborate", now="2026-08-12T00:00:00+00:00", project_id="prj_generate", projects_root=root)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def test_explicit_generation_persists_article_and_image_partial_failure(client, tmp_path, monkeypatch):
    _project(tmp_path / "projects")
    monkeypatch.setattr(llm, "_PROVIDER", object())
    monkeypatch.setattr(llm, "complete_json", lambda *_args, **_kwargs: {"title": "真实标题", "body": "## 第一节\n\n正文。\n\n## 第二节\n\n结尾。"})

    class Provider:
        _model = "test-image"
        def estimated_cost_usd(self, *, aspect_ratio): return 0.01
        def call(self, prompt, *, aspect_ratio, n, response_format="base64"):
            if "插图一" in prompt: raise image_gen.RetryableError("timeout")
            return [b"png"]
    monkeypatch.setattr(image_gen, "_PROVIDER", Provider())

    response = client.post("/api/v1/projects/prj_generate/article/generate", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "completed_with_errors"
    master = client.get("/api/v1/projects/prj_generate/master").json()["master"]
    assert master["title"] == "真实标题" and master["body"].count("![") == 2
    assert client.post("/api/v1/projects/prj_generate/article/generate", json={}).json()["status"] == "completed_with_errors"


def test_provider_unavailable_does_not_create_a_blank_article(client, tmp_path):
    _project(tmp_path / "projects")
    response = client.post("/api/v1/projects/prj_generate/article/generate", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "llm_provider_unavailable"
    assert client.get("/api/v1/projects/prj_generate/master").json() == {"master": None}
