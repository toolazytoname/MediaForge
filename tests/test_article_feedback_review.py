from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import article_feedback, master_documents, projects


NOW = "2026-08-12T10:00:00+00:00"


def _project(root: Path) -> str:
    project = projects.create_project(
        title="项目", idea="想法", audience="读者", goal="目标", voice="克制",
        autonomy="collaborate", now=NOW, project_id="prj_review", projects_root=root,
    )
    master_documents.save_manual(project.id, title="原题", body="第一段：原文。\n\n第二段：保持。", now=NOW, projects_root=root)
    return project.id


def _proposal(project_id: str, root: Path, proposal_id: str = "afp_review") -> article_feedback.ArticleFeedbackProposal:
    return article_feedback.create_proposal(
        project_id, feedback="减少说教感", target=None, readership=None, platform=None, values=None,
        proposed_title="建议标题", proposed_body="第一段：更具体的建议。\n\n第二段：保持。",
        now=NOW, projects_root=root, proposal_id=proposal_id,
    )


def test_accept_current_proposal_writes_a_new_master_version_and_audits_decision(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    proposal = _proposal(project_id, tmp_path)

    updated = article_feedback.accept_proposal(
        project_id, proposal.id, title="作者微调后的标题", body="第一段：作者微调后的建议。\n\n第二段：保持。",
        now="2026-08-12T11:00:00+00:00", projects_root=tmp_path,
    )

    master = master_documents.load_master(project_id, projects_root=tmp_path)
    assert master is not None and master.version == 2
    assert master.title == "作者微调后的标题"
    assert master.history[-1].reason == f"feedback:{proposal.id}"
    assert updated.status == "accepted"
    assert updated.decision == "accepted"
    assert updated.accepted_title == master.title
    assert updated.accepted_body == master.body
    assert updated.decided_at == "2026-08-12T11:00:00+00:00"


def test_accept_refuses_an_obsolete_proposal_by_version_and_hash(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    proposal = _proposal(project_id, tmp_path)
    master_documents.save_manual(project_id, title="人工更新", body="人工更新正文", now="2026-08-12T10:30:00+00:00", projects_root=tmp_path)

    with pytest.raises(article_feedback.ArticleFeedbackError, match="obsolete"):
        article_feedback.accept_proposal(project_id, proposal.id, title="建议标题", body="建议正文", now="2026-08-12T11:00:00+00:00", projects_root=tmp_path)

    current = master_documents.load_master(project_id, projects_root=tmp_path)
    saved = article_feedback.load_proposals(project_id, projects_root=tmp_path)[0]
    assert current is not None and current.version == 2 and current.body == "人工更新正文"
    assert saved.status == "ready" and saved.decision is None


def test_reject_only_records_a_decision_and_never_changes_master(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    proposal = _proposal(project_id, tmp_path)
    before = master_documents.load_master(project_id, projects_root=tmp_path)

    rejected = article_feedback.reject_proposal(project_id, proposal.id, now="2026-08-12T11:00:00+00:00", projects_root=tmp_path)

    assert rejected.status == "rejected" and rejected.decision == "rejected"
    assert rejected.accepted_title is None and rejected.accepted_body is None
    assert master_documents.load_master(project_id, projects_root=tmp_path) == before
    with pytest.raises(article_feedback.ArticleFeedbackError, match="already rejected"):
        article_feedback.reject_proposal(project_id, proposal.id, now="2026-08-12T12:00:00+00:00", projects_root=tmp_path)


def test_old_ready_manifest_without_decision_fields_loads_safely(tmp_path: Path) -> None:
    project_id = _project(tmp_path)
    proposal = _proposal(project_id, tmp_path)
    path = tmp_path / project_id / "article_feedback.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("decision", "decided_at", "accepted_title", "accepted_body"):
        del payload[0][key]
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = article_feedback.load_proposals(project_id, projects_root=tmp_path)
    assert loaded[0].id == proposal.id and loaded[0].status == "ready" and loaded[0].decision is None


def test_interrupted_final_audit_is_recovered_from_immutable_master_history(tmp_path: Path, monkeypatch) -> None:
    project_id = _project(tmp_path); proposal = _proposal(project_id, tmp_path)
    original_write = article_feedback._write; calls = 0
    def fail_final_write(pid, items, root):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("disk interrupted after master")
        original_write(pid, items, root)
    monkeypatch.setattr(article_feedback, "_write", fail_final_write)
    with pytest.raises(OSError):
        article_feedback.accept_proposal(project_id, proposal.id, title="建议标题", body="建议正文", now="2026-08-12T11:00:00+00:00", projects_root=tmp_path)
    # The master commit exists, and the durable intent is brought to a final
    # audited accepted state by a later read.
    monkeypatch.setattr(article_feedback, "_write", original_write)
    assert article_feedback.recover_acceptances(project_id, projects_root=tmp_path)[0].status == "accepted"
