from __future__ import annotations

import json

import pytest

from pipeline.projects import create_project
from pipeline.research import (
    ResearchManifestError,
    add_source,
    add_claim,
    load_research,
    update_claim,
    update_source,
)


NOW = "2026-08-09T10:00:00+00:00"
LATER = "2026-08-09T11:00:00+00:00"


def _project(tmp_path, project_id: str = "prj_research"):
    return create_project(
        title="研究项目", idea="一条真实观察", audience="独立创作者",
        goal="完成一篇主稿", voice="清晰", autonomy="collaborate",
        now=NOW, project_id=project_id, projects_root=tmp_path,
    )


def test_research_round_trip_keeps_immutable_records_and_references(tmp_path):
    project = _project(tmp_path)
    source = add_source(
        project.id, title="一手研究", reference="https://example.com/report",
        summary="样本与方法说明。", now=NOW, projects_root=tmp_path,
    )
    claim = add_claim(
        project.id, text="这一事实还需要复核。", kind="fact", source_ids=(source.id,),
        status="unverified", limitation="样本并不覆盖所有创作者。", now=LATER,
        projects_root=tmp_path,
    )

    board = load_research(project.id, projects_root=tmp_path)
    assert board.sources == (source,)
    assert board.claims == (claim,)
    assert source.entered_at == NOW
    assert claim.counterpoint is None
    assert not (tmp_path / project.id / "research.json.tmp").exists()


def test_update_replaces_record_without_mutating_the_original(tmp_path):
    project = _project(tmp_path)
    original_source = add_source(
        project.id, title="初始资料", reference="notes/interview.md", summary="访谈摘录",
        now=NOW, projects_root=tmp_path,
    )
    updated_source = update_source(
        project.id, original_source.id, title="修订资料", reference="notes/interview.md",
        summary="补充访谈摘录", now=LATER, projects_root=tmp_path,
    )
    original_claim = add_claim(
        project.id, text="我的判断", kind="judgment", source_ids=(), status="unverified",
        now=NOW, projects_root=tmp_path,
    )
    updated_claim = update_claim(
        project.id, original_claim.id, text="修订后的判断", kind="judgment", source_ids=(),
        status="verified", counterpoint="也可能只适用于早期阶段。", now=LATER,
        projects_root=tmp_path,
    )

    assert original_source.title == "初始资料"
    assert original_claim.text == "我的判断"
    board = load_research(project.id, projects_root=tmp_path)
    assert board.sources == (updated_source,)
    assert board.claims == (updated_claim,)


@pytest.mark.parametrize("payload", ["{", "[]"])
def test_research_rejects_bad_json_shape(tmp_path, payload):
    project = _project(tmp_path)
    path = tmp_path / project.id / "research.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ResearchManifestError):
        load_research(project.id, projects_root=tmp_path)


def test_research_rejects_unknown_fields_and_unknown_source_references(tmp_path):
    project = _project(tmp_path)
    source = add_source(
        project.id, title="资料", reference="local:notes.md", summary="摘要", now=NOW,
        projects_root=tmp_path,
    )
    path = tmp_path / project.id / "research.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResearchManifestError, match="missing or unknown"):
        load_research(project.id, projects_root=tmp_path)
    payload.pop("unknown")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResearchManifestError, match="unknown source"):
        add_claim(project.id, text="无效引用", kind="fact", source_ids=(source.id, "src_missing"),
                  status="unverified", now=LATER, projects_root=tmp_path)


def test_research_requires_project_and_rejects_invalid_claim_type_or_status(tmp_path):
    with pytest.raises(ResearchManifestError, match="project not found"):
        load_research("prj_missing", projects_root=tmp_path)
    project = _project(tmp_path)
    with pytest.raises(ResearchManifestError, match="claim kind"):
        add_claim(project.id, text="错误", kind="opinion", source_ids=(), status="unverified",
                  now=NOW, projects_root=tmp_path)
    with pytest.raises(ResearchManifestError, match="open_question"):
        add_claim(project.id, text="问题", kind="open_question", source_ids=(), status="verified",
                  now=NOW, projects_root=tmp_path)
    with pytest.raises(ResearchManifestError, match="verified fact"):
        add_claim(project.id, text="不能伪装为已核查", kind="fact", source_ids=(), status="verified",
                  now=NOW, projects_root=tmp_path)
