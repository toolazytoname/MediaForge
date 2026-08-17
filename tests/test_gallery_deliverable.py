from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import approvals, db, deliverables, projects, visuals
from pipeline.delivery.service import DeliveryError, create_draft, create_export_delivery, preview_deliverable
from pipeline.project_exports import read_gallery_export
from pipeline.publishers.base import AccountConfig, PostBundle, PublishError, PublishResult, PublisherAdapter
from pipeline.publishers.capability_registry import (
    DEFAULT_GALLERY_MAX_IMAGES,
    DEFAULT_GALLERY_MIN_IMAGES,
    effective_delivery,
    gallery_image_limits,
    get_capability,
)


_NOW = "2026-08-17T00:00:00+00:00"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _XhsUnknown(PublisherAdapter):
    platform = "xiaohongshu"

    def validate(self, bundle: PostBundle) -> list[str]:
        return []

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        raise PublishError("CLI exit=0 but status is 'unknown' (no protocol success receipt)")


def _selected_assets(root: Path, project_id: str, count: int, prefix: str) -> list[str]:
    slots = [
        {
            "id": f"vsl_{prefix}_{index}",
            "purpose": "封面" if index == 0 else f"组图{index}",
            "paragraph_anchor": None,
            "direction": "克制",
            "aspect_ratio": "1:1",
        }
        for index in range(count)
    ]
    visuals.save_plan(project_id, bible={"style": "plain"}, slots=slots, projects_root=root)
    asset_ids = []
    for index, slot in enumerate(slots):
        asset_id = f"vas_{prefix}_{index}"
        png = root / project_id / "assets" / f"{asset_id}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(_PNG)
        visuals.record_asset(
            project_id, slot_id=slot["id"], prompt=f"prompt-{prefix}-{index}",
            model="fixture", size="1:1", cost_usd=0.12, now=_NOW,
            file_path=f"assets/{asset_id}.png", status="candidate",
            asset_id=asset_id, projects_root=root,
        )
        visuals.select_asset(project_id, asset_id, reason="选用", rating=4, projects_root=root)
        asset_ids.append(asset_id)
    return asset_ids


def _make_gallery(
    root: Path, project_id: str, *, slides: int, prefix: str, caption: str,
    targets: list[str] | None = None,
) -> deliverables.Deliverable:
    projects.create_project(
        title=f"组图{prefix}", idea="组图", audience="读者", goal="导出", voice="清晰",
        autonomy="collaborate", now=_NOW, project_id=project_id, projects_root=root,
    )
    asset_ids = _selected_assets(root, project_id, slides, prefix)
    item = deliverables.create_gallery(
        project_id,
        title=f"夹具{prefix}",
        caption=caption,
        tags=["fixture", prefix],
        cover_asset_id=asset_ids[0],
        slides=[
            {"asset_id": asset_id, "order": index, "alt": f"第{index + 1}张", "crop": {"x": 0, "y": 0, "w": 1, "h": 1}}
            for index, asset_id in enumerate(asset_ids)
        ],
        targets=targets or ["xiaohongshu"],
        now=_NOW,
        projects_root=root,
    )
    return deliverables.set_gallery_locked(
        project_id, item.id, locked=True, now="2026-08-17T00:01:00+00:00", projects_root=root,
    )


def _approve_gallery(root: Path, project_id: str, item: deliverables.Deliverable) -> None:
    state = approvals.recheck(project_id, actor="lazy", now="2026-08-17T00:02:00+00:00", projects_root=root)
    assert state.ready, state.blockers
    assert "master" not in {check.id for check in state.approval.checks}
    for check in state.approval.checks:
        approvals.decide(
            project_id, check.id, approved=True, note="组图检查通过",
            actor="lazy", now="2026-08-17T00:03:00+00:00", projects_root=root,
        )
    assert approvals.status(project_id, projects_root=root).complete


def test_three_gallery_fixtures_approve_and_export(tmp_path):
    root = tmp_path / "projects"
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    fixtures = (
        ("one", 1, "一张封面也要能导出"),
        ("three", 3, "三张连续幻灯片"),
        ("six", 6, "六张带裁切的组图"),
    )
    for prefix, count, caption in fixtures:
        project_id = f"prj_gal_{prefix}"
        item = _make_gallery(root, project_id, slides=count, prefix=prefix, caption=caption)
        _approve_gallery(root, project_id, item)
        preview = preview_deliverable(
            conn, project_id=project_id, deliverable_id=item.id, actor="lazy", projects_root=root,
        )
        assert preview.attempt.mode == "preview"
        assert preview.attempt.platform_post_id is None
        result = create_export_delivery(
            conn, project_id=project_id, deliverable_id=item.id, actor="lazy", projects_root=root,
        )
        assert result.attempt.mode == "export"
        assert result.attempt.outcome == "success"
        assert result.attempt.publication_id is None
        assert result.attempt.platform_post_id is None
        assert result.attempt.platform_url is None
        receipt = json.loads(result.attempt.raw_receipt or "{}")
        assert receipt["platform_post_id"] is None
        archive = root / project_id / result.export.path
        restored = read_gallery_export(archive)
        assert restored["payload"].caption == caption
        assert restored["payload"].cover_asset_id == item.payload["cover_asset_id"]
        assert [slide.order for slide in restored["payload"].slides] == list(range(count))
        assert restored["platform_post_id"] is None
        assert conn.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"] == 0


def test_unapproved_gallery_export_is_409(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_gal_deny", slides=2, prefix="deny", caption="未批准不得导出")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    with pytest.raises(DeliveryError) as error:
        create_export_delivery(
            conn, project_id="prj_gal_deny", deliverable_id=item.id, actor="lazy", projects_root=root,
        )
    assert error.value.http_status == 409
    assert error.value.code == "not_approved"


def test_gallery_rejects_unselected_and_noncontiguous_slides(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(
        title="坏组图", idea="组图", audience="读者", goal="导出", voice="清晰",
        autonomy="collaborate", now=_NOW, project_id="prj_gal_bad", projects_root=root,
    )
    assets = _selected_assets(root, "prj_gal_bad", 2, "bad")
    with pytest.raises(deliverables.DeliverablesError, match="contiguous"):
        deliverables.create_gallery(
            "prj_gal_bad", title="坏顺序", caption="顺序必须从 0 连续",
            cover_asset_id=assets[0],
            slides=[
                {"asset_id": assets[0], "order": 0, "alt": "一"},
                {"asset_id": assets[1], "order": 2, "alt": "二"},
            ],
            now=_NOW, projects_root=root,
        )
    with pytest.raises(deliverables.DeliverablesError, match="selected"):
        deliverables.create_gallery(
            "prj_gal_bad", title="坏引用", caption="只能引用 selected",
            cover_asset_id="vas_missing_xxx",
            slides=[{"asset_id": "vas_missing_xxx", "order": 0, "alt": "无"}],
            now=_NOW, projects_root=root,
        )


def test_gallery_draft_and_direct_are_forbidden(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_gal_mode", slides=1, prefix="mode", caption="禁止直发")
    _approve_gallery(root, "prj_gal_mode", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    with pytest.raises(DeliveryError) as error:
        create_draft(
            conn, project_id="prj_gal_mode", deliverable_id=item.id, actor="lazy",
            adapter=_XhsUnknown(), account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
            publish_config=None, projects_root=root,
        )
    assert error.value.code == "mode_not_allowed"
    flags = effective_delivery("xiaohongshu")
    assert flags.export is True
    assert flags.preview is True
    assert flags.draft is False
    assert flags.direct is False


def test_xiaohongshu_unknown_receipt_is_not_success():
    cap = get_capability("xiaohongshu")
    assert cap.formats == ("gallery",)
    assert cap.limits.min_images == DEFAULT_GALLERY_MIN_IMAGES
    assert cap.limits.max_images == DEFAULT_GALLERY_MAX_IMAGES
    assert gallery_image_limits("xiaohongshu") == (1, 9)
    assert cap.receipts.unknown_is_failure is True
    with pytest.raises(PublishError, match="unknown"):
        _XhsUnknown().publish(
            PostBundle(content_id="c_xhs", title="t", body_path=Path("caption.md"), media_paths=(), tags=()),
            AccountConfig(id="main", credentials_path=Path("/missing.json")),
        )
