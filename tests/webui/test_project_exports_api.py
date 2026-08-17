from __future__ import annotations

import sqlite3
import zipfile

from fastapi.testclient import TestClient

from pipeline import approvals, master_documents, projects, research, variants, visuals
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.app import create_app


def _ready(root):
    project_id = "prj_export_api"
    projects.create_project(
        title="真实内容包", idea="真实观察", audience="独立创作者", goal="完成双平台文章",
        voice="清晰克制", autonomy="collaborate", now="2026-08-09T00:00:00+00:00",
        project_id=project_id, projects_root=root,
    )
    source_ids = []
    for index in range(3):
        source_ids.append(research.add_source(
            project_id, title=f"来源 {index}", reference=f"https://example.com/{index}",
            summary="可靠来源摘要", now=f"2026-08-09T00:0{index}:00+00:00", projects_root=root,
        ).id)
    research.add_claim(
        project_id, text="一个已核查事实", kind="fact", source_ids=[source_ids[0]], status="verified",
        limitation="仅适用于本次案例", counterpoint="工具能力仍在快速变化",
        now="2026-08-09T00:04:00+00:00", projects_root=root,
    )
    research.add_claim(
        project_id, text="验收系统比生成速度更重要", kind="judgment", source_ids=[], status="verified",
        now="2026-08-09T00:05:00+00:00", projects_root=root,
    )
    body = ("## 真实问题\n\n这是经过核查并准备交付的正文。\n\n" * 80).strip()
    master_documents.save_manual(project_id, title="测试全绿，产品仍不能用", body=body,
                                 now="2026-08-09T00:06:00+00:00", projects_root=root)
    slots = [
        {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "封面", "aspect_ratio": "16:9"},
        {"id": "vsl_one", "purpose": "正文插图", "paragraph_anchor": "真实问题", "direction": "插图一", "aspect_ratio": "16:9"},
        {"id": "vsl_two", "purpose": "正文插图", "paragraph_anchor": "准备交付", "direction": "插图二", "aspect_ratio": "16:9"},
    ]
    visuals.save_plan(project_id, bible={"style": "editorial"}, slots=slots, projects_root=root)
    for index, slot in enumerate(slots):
        asset_id = f"vas_export_{index}"
        path = root / project_id / "assets" / f"{asset_id}.png"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"png")
        visuals.record_asset(
            project_id, slot_id=slot["id"], prompt=slot["direction"], model="local-import", size="16:9",
            cost_usd=0, now=f"2026-08-09T00:1{index}:00+00:00", file_path=f"assets/{asset_id}.png",
            status="candidate", asset_id=asset_id, projects_root=root,
        )
        visuals.select_asset(project_id, asset_id, reason="可发布", rating=5, projects_root=root)
    for platform in ("wechat_mp", "toutiao"):
        item = variants.create_from_master(project_id, platform, now="2026-08-09T00:20:00+00:00", projects_root=root)
        variants.set_locked(project_id, platform, locked=True, now="2026-08-09T00:21:00+00:00", projects_root=root)
    approvals.recheck(project_id, actor="tester", now="2026-08-09T00:22:00+00:00", projects_root=root)
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        approvals.decide(project_id, check, approved=True, note="已核对", actor="tester",
                         now="2026-08-09T00:23:00+00:00", projects_root=root)
    return project_id


def test_completed_approval_can_create_local_export_without_publication(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(projects_api, "_PROJECTS_ROOT", root)
    client = TestClient(create_app())
    project_id = _ready(root)
    def official_rows():
        conn = sqlite3.connect(tmp_path / "state.db")
        try:
            return {
                name: conn.execute(f"SELECT * FROM {name}").fetchall()
                for name in ("topics", "contents", "publications", "metrics")
            }
        finally:
            conn.close()
    before = official_rows()

    response = client.post(f"/api/v1/projects/{project_id}/export")

    assert response.status_code == 201
    archive = root / project_id / response.json()["path"]
    assert archive.is_file()
    with zipfile.ZipFile(archive) as package:
        assert {"manifest.json", "wechat_mp.md", "toutiao.md"} <= set(package.namelist())
        assert "assets/vas_export_0.png" in package.namelist()
        assert "![封面](assets/vas_export_0.png)" in package.read("wechat_mp.md").decode("utf-8")
    first_mtime = archive.stat().st_mtime_ns
    repeated = client.post(f"/api/v1/projects/{project_id}/export")
    assert repeated.status_code == 201 and repeated.json()["file_name"] == response.json()["file_name"]
    assert archive.stat().st_mtime_ns == first_mtime
    archive.write_bytes(b"not a zip")
    corrupt = client.post(f"/api/v1/projects/{project_id}/export")
    assert corrupt.status_code == 400 and "corrupt" in corrupt.text
    assert official_rows() == before
