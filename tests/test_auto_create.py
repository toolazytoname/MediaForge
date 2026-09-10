"""AUTO-01: real pack create pipeline, not theme-loop placeholder."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline import research
from pipeline.auto_create import AutoCreateError, run_auto_create
from pipeline.delivery import service as delivery_service
from pipeline.interviews import confirm_interview, save_interview
from pipeline.master_documents import load_master
from pipeline.pack import prepare_pack
from pipeline.projects import create_project, load_project, update_project
from tests.test_autonomy_policy import _project
from tests.test_delivery_kernel import _complete, _ready

NOW = "2026-08-09T00:02:00+00:00"


def _interview(project_id, root, *, book=False):
    sources = [{"kind": "url", "title": "公开报道", "reference": "https://example.com/a", "excerpt": "摘录"}]
    if book:
        sources.append({"kind": "book", "title": "未读书", "reference": "isbn:1", "excerpt": ""})
    save_interview(
        project_id, viewpoint="我的判断是边界比速度重要", motive="想写给同行看",
        experience="我在一次延期里改过排期", sources=sources, now=NOW, projects_root=root,
    )
    confirm_interview(project_id, now=NOW, projects_root=root)


def _source(project_id, root):
    research.add_source(
        project_id, title="公开报道", reference="https://example.com/a",
        summary="只核实过的公开事实", now=NOW, projects_root=root,
    )


def _draft_fn(project, interview, board, critique=None):
    body = (
        f"{interview.viewpoint}\n\n{interview.motive}\n\n{interview.experience}\n\n"
        f"来源：{board.sources[0].title} {board.sources[0].summary}\n\n"
        "这不是主题循环粘贴，而是根据访谈和来源写成的候选主稿。" * 20
    )
    if critique:
        body += f"\n修订：{critique}"
    return project.title + "·有依据", body


def test_prepare_pack_rejects_missing_interview_and_sources(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packmiss")
    with pytest.raises(AutoCreateError, match="interview"):
        prepare_pack("prj_packmiss", now=NOW, projects_root=root, draft_fn=_draft_fn)
    _interview("prj_packmiss", root)
    with pytest.raises(AutoCreateError, match="来源"):
        prepare_pack("prj_packmiss", now=NOW, projects_root=root, draft_fn=_draft_fn)


def test_prepare_pack_body_is_not_theme_loop(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packreal")
    _interview("prj_packreal", root)
    _source("prj_packreal", root)
    spy = MagicMock(wraps=delivery_service.safe_publish)
    monkeypatch.setattr(delivery_service, "safe_publish", spy)
    result = prepare_pack(
        "prj_packreal", now=NOW, projects_root=root, draft_fn=_draft_fn,
    )
    master = load_master("prj_packreal", projects_root=root)
    assert master is not None
    assert "这是自动内容包生成的主稿候选" not in master.body
    assert master.body.count("想法足够支撑一篇候选主稿") < 2
    assert "我的判断是边界比速度重要" in master.body
    assert result.created_master is True
    assert result.revision_count == 0
    assert result.terminal_status in {"drafting", "ready_for_approval"}
    spy.assert_not_called()


def test_gate_failure_pauses_after_two_revisions(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packgate")
    _interview("prj_packgate", root)
    _source("prj_packgate", root)
    drafts = {"n": 0}

    def flaky(project, interview, board, critique=None):
        drafts["n"] += 1
        return _draft_fn(project, interview, board, critique)

    with pytest.raises(AutoCreateError, match="质量"):
        run_auto_create(
            "prj_packgate", now=NOW, projects_root=root,
            draft_fn=flaky, score_fn=lambda title, body: ("fail", 3.0),
        )
    assert drafts["n"] == 3  # 初稿 + 两轮修订
    assert load_master("prj_packgate", projects_root=root) is None


def test_book_without_excerpt_is_marked_unread(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_packbook")
    _interview("prj_packbook", root, book=True)
    _source("prj_packbook", root)
    seen = {}

    def capture(project, interview, board, critique=None):
        seen["unread"] = interview.unread_books
        return _draft_fn(project, interview, board, critique)

    run_auto_create("prj_packbook", now=NOW, projects_root=root, draft_fn=capture)
    assert "未读书" in seen["unread"]
