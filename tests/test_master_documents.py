from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.master_documents import (
    HISTORY_LIMIT, MasterDocumentError, accept_suggestion, create_suggestion,
    list_versions, load_master, reject_suggestion, restore_version, save_manual,
)
from pipeline.projects import create_project


NOW = "2026-08-09T10:00:00+00:00"
LATER = "2026-08-09T11:00:00+00:00"


def _project(tmp_path):
    return create_project(title="主稿", idea="一个观察", audience="独立创作者", goal="写主稿", voice="克制",
                          autonomy="collaborate", now=NOW, project_id="prj_master", projects_root=tmp_path)


def test_manual_save_creates_atomic_master_and_immutable_versions(tmp_path):
    project = _project(tmp_path)
    first = save_manual(project.id, title="第一版", body="人先写下这一段。", now=NOW, projects_root=tmp_path)
    second = save_manual(project.id, title="第二版", body="人保留并修订这一段。", now=LATER, projects_root=tmp_path)

    assert first.version == 1
    assert second.version == 2
    assert first.body == "人先写下这一段。"
    assert load_master(project.id, projects_root=tmp_path) == second
    assert list_versions(project.id, projects_root=tmp_path)[0].body == first.body
    assert (tmp_path / project.id / "master.md").read_text(encoding="utf-8") == "# 第二版\n\n人保留并修订这一段。\n"
    assert not (tmp_path / project.id / "master.json.tmp").exists()
    assert not (tmp_path / project.id / "master.md.tmp").exists()


def test_suggestion_never_changes_master_until_accept_and_reject_does_not_write_master(tmp_path):
    project = _project(tmp_path)
    master = save_manual(project.id, title="标题", body="这段话可以更清楚。", now=NOW, projects_root=tmp_path)
    master_path = tmp_path / project.id / "master.json"
    before = master_path.read_text(encoding="utf-8")
    suggestion = create_suggestion(project.id, action="clarify", selection="这段话可以更清楚。",
                                   proposed_title="标题", proposed_body="这句话表达得更清楚。", now=LATER,
                                   suggestion_id="sug_one", projects_root=tmp_path)
    assert load_master(project.id, projects_root=tmp_path) == master

    rejected = reject_suggestion(project.id, suggestion.id, now=LATER, projects_root=tmp_path)
    assert rejected.status == "rejected"
    assert master_path.read_text(encoding="utf-8") == before


def test_accept_and_restore_always_create_new_versions(tmp_path):
    project = _project(tmp_path)
    first = save_manual(project.id, title="标题", body="原文", now=NOW, projects_root=tmp_path)
    suggestion = create_suggestion(project.id, action="shorten", selection=None, proposed_title="标题", proposed_body="短文", now=LATER,
                                   suggestion_id="sug_accept", projects_root=tmp_path)
    second = accept_suggestion(project.id, suggestion.id, now=LATER, projects_root=tmp_path)
    third = restore_version(project.id, first.version, now="2026-08-09T12:00:00+00:00", projects_root=tmp_path)

    assert (first.version, second.version, third.version) == (1, 2, 3)
    assert third.body == "原文"
    assert [item.version for item in list_versions(project.id, projects_root=tmp_path)] == [1, 2, 3]


def test_master_rejects_bad_manifest_stale_suggestion_and_missing_version(tmp_path):
    project = _project(tmp_path)
    save_manual(project.id, title="标题", body="原文", now=NOW, projects_root=tmp_path)
    path = tmp_path / project.id / "master.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MasterDocumentError, match="missing or unknown"):
        load_master(project.id, projects_root=tmp_path)

    payload.pop("unknown")
    path.write_text(json.dumps(payload), encoding="utf-8")
    save_manual(project.id, title="标题", body="原文", now=NOW, projects_root=tmp_path)
    suggestion = create_suggestion(project.id, action="clarify", selection=None, proposed_title="标题", proposed_body="建议", now=LATER,
                                   suggestion_id="sug_stale", projects_root=tmp_path)
    save_manual(project.id, title="标题", body="人工更新", now=LATER, projects_root=tmp_path)
    with pytest.raises(MasterDocumentError, match="stale"):
        accept_suggestion(project.id, suggestion.id, now=LATER, projects_root=tmp_path)
    with pytest.raises(MasterDocumentError, match="version not found"):
        restore_version(project.id, 99, now=LATER, projects_root=tmp_path)


def test_history_is_bounded(tmp_path):
    project = _project(tmp_path)
    for index in range(HISTORY_LIMIT + 3):
        now = (datetime(2026, 8, 9, 10, tzinfo=timezone.utc) + timedelta(hours=index)).isoformat()
        save_manual(project.id, title="标题", body=f"版本 {index}", now=now, projects_root=tmp_path)
    master = load_master(project.id, projects_root=tmp_path)
    assert master is not None
    assert len(master.history) == HISTORY_LIMIT
    assert master.version == HISTORY_LIMIT + 3
