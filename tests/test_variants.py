from __future__ import annotations

import json
import pytest

from pipeline import master_documents, projects, variants, visuals


def _project(root):
    projects.create_project(title="项目", idea="想法", audience="读者", goal="文章", voice="清晰", autonomy="collaborate", now="2026-08-09T00:00:00+00:00", project_id="prj_variant", projects_root=root)
    master_documents.save_manual("prj_variant", title="主标题", body="主稿正文", now="2026-08-09T00:01:00+00:00", projects_root=root)


def test_create_is_idempotent_and_manual_variant_is_independent(tmp_path):
    root = tmp_path / "projects"; _project(root)
    first = variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    assert variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:03:00+00:00", projects_root=root) == first
    edited = variants.save_manual("prj_variant", "wechat_mp", title="微信标题", summary="微信摘要", body="微信正文", asset_ids=[], now="2026-08-09T00:04:00+00:00", projects_root=root)
    toutiao = variants.create_from_master("prj_variant", "toutiao", now="2026-08-09T00:05:00+00:00", projects_root=root)
    assert edited.body == "微信正文" and edited.manually_modified and toutiao.body == "主稿正文"
    assert not (root / "prj_variant" / "variants.json.tmp").exists()


def test_lock_upstream_and_restore_never_overwrite_variant(tmp_path):
    root = tmp_path / "projects"; _project(root)
    variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    variants.save_manual("prj_variant", "wechat_mp", title="人工", summary="摘要", body="人工正文", asset_ids=[], now="2026-08-09T00:03:00+00:00", projects_root=root)
    locked = variants.set_locked("prj_variant", "wechat_mp", locked=True, now="2026-08-09T00:04:00+00:00", projects_root=root)
    with pytest.raises(variants.VariantsError, match="locked"):
        variants.save_manual("prj_variant", "wechat_mp", title="x", summary="x", body="x", asset_ids=[], now="2026-08-09T00:05:00+00:00", projects_root=root)
    master_documents.save_manual("prj_variant", title="新主稿", body="新正文", now="2026-08-09T00:06:00+00:00", projects_root=root)
    changed = variants.check_upstream("prj_variant", "wechat_mp", now="2026-08-09T00:07:00+00:00", projects_root=root)
    assert locked.locked and changed.upstream_updated and changed.body == "人工正文"
    variants.set_locked("prj_variant", "wechat_mp", locked=False, now="2026-08-09T00:08:00+00:00", projects_root=root)
    assert variants.restore_version("prj_variant", "wechat_mp", 1, now="2026-08-09T00:09:00+00:00", projects_root=root).body == "主稿正文"


def test_acknowledge_master_update_is_explicit_versioned_and_never_overwrites(tmp_path):
    root = tmp_path / "projects"; _project(root)
    variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    edited = variants.save_manual(
        "prj_variant", "wechat_mp", title="人工标题", summary="人工摘要", body="人工平台正文",
        asset_ids=[], now="2026-08-09T00:03:00+00:00", projects_root=root,
    )
    variants.set_locked("prj_variant", "wechat_mp", locked=True, now="2026-08-09T00:04:00+00:00", projects_root=root)
    master_documents.save_manual("prj_variant", title="主稿 v2", body="主稿更新正文", now="2026-08-09T00:05:00+00:00", projects_root=root)
    changed = variants.check_upstream("prj_variant", "wechat_mp", now="2026-08-09T00:06:00+00:00", projects_root=root)
    assert changed.upstream_updated
    with pytest.raises(variants.VariantsError, match="unlock"):
        variants.acknowledge_master_update("prj_variant", "wechat_mp", now="2026-08-09T00:07:00+00:00", projects_root=root)

    variants.set_locked("prj_variant", "wechat_mp", locked=False, now="2026-08-09T00:08:00+00:00", projects_root=root)
    acknowledged = variants.acknowledge_master_update(
        "prj_variant", "wechat_mp", now="2026-08-09T00:09:00+00:00", projects_root=root,
    )
    assert acknowledged.version == edited.version + 1
    assert acknowledged.source_master_version == 2
    assert acknowledged.body == edited.body and acknowledged.title == edited.title
    assert acknowledged.manually_modified and not acknowledged.upstream_updated and not acknowledged.locked
    assert acknowledged.history[-1].reason == "acknowledge-master:1->2"
    assert variants.acknowledge_master_update(
        "prj_variant", "wechat_mp", now="2026-08-09T00:10:00+00:00", projects_root=root,
    ) == acknowledged


def test_rejects_unknown_fields_platform_and_bad_asset_reference(tmp_path):
    root = tmp_path / "projects"; _project(root)
    with pytest.raises(variants.VariantsError, match="platform"):
        variants.create_from_master("prj_variant", "xiaohongshu", now="2026-08-09T00:02:00+00:00", projects_root=root)
    variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    with pytest.raises(variants.VariantsError, match="unknown or unselected"):
        variants.save_manual("prj_variant", "wechat_mp", title="标题", summary="摘要", body="正文", asset_ids=["vas_missing"], now="2026-08-09T00:03:00+00:00", projects_root=root)
    path = root / "prj_variant" / "variants.json"; raw = json.loads(path.read_text()); raw["extra"] = 1; path.write_text(json.dumps(raw))
    with pytest.raises(variants.VariantsError, match="unknown"):
        variants.load_variants("prj_variant", projects_root=root)


def test_replacing_selected_visual_keeps_old_variant_readable_until_explicit_update(tmp_path):
    root = tmp_path / "projects"; _project(root)
    visuals.save_plan(
        "prj_variant", bible={}, slots=[{
            "id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None,
            "direction": "编辑插画", "aspect_ratio": "16:9",
        }], projects_root=root,
    )
    old = visuals.record_asset(
        "prj_variant", slot_id="vsl_cover", prompt="旧封面", model="test",
        size="16:9", cost_usd=0, now="2026-08-09T00:02:00+00:00",
        file_path="assets/vas_old.png", status="candidate", asset_id="vas_old",
        projects_root=root,
    )
    visuals.select_asset("prj_variant", old.id, reason="旧图", rating=4, projects_root=root)
    created = variants.create_from_master(
        "prj_variant", "wechat_mp", now="2026-08-09T00:03:00+00:00", projects_root=root,
    )
    replacement = visuals.record_asset(
        "prj_variant", slot_id="vsl_cover", prompt="新封面", model="test",
        size="16:9", cost_usd=0, now="2026-08-09T00:04:00+00:00",
        file_path="assets/vas_new.png", status="candidate", asset_id="vas_new",
        projects_root=root,
    )
    visuals.select_asset("prj_variant", replacement.id, reason="新图", rating=5, projects_root=root)

    readable = variants.load_variants("prj_variant", projects_root=root).variants[0]
    assert readable.asset_ids == ("vas_old",)
    updated = variants.save_manual(
        "prj_variant", "wechat_mp", title=created.title, summary=created.summary,
        body=created.body, asset_ids=[replacement.id],
        now="2026-08-09T00:05:00+00:00", projects_root=root,
    )
    assert updated.asset_ids == ("vas_new",)
    assert updated.history[-1].asset_ids == ("vas_old",)


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["variants"][0]["history"].extend([dict(raw["variants"][0]["history"][0]), dict(raw["variants"][0]["history"][0])]),
    lambda raw: raw["variants"][0]["history"].reverse(),
    lambda raw: raw["variants"][0]["history"][0].update(asset_ids=["vas_missing"]),
])
def test_loaded_history_is_strict_and_revalidates_assets(tmp_path, mutate):
    root = tmp_path / "projects"; _project(root)
    variants.create_from_master("prj_variant", "wechat_mp", now="2026-08-09T00:02:00+00:00", projects_root=root)
    variants.save_manual("prj_variant", "wechat_mp", title="人工", summary="摘要", body="正文", asset_ids=[], now="2026-08-09T00:03:00+00:00", projects_root=root)
    variants.save_manual("prj_variant", "wechat_mp", title="人工二", summary="摘要二", body="正文二", asset_ids=[], now="2026-08-09T00:04:00+00:00", projects_root=root)
    path = root / "prj_variant" / "variants.json"; raw = json.loads(path.read_text()); mutate(raw); path.write_text(json.dumps(raw))
    with pytest.raises(variants.VariantsError, match="history|asset"):
        variants.load_variants("prj_variant", projects_root=root)
