from __future__ import annotations

from fastapi.testclient import TestClient

from pipeline import db, projects as project_store, visuals
from pipeline.webui import deps
from pipeline.webui.api import master_documents as master_api
from pipeline.webui.api import projects as projects_api
from pipeline.webui.api import variants as variants_api
from pipeline.webui.app import create_app


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", tmp_path / "projects")
    return TestClient(create_app())


def _create(root, *, autonomy: str, project_id: str) -> None:
    project_store.create_project(
        title="API", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy=autonomy, now="2026-08-09T00:00:00+00:00",
        project_id=project_id, projects_root=root,
    )


def test_assist_llm_project_apis_are_400_and_do_not_write_llm_calls(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _create(root, autonomy="assist", project_id="prj_assist")
    client = _client(tmp_path, monkeypatch)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    before = conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"]

    def boom(*_args, **_kwargs):
        raise AssertionError("assist must not call the model")

    monkeypatch.setattr(master_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(master_api.llm, "complete_json", boom)
    monkeypatch.setattr(master_api.llm, "complete", boom)
    monkeypatch.setattr(variants_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(variants_api.llm, "complete_json", boom)

    draft = client.post("/api/v1/projects/prj_assist/master/draft")
    suggestion = client.post(
        "/api/v1/projects/prj_assist/master/suggestions",
        json={"action": "clarify"},
    )
    adapt = client.post(
        "/api/v1/projects/prj_assist/variants/wechat_mp",
        json={"adapt_with_ai": True},
    )
    generate = client.post(
        "/api/v1/projects/prj_assist/visuals/assets",
        json={"slot_id": "vsl_cover", "prompt": "封面"},
    )
    for response in (draft, suggestion, adapt, generate):
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"]["code"] == "autonomy_forbids_llm"
    after = conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"]
    assert after == before


def test_collaborate_adapt_does_not_persist(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _create(root, autonomy="collaborate", project_id="prj_collab")
    from pipeline import master_documents
    master_documents.save_manual(
        "prj_collab", title="主标题", body="主稿正文",
        now="2026-08-09T00:01:00+00:00", projects_root=root,
    )
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(variants_api, "_llm_is_configured", lambda: True)
    monkeypatch.setattr(
        variants_api.llm, "complete_json",
        lambda *args, **kwargs: {"title": "预览标题", "summary": "预览摘要", "body": "预览正文"},
    )
    preview = client.post(
        "/api/v1/projects/prj_collab/variants/wechat_mp",
        json={"adapt_with_ai": True},
    )
    assert preview.status_code == 200
    assert preview.json()["persisted"] is False
    assert preview.json()["title"] == "预览标题"
    listed = client.get("/api/v1/projects/prj_collab/variants").json()["variants"]
    assert listed == []
    accepted = client.post(
        "/api/v1/projects/prj_collab/variants/wechat_mp",
        json={"title": "预览标题", "summary": "预览摘要", "body": "预览正文"},
    )
    assert accepted.status_code == 201
    assert accepted.json()["title"] == "预览标题"
    assert accepted.json()["persisted"] is True


def test_next_action_and_visual_library_and_pack_prepare(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _create(root, autonomy="assist", project_id="prj_cta")
    visuals.save_plan(
        "prj_cta", bible={"style": "plain"},
        slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "方向", "aspect_ratio": "16:9"}],
        projects_root=root,
    )
    visuals.record_asset(
        "prj_cta", slot_id="vsl_cover", prompt="cover", model="local-import",
        size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00",
        file_path="assets/vas_cover.png", status="candidate", asset_id="vas_cover",
        projects_root=root,
    )
    (root / "prj_cta" / "assets").mkdir(exist_ok=True)
    (root / "prj_cta" / "assets" / "vas_cover.png").write_bytes(b"png")
    client = _client(tmp_path, monkeypatch)
    nxt = client.get("/api/v1/projects/prj_cta/next-action")
    assert nxt.status_code == 200
    assert nxt.json()["cta"]["label"] == "继续研究"
    assert nxt.json()["policy"]["label"] == "手工"
    assert nxt.json()["policy"]["llm_allowed"] is False
    library = client.get("/api/v1/visual-library")
    assert library.status_code == 200
    ids = [item["id"] for item in library.json()["items"]]
    assert "vas_cover" in ids
    policies = client.get("/api/v1/autonomy-policies")
    assert [item["label"] for item in policies.json()["items"]] == ["手工", "协作", "AI 起草", "自动内容包"]

    _create(root, autonomy="pack", project_id="prj_pack_api")
    prepared = client.post("/api/v1/projects/prj_pack_api/pack/prepare")
    assert prepared.status_code == 201
    assert prepared.json()["terminal_status"] in {"drafting", "ready_for_approval"}


def test_draft_api_checks_approval_before_config(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _create(root, autonomy="collaborate", project_id="prj_order")
    client = _client(tmp_path, monkeypatch)
    denied = client.post(
        "/api/v1/projects/prj_order/deliverables/dlv_article_wechat_mp/draft",
        json={"actor": "lazy"},
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["error"]["code"] == "not_approved"
