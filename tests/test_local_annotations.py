from pathlib import Path

import pytest

from pipeline import local_annotations, master_documents, projects, visuals


NOW = "2026-08-12T10:00:00+00:00"


def _project_with_article(root: Path, body: str = "第一段：AI 不是魔法。\n\n第二段：人仍然要做判断。") -> str:
    project = projects.create_project(
        title="一篇文章", idea="真实想法", audience="读者", goal="写作", voice="克制", autonomy="collaborate",
        now=NOW, project_id="prj_annotations", projects_root=root,
    )
    master_documents.save_manual(project.id, title="AI 与判断", body=body, now=NOW, projects_root=root)
    return project.id


def test_text_annotation_keeps_excerpt_context_and_structural_anchor_not_offset(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path)

    annotation = local_annotations.create_text_annotation(
        project_id, excerpt="人仍然要做判断", feedback="把这句写得更有力量", categories=("fact",), now=NOW,
        projects_root=tmp_path,
    )

    assert annotation.excerpt == "人仍然要做判断"
    assert annotation.structural_anchor.startswith("body:")
    assert annotation.context_before and annotation.context_after is not None
    assert annotation.status == "active"
    assert annotation.asset_id is None


def test_text_annotation_reanchors_only_when_exact_target_is_unique(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path)
    annotation = local_annotations.create_text_annotation(
        project_id, excerpt="人仍然要做判断", feedback="少一点说教", categories=("style",), now=NOW,
        projects_root=tmp_path,
    )
    master_documents.save_manual(project_id, title="AI 与判断", body="新增开头。\n\n第二段：人仍然要做判断。", now="2026-08-12T10:01:00+00:00", projects_root=tmp_path)

    resolved = local_annotations.resolve_annotation(project_id, annotation.id, now="2026-08-12T10:02:00+00:00", projects_root=tmp_path)

    assert resolved.status == "active"
    assert resolved.resolved_version == 2
    assert resolved.resolved_hash == local_annotations.master_hash(master_documents.load_master(project_id, projects_root=tmp_path))


def test_changed_text_with_ambiguous_or_missing_match_becomes_orphaned_never_moves_silently(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path)
    annotation = local_annotations.create_text_annotation(
        project_id, excerpt="人仍然要做判断", feedback="改写", categories=(), now=NOW, projects_root=tmp_path,
    )
    master_documents.save_manual(project_id, title="AI 与判断", body="人仍然要做判断。\n\n人仍然要做判断。", now="2026-08-12T10:01:00+00:00", projects_root=tmp_path)

    orphaned = local_annotations.resolve_annotation(project_id, annotation.id, now="2026-08-12T10:02:00+00:00", projects_root=tmp_path)

    assert orphaned.status == "orphaned"
    assert orphaned.resolved_version == 1


def test_image_annotation_binds_asset_and_its_contextual_paragraph(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path)
    plan = visuals.save_plan(project_id, bible={}, slots=[{"id": "vsl_cover", "purpose": "cover", "paragraph_anchor": "第一段：AI 不是魔法。", "direction": "真实", "aspect_ratio": "16:9"}], projects_root=tmp_path)
    asset = visuals.record_asset(project_id, slot_id=plan.slots[0].id, prompt="封面", model="test", size="1024x1024", cost_usd=0,
                                 file_path="assets/vas_image.png", status="candidate", failure=None, now=NOW, projects_root=tmp_path,
                                 asset_id="vas_image")
    asset = visuals.select_asset(project_id, asset.id, reason="文章封面", rating=None, projects_root=tmp_path)

    annotation = local_annotations.create_image_annotation(
        project_id, asset_id=asset.id, feedback="主体换成在桌边思考的人", categories=("composition", "subject"), now=NOW,
        projects_root=tmp_path,
    )

    assert annotation.kind == "image"
    assert annotation.asset_id == asset.id
    assert annotation.paragraph_anchor == "第一段：AI 不是魔法。"


def test_strict_manifest_rejects_unknown_fields_and_removal_is_atomic(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path)
    annotation = local_annotations.create_text_annotation(project_id, excerpt="AI 不是魔法", feedback="更具体", categories=(), now=NOW, projects_root=tmp_path)
    path = tmp_path / project_id / "local_annotations.json"
    path.write_text('[{"unknown": true}]', encoding="utf-8")
    with pytest.raises(local_annotations.LocalAnnotationError):
        local_annotations.load_annotations(project_id, projects_root=tmp_path)
    # Restore a known valid manifest and prove remove leaves no half-written data.
    local_annotations._write(project_id, (annotation,), tmp_path)
    local_annotations.remove_annotation(project_id, annotation.id, projects_root=tmp_path)
    assert local_annotations.load_annotations(project_id, projects_root=tmp_path) == ()


def test_text_annotation_rejects_excerpt_that_is_ambiguous_between_title_and_body(tmp_path: Path) -> None:
    project_id = _project_with_article(tmp_path, "判断需要依据。")
    master_documents.save_manual(project_id, title="判断", body="判断需要依据。", now="2026-08-12T10:01:00+00:00", projects_root=tmp_path)
    with pytest.raises(local_annotations.LocalAnnotationError, match="exactly once"):
        local_annotations.create_text_annotation(project_id, excerpt="判断", feedback="意见", categories=(), now=NOW, projects_root=tmp_path)
