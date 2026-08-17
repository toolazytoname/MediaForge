"""RFC §5.7 autonomy assertions for LAZY-45."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline import db, deliverables, master_documents, projects, variants
from pipeline.autonomy import next_cta
from pipeline.delivery.service import DeliveryError, create_draft, create_export_delivery
from pipeline.pack import prepare_pack
from pipeline.delivery import service as delivery_service
from pipeline.publishers.base import AccountConfig, PublishResult
from pipeline.config import PublishConfig

from tests.test_delivery_kernel import _WechatStub, _complete, _ready

_NOW = "2026-08-09T00:00:00+00:00"


class _NoMediaStub(_WechatStub):
    def __init__(self):
        super().__init__(media_id=None)

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        self.calls += 1
        if dry_run:
            return PublishResult("dry-draft", None, '{"dry_run": true}')
        return PublishResult(None, None, '{"draft_media_id": null}')


def _project(root, *, autonomy: str, project_id: str = "prj_auto"):
    return projects.create_project(
        title="项目", idea="想法足够支撑一篇候选主稿。", audience="读者",
        goal="文章", voice="清晰", autonomy=autonomy, now=_NOW,
        project_id=project_id, projects_root=root,
    )


def test_next_cta_is_unique_per_state():
    assert next_cta(research_ready=False, master_ready=False, approval_complete=False, autonomy="collaborate")["label"] == "继续研究"
    assert next_cta(research_ready=True, master_ready=False, approval_complete=False, autonomy="collaborate")["label"] == "写主稿"
    assert next_cta(research_ready=True, master_ready=True, approval_complete=False, autonomy="collaborate")["label"] == "去审批"
    assert next_cta(research_ready=True, master_ready=True, approval_complete=True, autonomy="collaborate")["label"] == "去导出或草稿"
    assert next_cta(research_ready=True, master_ready=True, approval_complete=True, autonomy="pack")["label"] == "去导出"


def test_master_bump_does_not_overwrite_locked_deliverable(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root, project_id="prj_lock")
    before = deliverables.get_deliverable(project_id, "dlv_article_wechat_mp", projects_root=root)
    assert before.locked
    locked_body = before.payload["body"]
    master_documents.save_manual(
        project_id, title="主稿升版", body="新主稿不得覆盖锁定交付物。" * 40,
        now="2026-08-09T00:10:00+00:00", projects_root=root,
    )
    flagged = variants.check_upstream(project_id, "wechat_mp", now="2026-08-09T00:11:00+00:00", projects_root=root)
    after = deliverables.get_deliverable(project_id, "dlv_article_wechat_mp", projects_root=root)
    assert flagged.upstream_updated
    assert after.locked
    assert after.payload["body"] == locked_body
    assert after.title == before.title
    with pytest.raises(variants.VariantsError, match="unlock"):
        variants.acknowledge_master_update(
            project_id, "wechat_mp", now="2026-08-09T00:12:00+00:00", projects_root=root,
        )


def test_unapproved_export_draft_direct_are_409_for_every_strategy(tmp_path):
    root = tmp_path / "projects"
    for autonomy, project_id in (("assist", "prj_a"), ("collaborate", "prj_c"), ("draft", "prj_d"), ("pack", "prj_p")):
        _ready(root, project_id=project_id)
        projects.update_project(
            projects.load_project(project_id, projects_root=root),
            now="2026-08-09T00:06:00+00:00", autonomy=autonomy, projects_root=root,
        )
        conn = db.connect(tmp_path / f"{project_id}.db")
        db.init_db(conn)
        with pytest.raises(DeliveryError) as export_err:
            create_export_delivery(
                conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
                actor="lazy", projects_root=root,
            )
        assert export_err.value.http_status == 409
        assert export_err.value.code == "not_approved"
        with pytest.raises(DeliveryError) as draft_err:
            create_draft(
                conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
                actor="lazy", adapter=_WechatStub(),
                account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
                publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
                projects_root=root,
            )
        assert draft_err.value.http_status == 409
        assert draft_err.value.code == "not_approved"


def test_pack_prepare_stops_at_ready_for_approval_and_never_calls_live_publish(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _project(root, autonomy="pack", project_id="prj_pack")
    spy = MagicMock(wraps=delivery_service.safe_publish)
    monkeypatch.setattr(delivery_service, "safe_publish", spy)
    result = prepare_pack("prj_pack", now="2026-08-09T00:02:00+00:00", projects_root=root)
    assert result.terminal_status in {"drafting", "ready_for_approval"}
    assert all(status in {"drafting", "ready_for_approval"} for status in result.deliverable_statuses)
    assert result.created_master is True
    items = deliverables.load_deliverables("prj_pack", projects_root=root).items
    assert {item.status for item in items} <= {"drafting", "ready_for_approval"}
    assert not any(item.locked for item in items)
    spy.assert_not_called()
    for call in spy.call_args_list:
        assert call.kwargs.get("dry_run") is not False

    _ready(root, project_id="prj_pack_ready")
    projects.update_project(
        projects.load_project("prj_pack_ready", projects_root=root),
        now="2026-08-09T00:07:00+00:00", autonomy="pack", projects_root=root,
    )
    _complete(root, "prj_pack_ready")
    conn = db.connect(tmp_path / "pack.db")
    db.init_db(conn)
    with pytest.raises(DeliveryError) as draft_err:
        create_draft(
            conn, project_id="prj_pack_ready", deliverable_id="dlv_article_wechat_mp",
            actor="lazy", adapter=_WechatStub(),
            account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
            publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
            projects_root=root,
        )
    assert draft_err.value.code == "autonomy_forbids_delivery"
    assert draft_err.value.http_status == 403
    spy.assert_not_called()


def test_missing_media_id_does_not_mark_publication_published(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root, project_id="prj_nomedia")
    _complete(root, project_id)
    conn = db.connect(tmp_path / "nomedia.db")
    db.init_db(conn)
    result = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=_NoMediaStub(),
        account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
        publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
        projects_root=root,
    )
    assert result.attempt.outcome == "failure"
    assert "media_id" in (result.attempt.error or "")
    pub = db.get_publication(conn, result.publication_id)
    assert pub is not None
    assert pub.status != "published"
    assert pub.platform_post_id is None
