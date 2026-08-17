from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline import master_documents, projects as project_store, visuals
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.api import variants as variants_api
from pipeline.webui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def _project(root):
    project_store.create_project(title="API", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_variant_api", projects_root=root)
    master_documents.save_manual("prj_variant_api", title="主标题", body="主稿正文", now="2026-08-09T00:01:00+00:00", projects_root=root)


def test_api_variants_are_independent_and_preview_is_read_only(client, tmp_path):
    _project(tmp_path / "projects")
    wechat = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp")
    toutiao = client.post("/api/v1/projects/prj_variant_api/variants/toutiao")
    assert wechat.status_code == toutiao.status_code == 201
    edited = client.put("/api/v1/projects/prj_variant_api/variants/wechat_mp", json={"title": "微信", "summary": "**摘要**", "body": "微信**正文**\n\n---", "asset_ids": []})
    assert edited.status_code == 200 and edited.json()["manually_modified"]
    locked = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/lock", json={"locked": True})
    assert locked.status_code == 200 and locked.json()["locked"]
    master_documents.save_manual("prj_variant_api", title="更新", body="更新正文", now="2026-08-09T00:02:00+00:00", projects_root=tmp_path / "projects")
    upstream = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/check-upstream")
    assert upstream.status_code == 200 and upstream.json()["upstream_updated"]
    assert upstream.json()["body"] == "微信**正文**\n\n---"
    preview = client.get("/api/v1/projects/prj_variant_api/variants/wechat_mp/preview")
    assert preview.status_code == 200 and "只读预览" in preview.text
    assert "微信<strong>正文</strong>" in preview.text and "<hr>" in preview.text
    assert "<blockquote><p><strong>摘要</strong></p></blockquote>" in preview.text
    assert client.get("/api/v1/projects/prj_variant_api/variants").json()["variants"][1]["body"] == "主稿正文"


def test_api_rejects_platform_and_keeps_sqlite_and_content_untouched(client, tmp_path):
    _project(tmp_path / "projects")
    bad = client.post("/api/v1/projects/prj_variant_api/variants/xiaohongshu")
    assert bad.status_code == 400 and bad.json()["detail"]["error"]["code"] == "invalid_variant_request"
    before = (tmp_path / "state.db").read_bytes() if (tmp_path / "state.db").exists() else b""
    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp")
    client.get("/api/v1/projects/prj_variant_api/variants/wechat_mp/preview")
    after = (tmp_path / "state.db").read_bytes() if (tmp_path / "state.db").exists() else b""
    assert before == after


def test_preview_resolves_selected_asset_and_restore_and_bad_envelopes(client, tmp_path):
    root = tmp_path / "projects"; _project(root)
    visuals.save_plan("prj_variant_api", bible={"style": "plain"}, slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "方向", "aspect_ratio": "16:9"}], projects_root=root)
    asset = visuals.record_asset("prj_variant_api", slot_id="vsl_cover", prompt="cover", model="fake", size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00", file_path="assets/vas_cover.png", status="candidate", asset_id="vas_cover", projects_root=root)
    (root / "prj_variant_api" / "assets").mkdir(exist_ok=True); (root / "prj_variant_api" / "assets" / "vas_cover.png").write_bytes(b"png")
    visuals.select_asset("prj_variant_api", asset.id, reason="fit", rating=4, projects_root=root)
    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp")
    saved = client.put("/api/v1/projects/prj_variant_api/variants/wechat_mp", json={"title": "改", "summary": "摘要", "body": "正文", "asset_ids": ["vas_cover"]})
    assert saved.status_code == 200
    preview = client.get("/api/v1/projects/prj_variant_api/variants/wechat_mp/preview")
    assert preview.status_code == 200 and '<img src="/output/projects/prj_variant_api/assets/vas_cover.png"' in preview.text
    restored = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/versions/1/restore")
    assert restored.status_code == 200 and restored.json()["version"] == 3
    assert client.put("/api/v1/projects/prj_variant_api/variants/wechat_mp", json={"title": "bad"}).status_code == 400
    assert client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/lock", json={"locked": "yes"}).status_code == 400
    assert client.get("/api/v1/projects/prj_variant_api/variants/x/preview").status_code == 400


def test_ai_adaptation_is_explicit_and_only_creates_a_new_variant(
    client, tmp_path, monkeypatch
):
    _project(tmp_path / "projects")
    monkeypatch.setattr(variants_api, "_llm_is_configured", lambda: True)
    seen = {}
    def fake_complete(prompt, **kwargs):
        seen["prompt"] = prompt
        return {
            "title": "微信原生标题",
            "summary": "面向微信读者的摘要",
            "body": "这是经过平台重组、但没有覆盖主稿的正文。",
        }
    monkeypatch.setattr(variants_api.llm, "complete_json", fake_complete)

    created = client.post(
        "/api/v1/projects/prj_variant_api/variants/wechat_mp",
        json={"adapt_with_ai": True},
    )

    assert created.status_code == 200
    assert created.json()["persisted"] is False
    assert created.json()["title"] == "微信原生标题"
    assert client.get("/api/v1/projects/prj_variant_api/variants").json()["variants"] == []
    assert "[来源标题](URL)" in seen["prompt"]
    assert "不得虚构 URL" in seen["prompt"]
    assert master_documents.load_master(
        "prj_variant_api", projects_root=tmp_path / "projects"
    ).title == "主标题"
    accepted = client.post(
        "/api/v1/projects/prj_variant_api/variants/wechat_mp",
        json={
            "title": created.json()["title"],
            "summary": created.json()["summary"],
            "body": created.json()["body"],
        },
    )
    assert accepted.status_code == 201
    repeated = client.post(
        "/api/v1/projects/prj_variant_api/variants/wechat_mp",
        json={"adapt_with_ai": True},
    )
    assert repeated.status_code == 200


def test_api_acknowledges_latest_master_without_overwriting_platform_copy(client, tmp_path):
    root = tmp_path / "projects"; _project(root)
    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp")
    edited = client.put(
        "/api/v1/projects/prj_variant_api/variants/wechat_mp",
        json={"title": "平台标题", "summary": "平台摘要", "body": "平台正文", "asset_ids": []},
    ).json()
    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/lock", json={"locked": True})
    master_documents.save_manual(
        "prj_variant_api", title="主稿 v2", body="主稿新版正文",
        now="2026-08-09T00:03:00+00:00", projects_root=root,
    )
    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/check-upstream")
    locked = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/acknowledge-master")
    assert locked.status_code == 400 and "unlock" in locked.text

    client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/lock", json={"locked": False})
    acknowledged = client.post("/api/v1/projects/prj_variant_api/variants/wechat_mp/acknowledge-master")
    assert acknowledged.status_code == 200
    assert acknowledged.json()["version"] == edited["version"] + 1
    assert acknowledged.json()["source_master_version"] == 2
    assert acknowledged.json()["body"] == "平台正文"
    assert not acknowledged.json()["upstream_updated"]
