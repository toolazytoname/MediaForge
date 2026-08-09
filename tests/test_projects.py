from __future__ import annotations

import json

import pytest

from pipeline.projects import (
    ProjectManifestError,
    create_project,
    list_projects,
    load_project,
    update_project,
)


NOW = "2026-08-09T10:00:00+00:00"
LATER = "2026-08-09T11:00:00+00:00"


def _create(tmp_path, *, project_id: str = "prj_1234abcd"):
    return create_project(
        title="AI 创作工作台",
        idea="一个主题变成多篇内容",
        audience="独立创作者",
        goal="完成双平台草稿",
        voice="清晰克制",
        autonomy="collaborate",
        now=NOW,
        project_id=project_id,
        projects_root=tmp_path,
    )


def test_create_and_load_round_trip(tmp_path):
    project = _create(tmp_path)
    assert load_project(project.id, projects_root=tmp_path) == project


def test_create_refuses_existing_manifest(tmp_path):
    _create(tmp_path)
    with pytest.raises(ProjectManifestError, match="already exists"):
        _create(tmp_path)


@pytest.mark.parametrize("payload", ["{", "[]"])
def test_load_rejects_bad_json_shape(tmp_path, payload):
    path = tmp_path / "prj_1234abcd" / "project.json"
    path.parent.mkdir()
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ProjectManifestError):
        load_project("prj_1234abcd", projects_root=tmp_path)


def test_load_rejects_unknown_fields_and_naive_timestamp(tmp_path):
    project = _create(tmp_path)
    path = tmp_path / project.id / "project.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectManifestError, match="missing or unknown"):
        load_project(project.id, projects_root=tmp_path)
    payload.pop("unknown")
    payload["created_at"] = "2026-08-09T10:00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectManifestError, match="timezone"):
        load_project(project.id, projects_root=tmp_path)


def test_update_rejects_invalid_or_duplicate_references(tmp_path):
    project = _create(tmp_path)
    with pytest.raises(ProjectManifestError, match="existing c_"):
        update_project(project, content_ids=("topic_123",), now=LATER, projects_root=tmp_path)
    with pytest.raises(ProjectManifestError, match="duplicate"):
        update_project(project, content_ids=("c_one", "c_one"), now=LATER, projects_root=tmp_path)
    with pytest.raises(ProjectManifestError, match="relative paths"):
        update_project(project, asset_paths=("../cover.png",), now=LATER, projects_root=tmp_path)


def test_update_is_immutable_and_atomic(tmp_path):
    original = _create(tmp_path)
    manifest = tmp_path / original.id / "project.json"
    updated = update_project(
        original,
        content_ids=("c_one", "c_two"),
        asset_paths=("output/projects/prj_1234abcd/cover.png",),
        now=LATER,
        projects_root=tmp_path,
    )
    assert original.content_ids == ()
    assert updated.content_ids == ("c_one", "c_two")
    assert updated.updated_at == LATER
    assert load_project(original.id, projects_root=tmp_path) == updated
    assert not manifest.with_suffix(".json.tmp").exists()


def test_list_orders_by_most_recent_update(tmp_path):
    first = _create(tmp_path, project_id="prj_11111111")
    second = _create(tmp_path, project_id="prj_22222222")
    update_project(first, now=LATER, projects_root=tmp_path)
    assert [project.id for project in list_projects(projects_root=tmp_path)] == [
        first.id,
        second.id,
    ]


@pytest.mark.parametrize(
    "project_id",
    ["prj_x/../../escaped", "prj_../escaped", "prj_back\\slash", "prj_dot.name"],
)
def test_project_id_cannot_escape_sidecar_root(tmp_path, project_id):
    root = tmp_path / "projects"
    with pytest.raises(ProjectManifestError, match="invalid project id"):
        _create(root, project_id=project_id)
    assert list(tmp_path.rglob("project.json")) == []
