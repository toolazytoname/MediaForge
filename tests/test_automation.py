"""LAZY-88: scheduled prepare stops at awaiting_approval."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline import db, deliverables, projects
from pipeline.automation import (
    AWAITING_APPROVAL,
    SKIPPED,
    load_automation,
    prepare_project,
    run_prepare_due,
)
from pipeline.config import PublishConfig
from pipeline.delivery import service as delivery_service
from pipeline.delivery.service import DeliveryError, create_draft, create_export_delivery
from pipeline.delivery.store import latest_attempts
from pipeline.publishers.base import AccountConfig

from tests.test_delivery_kernel import _WechatStub

_NOW = "2026-08-17T07:00:00+00:00"


def _project(root, *, autonomy: str, project_id: str):
    return projects.create_project(
        title="定时项目", idea="想法足够支撑一篇候选主稿。", audience="读者",
        goal="文章", voice="清晰", autonomy=autonomy, now=_NOW,
        project_id=project_id, projects_root=root,
    )


def test_prepare_due_pack_stops_at_awaiting_approval_without_side_effects(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_pack_auto")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    spy = MagicMock()
    monkeypatch.setattr(delivery_service, "safe_publish", spy)

    result = run_prepare_due(conn, now=_NOW, actor="cron", projects_root=root)
    assert result.prepared == 1
    assert result.items[0].status == AWAITING_APPROVAL
    state = load_automation("prj_pack_auto", projects_root=root)
    assert state["status"] == AWAITING_APPROVAL
    items = deliverables.load_deliverables("prj_pack_auto", projects_root=root).items
    assert {item.status for item in items} <= {"drafting", "ready_for_approval"}
    assert not any(item.locked for item in items)
    assert latest_attempts(conn, "prj_pack_auto") == []
    spy.assert_not_called()
    audits = conn.execute("SELECT action FROM audit_events").fetchall()
    assert "automation.prepare" in {row["action"] for row in audits}

    with pytest.raises(DeliveryError) as export_err:
        create_export_delivery(
            conn, project_id="prj_pack_auto", deliverable_id="dlv_article_toutiao",
            actor="lazy", projects_root=root,
        )
    assert export_err.value.code in {"not_approved", "autonomy_forbids_delivery"}
    with pytest.raises(DeliveryError) as draft_err:
        create_draft(
            conn, project_id="prj_pack_auto", deliverable_id="dlv_article_wechat_mp",
            actor="lazy", adapter=_WechatStub(),
            account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
            publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
            projects_root=root,
        )
    assert draft_err.value.code in {"not_approved", "autonomy_forbids_delivery"}


def test_prepare_due_skips_assist_and_collaborate(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="assist", project_id="prj_assist_auto")
    _project(root, autonomy="collaborate", project_id="prj_collab_auto")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = run_prepare_due(conn, now=_NOW, actor="cron", projects_root=root)
    assert result.skipped == 2
    assert {item.status for item in result.items} == {SKIPPED}
    assert latest_attempts(conn, "prj_assist_auto") == []
    assert latest_attempts(conn, "prj_collab_auto") == []


def test_prepare_due_draft_also_stops_before_delivery(tmp_path):
    root = tmp_path / "projects"
    _project(root, autonomy="draft", project_id="prj_draft_auto")
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    item = prepare_project(conn, "prj_draft_auto", now=_NOW, actor="cron", projects_root=root)
    assert item.status == AWAITING_APPROVAL
    assert latest_attempts(conn, "prj_draft_auto") == []
