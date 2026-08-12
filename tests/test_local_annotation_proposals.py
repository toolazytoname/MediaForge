from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import article_feedback, local_annotations, master_documents, projects

NOW = "2026-08-12T10:00:00+00:00"


def _project(root: Path) -> str:
    p = projects.create_project(title="项目", idea="想法", audience="读者", goal="目标", voice="克制", autonomy="collaborate", now=NOW, project_id="prj_localproposal", projects_root=root)
    master_documents.save_manual(p.id, title="标题", body="第一段需要更具体。\n\n第二段保持不动。", now=NOW, projects_root=root)
    return p.id


def test_local_text_proposal_snapshots_annotation_and_never_mutates_master(tmp_path: Path):
    project_id = _project(tmp_path)
    annotation = local_annotations.create_text_annotation(project_id, excerpt="需要更具体", feedback="举一个真实动作", categories=("text",), now=NOW, projects_root=tmp_path, annotation_id="lan_local")
    before = master_documents.load_master(project_id, projects_root=tmp_path)

    proposal = article_feedback.create_local_proposal(project_id, annotation_id=annotation.id, proposed_title="标题", proposed_body="第一段加入一个今天能做的动作。\n\n第二段保持不动。", now="2026-08-12T10:01:00+00:00", projects_root=tmp_path, proposal_id="afp_local")

    assert proposal.scope == "local_text" and proposal.annotation_id == annotation.id
    assert proposal.annotation_excerpt == "需要更具体" and proposal.annotation_categories == ("text",)
    assert proposal.annotation_resolved_version == before.version
    assert proposal.annotation_resolved_hash == article_feedback.master_hash(before)
    assert master_documents.load_master(project_id, projects_root=tmp_path) == before


def test_local_proposal_refuses_orphaned_or_changed_annotation(tmp_path: Path):
    project_id = _project(tmp_path)
    annotation = local_annotations.create_text_annotation(project_id, excerpt="需要更具体", feedback="举例", categories=(), now=NOW, projects_root=tmp_path, annotation_id="lan_stale")
    master_documents.save_manual(project_id, title="标题", body="第一段已经删除。", now="2026-08-12T10:02:00+00:00", projects_root=tmp_path)
    with pytest.raises(article_feedback.ArticleFeedbackError, match="annotation is orphaned"):
        article_feedback.create_local_proposal(project_id, annotation_id=annotation.id, proposed_title="标题", proposed_body="建议", now="2026-08-12T10:03:00+00:00", projects_root=tmp_path)


def test_local_proposal_accept_uses_same_version_and_hash_protection(tmp_path: Path):
    project_id = _project(tmp_path)
    annotation = local_annotations.create_text_annotation(project_id, excerpt="需要更具体", feedback="举例", categories=(), now=NOW, projects_root=tmp_path, annotation_id="lan_accept")
    proposal = article_feedback.create_local_proposal(project_id, annotation_id=annotation.id, proposed_title="标题", proposed_body="第一段更具体。\n\n第二段保持不动。", now="2026-08-12T10:01:00+00:00", projects_root=tmp_path, proposal_id="afp_acceptlocal")
    master_documents.save_manual(project_id, title="人工改过", body="人工正文", now="2026-08-12T10:02:00+00:00", projects_root=tmp_path)
    with pytest.raises(article_feedback.ArticleFeedbackError, match="obsolete"):
        article_feedback.accept_proposal(project_id, proposal.id, title="标题", body="不应写入", now="2026-08-12T10:03:00+00:00", projects_root=tmp_path)
