from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import article_feedback, master_documents, projects


NOW = "2026-08-12T10:00:00+00:00"


def _project(root: Path) -> str:
    project = projects.create_project(title="项目", idea="想法", audience="读者", goal="目标", voice="克制", autonomy="collaborate", now=NOW, project_id="prj_feedback", projects_root=root)
    master_documents.save_manual(project.id, title="原题", body="原文保持不变。", now=NOW, projects_root=root)
    return project.id


def test_proposal_keeps_author_feedback_and_never_changes_master(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    before = master_documents.load_master(project_id, projects_root=tmp_path)
    assert before is not None

    proposal = article_feedback.create_proposal(
        project_id, feedback="减少说教感，保留真实失败", target="更真诚", readership="普通上班族", platform="公众号", values="不制造焦虑",
        proposed_title="更真诚的标题", proposed_body="建议稿", now=NOW, projects_root=tmp_path, proposal_id="afp_one",
    )

    assert proposal.status == "ready"
    assert article_feedback.proposal_state(proposal, before) == "current"
    assert proposal.feedback == "减少说教感，保留真实失败"
    assert proposal.base_version == before.version
    assert master_documents.load_master(project_id, projects_root=tmp_path) == before
    assert article_feedback.load_proposals(project_id, projects_root=tmp_path) == (proposal,)


def test_proposal_becomes_obsolete_after_manual_master_change(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    article_feedback.create_proposal(project_id, feedback="更克制", target=None, readership=None, platform=None, values=None,
        proposed_title="建议标题", proposed_body="建议正文", now=NOW, projects_root=tmp_path, proposal_id="afp_stale")
    master_documents.save_manual(project_id, title="人工更新", body="人工更新的正文", now="2026-08-12T11:00:00+00:00", projects_root=tmp_path)

    proposal = article_feedback.load_proposals(project_id, projects_root=tmp_path)[0]
    assert proposal.status == "ready"
    current = master_documents.load_master(project_id, projects_root=tmp_path)
    assert current is not None
    assert article_feedback.proposal_state(proposal, current) == "obsolete"


def test_failed_feedback_is_persisted_and_can_be_retried_without_losing_input(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    failed = article_feedback.create_failed_proposal(project_id, feedback="保留失败", target="目标", readership=None, platform=None, values=None,
        error="provider down", now=NOW, projects_root=tmp_path, proposal_id="afp_retry")
    assert failed.status == "failed"
    ready = article_feedback.complete_failed_proposal(project_id, failed.id, proposed_title="重试标题", proposed_body="重试正文",
        now="2026-08-12T11:00:00+00:00", projects_root=tmp_path)
    assert ready.status == "ready"
    assert ready.feedback == "保留失败"
    assert ready.error is None


def test_feedback_manifest_is_strict_and_ids_are_path_safe(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    with pytest.raises(article_feedback.ArticleFeedbackError):
        article_feedback.create_proposal(project_id, feedback="意见", target=None, readership=None, platform=None, values=None,
            proposed_title="标题", proposed_body="正文", now=NOW, projects_root=tmp_path, proposal_id="../bad")
