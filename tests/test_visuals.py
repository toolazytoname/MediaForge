from __future__ import annotations

import json

import pytest

from pipeline import projects, visuals


def _project(root):
    return projects.create_project(title="视觉", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_visual", projects_root=root)


def _slot():
    return {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "留白", "aspect_ratio": "16:9"}


def test_plan_assets_selection_and_atomic_history(tmp_path):
    root = tmp_path / "projects"; _project(root)
    plan = visuals.save_plan("prj_visual", bible={"style": "编辑插画"}, slots=[_slot()], projects_root=root)
    assert plan.slots[0].purpose == "封面"
    asset = visuals.record_asset("prj_visual", slot_id="vsl_cover", prompt="blue editorial", model="gpt-image-2", size="16:9", cost_usd=0, now="2026-08-09T00:01:00+00:00", file_path="assets/vas_a.png", status="candidate", asset_id="vas_a", projects_root=root)
    selected = visuals.select_asset("prj_visual", asset.id, reason="适合文章", rating=5, projects_root=root)
    assert selected.status == "selected" and selected.version == 1
    assert not (root / "prj_visual" / "visuals.json.tmp").exists()


def test_bad_slot_or_asset_reference_is_explicit(tmp_path):
    root = tmp_path / "projects"; _project(root)
    with pytest.raises(visuals.VisualsError, match="invalid visual aspect ratio"):
        visuals.save_plan("prj_visual", bible={}, slots=[{**_slot(), "aspect_ratio": "99:1"}], projects_root=root)
    visuals.save_plan("prj_visual", bible={}, slots=[_slot()], projects_root=root)
    with pytest.raises(visuals.VisualsError, match="visual slot not found"):
        visuals.record_asset("prj_visual", slot_id="vsl_missing", prompt="x", model="gpt-image-2", size="1:1", cost_usd=0, now="2026-08-09T00:01:00+00:00", file_path="x", status="candidate", projects_root=root)


@pytest.mark.parametrize("field,value,match", [
    ("file_path", "../escape.png", "file_path"),
    ("failure", "unexpected", "successful visual asset"),
    ("reference_asset_id", "not-an-asset", "invalid id"),
    ("user_rating", 9, "candidate visual asset"),
    ("selection_reason", "secretly selected", "candidate visual asset"),
])
def test_loaded_assets_revalidate_all_write_invariants(tmp_path, field, value, match):
    root = tmp_path / "projects"; _project(root)
    visuals.save_plan("prj_visual", bible={"style": "编辑插画"}, slots=[_slot()], projects_root=root)
    visuals.record_asset("prj_visual", slot_id="vsl_cover", prompt="blue editorial", model="gpt-image-2", size="16:9", cost_usd=0, now="2026-08-09T00:01:00+00:00", file_path="assets/vas_a.png", status="candidate", asset_id="vas_a", projects_root=root)
    path = root / "prj_visual" / "visuals.json"
    raw = json.loads(path.read_text(encoding="utf-8")); raw["assets"][0][field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(visuals.VisualsError, match=match):
        visuals.load_visuals("prj_visual", projects_root=root)
