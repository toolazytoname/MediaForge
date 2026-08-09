from __future__ import annotations

import json
import pytest

from pipeline import approvals, master_documents, projects, variants, visuals


def _ready(root):
    projects.create_project(title="项目", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_approval", projects_root=root)
    master_documents.save_manual("prj_approval", title="主稿", body="正文", now="2026-08-09T00:01:00+00:00", projects_root=root)
    visuals.save_plan("prj_approval", bible={"style": "plain"}, slots=[{"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "克制", "aspect_ratio": "16:9"}], projects_root=root)
    asset = visuals.record_asset("prj_approval", slot_id="vsl_cover", prompt="cover", model="fake", size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00", file_path="assets/vas_approval.png", status="candidate", asset_id="vas_approval", projects_root=root)
    visuals.select_asset("prj_approval", asset.id, reason="合适", rating=4, projects_root=root)
    for platform in ("wechat_mp", "toutiao"):
        variants.create_from_master("prj_approval", platform, now="2026-08-09T00:03:00+00:00", projects_root=root)
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
    assert not blocked.ready and "未处理上游更新" in "".join(blocked.blockers) and not blocked.approval.checks


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


def test_recheck_reports_missing_package_parts(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(title="项目", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_empty", projects_root=root)
    state = approvals.recheck("prj_empty", actor="lazy", now="2026-08-09T00:01:00+00:00", projects_root=root)
    assert not state.ready and {"缺少主稿", "缺少微信公众号版本", "缺少头条版本", "尚未选择视觉资产"} <= set(state.blockers)
