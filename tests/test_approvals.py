from __future__ import annotations

import json
import pytest

from pipeline import approvals, master_documents, projects, research, variants, visuals


def _ready(root):
    projects.create_project(title="项目", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_approval", projects_root=root)
    source_ids = [research.add_source("prj_approval", title=f"来源{i}", reference=f"https://example.com/{i}", summary="摘要", now="2026-08-09T00:00:30+00:00", projects_root=root).id for i in range(3)]
    research.add_claim("prj_approval", text="已核查事实", kind="fact", source_ids=[source_ids[0]], status="verified", now="2026-08-09T00:00:40+00:00", projects_root=root)
    research.add_claim("prj_approval", text="个人判断", kind="judgment", source_ids=[], status="verified", now="2026-08-09T00:00:50+00:00", projects_root=root)
    master_documents.save_manual("prj_approval", title="主稿", body="足够长的真实正文。" * 120, now="2026-08-09T00:01:00+00:00", projects_root=root)
    slots = [{"id": f"vsl_{name}", "purpose": purpose, "paragraph_anchor": None if name == "cover" else "正文", "direction": "克制", "aspect_ratio": "16:9"} for name, purpose in (("cover", "封面"), ("one", "正文插图一"), ("two", "正文插图二"))]
    visuals.save_plan("prj_approval", bible={"style": "plain"}, slots=slots, projects_root=root)
    for index, slot in enumerate(slots):
        asset_id = f"vas_approval_{index}"
        asset = visuals.record_asset("prj_approval", slot_id=slot["id"], prompt="visual", model="fake", size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00", file_path=f"assets/{asset_id}.png", status="candidate", asset_id=asset_id, projects_root=root)
        visuals.select_asset("prj_approval", asset.id, reason="合适", rating=4, projects_root=root)
    for platform in ("wechat_mp", "toutiao"):
        variants.create_from_master("prj_approval", platform, now="2026-08-09T00:03:00+00:00", projects_root=root)
        variants.set_locked("prj_approval", platform, locked=True, now="2026-08-09T00:03:30+00:00", projects_root=root)
    return "prj_approval"


def test_recheck_decisions_history_and_atomic_round_trip(tmp_path):
    root = tmp_path / "projects"; project_id = _ready(root)
    assert approvals.status(project_id, projects_root=root).ready
    state = approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    assert state.ready and not state.complete and [item.status for item in state.approval.checks] == ["pending"] * 4
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        state = approvals.decide(project_id, check, approved=True, note="已检查", actor="lazy", now="2026-08-09T00:05:00+00:00", projects_root=root)
    assert state.complete and len(state.approval.history) == 5
    state = approvals.decide(project_id, "master", approved=False, note="再看", actor="lazy", now="2026-08-09T00:06:00+00:00", projects_root=root)
    assert not state.complete and state.approval.checks[0].approved_at is None
    assert not (root / project_id / "approval.json.tmp").exists()
    assert approvals.load_approval(project_id, projects_root=root) == state.approval


def test_blockers_stale_upstream_and_recheck_resets_approval(tmp_path):
    root = tmp_path / "projects"; project_id = _ready(root)
    state = approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        state = approvals.decide(project_id, check, approved=True, note=None, actor="lazy", now="2026-08-09T00:05:00+00:00", projects_root=root)
    master_documents.save_manual(project_id, title="更新", body="更新正文", now="2026-08-09T00:06:00+00:00", projects_root=root)
    assert approvals.status(project_id, projects_root=root).stale
    with pytest.raises(approvals.ApprovalError, match="recheck"):
        approvals.decide(project_id, "master", approved=True, note=None, actor="lazy", now="2026-08-09T00:07:00+00:00", projects_root=root)
    variants.check_upstream(project_id, "wechat_mp", now="2026-08-09T00:07:00+00:00", projects_root=root)
    blocked = approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:08:00+00:00", projects_root=root)
    assert not blocked.ready and "旧主稿" in "".join(blocked.blockers) and not blocked.approval.checks


def test_research_change_makes_completed_approval_stale(tmp_path):
    root = tmp_path / "projects"; project_id = _ready(root)
    state = approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        state = approvals.decide(project_id, check, approved=True, note=None, actor="lazy", now="2026-08-09T00:05:00+00:00", projects_root=root)
    assert state.complete
    research.add_claim(project_id, text="新增判断", kind="judgment", source_ids=[], status="verified", now="2026-08-09T00:06:00+00:00", projects_root=root)
    assert approvals.status(project_id, projects_root=root).stale


def test_legacy_snapshot_without_research_fingerprint_loads_as_stale(tmp_path):
    root = tmp_path / "projects"; project_id = _ready(root)
    approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    path = root / project_id / "approval.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["snapshot"]["research_fingerprint"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    state = approvals.status(project_id, projects_root=root)
    assert state.stale
    assert state.approval.snapshot is not None
    assert state.approval.snapshot.research_fingerprint == "0" * 64


def test_rejects_bad_manifest_and_cannot_forge_complete(tmp_path):
    root = tmp_path / "projects"; project_id = _ready(root)
    approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    path = root / project_id / "approval.json"; raw = json.loads(path.read_text())
    raw["complete"] = True; path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(approvals.ApprovalError, match="unknown"):
        approvals.load_approval(project_id, projects_root=root)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(approvals.ApprovalError, match="invalid"):
        approvals.load_approval(project_id, projects_root=root)


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["checks"][0].update(status="approved"),
    lambda raw: raw["checks"][0].update(id="other"),
    lambda raw: raw["snapshot"].update(variant_versions={"wechat_mp": 1}),
    lambda raw: raw["history"][0].update(action="published"),
    lambda raw: raw["history"].extend([dict(raw["history"][0])] * 101),
])
def test_manifest_strictly_rejects_forged_check_snapshot_and_history(tmp_path, mutate):
    root = tmp_path / "projects"; project_id = _ready(root)
    approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    path = root / project_id / "approval.json"; raw = json.loads(path.read_text()); mutate(raw); path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(approvals.ApprovalError): approvals.load_approval(project_id, projects_root=root)


def test_legacy_wechat_toutiao_checks_still_load(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    path = root / project_id / "approval.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["checks"] = [
        {"id": item, "status": "pending", "note": None, "approved_by": None, "approved_at": None}
        for item in ("master", "visuals", "wechat_mp", "toutiao")
    ]
    raw["snapshot"].pop("deliverable_versions", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = approvals.load_approval(project_id, projects_root=root)
    assert [item.id for item in loaded.checks] == ["master", "visuals", "wechat_mp", "toutiao"]
    assert loaded.snapshot is not None
    assert loaded.snapshot.deliverable_versions["dlv_article_wechat_mp"] == raw["snapshot"]["variant_versions"]["wechat_mp"]


def test_recheck_reports_missing_package_parts(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(title="项目", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_empty", projects_root=root)
    state = approvals.recheck("prj_empty", actor="lazy", now="2026-08-09T00:01:00+00:00", projects_root=root)
    assert not state.ready and {"缺少主稿", "缺少微信公众号版本", "缺少头条版本", "尚未选择视觉资产"} <= set(state.blockers)
