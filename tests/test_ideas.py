from __future__ import annotations

import json

import pytest

from pipeline.ideas import (
    IdeaManifestError,
    create_idea,
    list_ideas,
    load_idea,
    promote_idea,
)


NOW = "2026-08-09T08:00:00+00:00"
LATER = "2026-08-09T09:00:00+00:00"


def _create(root, *, idea_id="idea_1234abcd", **kwargs):
    input_type = kwargs.pop("input_type", "thought")
    content = kwargs.pop("content", "创作者不该先学习状态机。")
    return create_idea(
        input_type=input_type,
        content=content,
        title="创作工作台的第一步",
        now=NOW,
        idea_id=idea_id,
        ideas_root=root,
        **kwargs,
    )


def test_create_load_and_list_ideas_by_recent_update(tmp_path):
    first = _create(tmp_path, idea_id="idea_first")
    second = _create(tmp_path, idea_id="idea_second")
    promoted = promote_idea(second, project_id="prj_1234", now=LATER, ideas_root=tmp_path)

    assert load_idea(first.id, ideas_root=tmp_path) == first
    assert list_ideas(ideas_root=tmp_path) == (promoted, first)


@pytest.mark.parametrize(
    ("input_type", "content"),
    [("unknown", "valid"), ("url", "not a URL"), ("thought", "   ")],
)
def test_create_rejects_invalid_input(tmp_path, input_type, content):
    with pytest.raises(IdeaManifestError):
        _create(tmp_path, input_type=input_type, content=content)


def test_load_rejects_unknown_fields_and_invalid_json(tmp_path):
    manifest = tmp_path / "idea_broken" / "idea.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"id": "idea_broken", "extra": 1}), encoding="utf-8")
    with pytest.raises(IdeaManifestError, match="missing or unknown"):
        load_idea("idea_broken", ideas_root=tmp_path)

    manifest.write_text("{", encoding="utf-8")
    with pytest.raises(IdeaManifestError, match="invalid idea JSON"):
        load_idea("idea_broken", ideas_root=tmp_path)


def test_promote_is_immutable_and_uses_atomic_replacement(tmp_path, monkeypatch):
    original = _create(tmp_path)
    manifest = tmp_path / original.id / "idea.json"
    observed = []
    original_replace = type(manifest).replace

    def recording_replace(source, target):
        observed.append((source, target))
        return original_replace(source, target)

    monkeypatch.setattr(type(manifest), "replace", recording_replace)
    promoted = promote_idea(original, project_id="prj_1234", now=LATER, ideas_root=tmp_path)

    assert original.project_id is None
    assert promoted.project_id == "prj_1234"
    assert promoted.updated_at == LATER
    assert load_idea(original.id, ideas_root=tmp_path) == promoted
    assert observed and observed[0][1] == manifest
