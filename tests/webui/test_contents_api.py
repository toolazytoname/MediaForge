"""M11-G 双模式 contents API 测试。

覆盖：
  - POST /api/v1/contents body_markdown 分支 → 调 manual_creator
  - POST /api/v1/contents topic_id 分支 → 调 creation_bridge（原有）
  - POST body 缺字段 → 400
  - POST manual 成功 → 201 + content_dict(status=draft)
  - PATCH /api/v1/contents/{id} 改 draft 字段 → 200
  - PATCH 非 draft → 409
  - PATCH 不存在 → 404
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.creators import manual as manual_creator
from pipeline.webui import deps
from pipeline.webui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个 test 临时一份 app + 隔离 output 目录 + 隔离 DB。

    修复：原先只隔离了 output 目录，_DB_PATH 仍是 deps 里的默认相对路径
    "state.db"——每次跑这个文件都会经 TestClient 的真实 HTTP 请求把
    "我的手写草稿"/"x"/"draft"/"new title" 这些测试数据写进仓库根目录
    的生产 state.db（create_app() 启动时会对 deps._DB_PATH 建表/写入）。
    必须在 create_app() 之前 monkeypatch deps._DB_PATH 到临时文件。
    """
    import os
    os.environ["MEDIAFORGE_OUTPUT_ROOT"] = str(tmp_path / "output")
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    app = create_app()
    return TestClient(app)


# ── POST 手动模式 ─────────────────────────────


class TestCreateContentManual:
    def test_happy_path_returns_201_draft(self, client):
        body = {
            "title": "我的手写草稿",
            "pillar": "ai_daily",
            "body_markdown": "# 手写标题\n\n这是手写正文。\n",
            "formats": ["xhs", "x"],
        }
        r = client.post("/api/v1/contents", json=body)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "draft"
        assert data["title"] == "我的手写草稿"
        assert data["pillar"] == "ai_daily"
        assert set(data["formats"]) == {"xhs", "x"}
        # canonical.md 落盘
        cp = Path(data["canonical_path"])
        assert cp.is_file()
        assert cp.read_text(encoding="utf-8") == "# 手写标题\n\n这是手写正文。\n"

    def test_empty_title_returns_400(self, client):
        r = client.post("/api/v1/contents", json={
            "title": "",
            "pillar": "ai_daily",
            "body_markdown": "body",
            "formats": [],
        })
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "manual_create_failed"

    def test_invalid_formats_returns_400(self, client):
        r = client.post("/api/v1/contents", json={
            "title": "t", "pillar": "ai_daily",
            "body_markdown": "body", "formats": "notalist",
        })
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "invalid_body"

    def test_no_llm_called(self, client, monkeypatch):
        """成本护栏：手动模式绝不能触发 LLM 调用。"""
        from pipeline.creators import llm as llm_mod
        def _explode(*a, **kw):
            raise AssertionError("LLM called from manual mode!")
        monkeypatch.setattr(llm_mod, "complete", _explode)
        monkeypatch.setattr(llm_mod, "complete_json", _explode)
        r = client.post("/api/v1/contents", json={
            "title": "x", "pillar": "ai_daily",
            "body_markdown": "body", "formats": [],
        })
        assert r.status_code == 201


# ── PATCH ─────────────────────────────


class TestPatchContent:
    def _create_draft(self, client) -> dict:
        r = client.post("/api/v1/contents", json={
            "title": "draft", "pillar": "ai_daily",
            "body_markdown": "draft body", "formats": ["xhs"],
        })
        assert r.status_code == 201
        return r.json()

    def test_patch_title_pillar_formats(self, client):
        d = self._create_draft(client)
        r = client.patch(f"/api/v1/contents/{d['id']}", json={
            "title": "new title",
            "pillar": "finance",
            "formats": ["x", "article"],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "new title"
        assert body["pillar"] == "finance"
        assert set(body["formats"]) == {"x", "article"}

    def test_patch_body_rewrites_file(self, client, tmp_path):
        d = self._create_draft(client)
        r = client.patch(f"/api/v1/contents/{d['id']}", json={
            "body_markdown": "更新后的正文",
        })
        assert r.status_code == 200
        cp = Path(d["canonical_path"])
        assert cp.read_text(encoding="utf-8") == "更新后的正文"

    def test_patch_non_draft_returns_409(self, client):
        from pipeline import db as db_mod
        from pipeline.models import ContentStatus
        from pipeline.webui import deps
        d = self._create_draft(client)
        # 直接走 DB 把这条 draft 推到 gated
        with deps._db() as conn:
            db_mod.transition(conn, "contents", d["id"],
                              ContentStatus.DRAFT.value, ContentStatus.GATED.value)
        r = client.patch(f"/api/v1/contents/{d['id']}", json={"title": "x"})
        assert r.status_code == 409
        assert r.json()["detail"]["error"]["code"] == "conflict_by_status"

    def test_patch_missing_returns_404(self, client):
        r = client.patch("/api/v1/contents/c_nope", json={"title": "x"})
        assert r.status_code == 404
        assert r.json()["detail"]["error"]["code"] == "content_not_found"

    def test_patch_invalid_body_returns_400(self, client):
        d = self._create_draft(client)
        r = client.patch(f"/api/v1/contents/{d['id']}", json={"title": 123})
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "invalid_body"
