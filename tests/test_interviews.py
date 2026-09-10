from __future__ import annotations

import pytest

from pipeline.interviews import (
    InterviewError,
    confirm_interview,
    load_interview,
    require_confirmed_interview,
    save_interview,
)
from pipeline.projects import create_project
from pipeline.webui.api.master_documents import _draft_prompt


NOW = "2026-09-10T08:00:00+00:00"
LATER = "2026-09-10T09:00:00+00:00"


def _project(root):
    return create_project(
        title="主题", idea="一个想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now=NOW, project_id="prj_interview1",
        projects_root=root,
    )


def test_unconfirmed_interview_blocks_draft_prompt(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(InterviewError, match="not confirmed"):
        require_confirmed_interview(project.id, projects_root=tmp_path)
    save_interview(
        project.id,
        viewpoint="工程绿不等于能用",
        motive="把验证顺序改对人能署名",
        experience="我在仓库里跑过全量测试",
        sources=(),
        now=NOW,
        projects_root=tmp_path,
    )
    with pytest.raises(InterviewError, match="not confirmed"):
        require_confirmed_interview(project.id, projects_root=tmp_path)


def test_confirmed_interview_enters_draft_prompt(tmp_path, monkeypatch):
    project = _project(tmp_path)
    save_interview(
        project.id,
        viewpoint="工程绿不等于能用",
        motive="把验证顺序改对人能署名",
        experience="我在仓库里跑过全量测试",
        sources=({"kind": "excerpt", "title": "笔记", "reference": "notes.md", "excerpt": "摘录"},),
        now=NOW,
        projects_root=tmp_path,
    )
    confirm_interview(project.id, now=LATER, projects_root=tmp_path)
    prompt = _draft_prompt(project.id, projects_root=tmp_path)
    assert "工程绿不等于能用" in prompt
    assert "我在仓库里跑过全量测试" in prompt
    assert load_interview(project.id, projects_root=tmp_path).confirmed is True


def test_book_title_without_excerpt_is_unread(tmp_path):
    project = _project(tmp_path)
    draft = save_interview(
        project.id,
        viewpoint="观点",
        motive="动机",
        experience="暂无，待作者补",
        sources=({"kind": "book", "title": "深度工作", "reference": "book:deep-work", "excerpt": ""},),
        now=NOW,
        projects_root=tmp_path,
    )
    assert draft.unread_books == ("深度工作",)
    confirm_interview(project.id, now=LATER, projects_root=tmp_path)
    prompt = _draft_prompt(project.id, projects_root=tmp_path)
    assert "未读原书" in prompt
    assert "深度工作" in prompt
    assert "不得假装读过" in prompt
