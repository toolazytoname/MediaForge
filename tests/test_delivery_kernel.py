from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import approvals, db, master_documents, project_exports, projects, research, variants, visuals
from pipeline.config import DeliveryConfig, PublishConfig
from pipeline.delivery.service import (
    DeliveryError,
    create_draft,
    create_export_delivery,
    preview_deliverable,
)
from pipeline.delivery.store import is_project_bridged_publication, latest_attempts
from pipeline.models import PublicationStatus
from pipeline.publishers.base import AccountConfig, PostBundle, PublishResult, PublisherAdapter

_NOW = "2026-08-09T00:00:00+00:00"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _WechatStub(PublisherAdapter):
    platform = "wechat_mp"

    def __init__(self, media_id: str = "media_fixture_1"):
        self.media_id = media_id
        self.calls = 0

    def validate(self, bundle: PostBundle) -> list[str]:
        return []

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        self.calls += 1
        if dry_run:
            return PublishResult("dry-draft", None, '{"dry_run": true}')
        return PublishResult(self.media_id, None, '{"draft_media_id": "%s"}' % self.media_id)


def _ready(root: Path, project_id: str = "prj_delivery") -> str:
    projects.create_project(
        title="项目", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="collaborate", now=_NOW, project_id=project_id, projects_root=root,
    )
    source_ids = [
        research.add_source(
            project_id, title=f"来源{i}", reference=f"https://example.com/{i}",
            summary="摘要", now="2026-08-09T00:00:30+00:00", projects_root=root,
        ).id
        for i in range(3)
    ]
    research.add_claim(project_id, text="已核查事实", kind="fact", source_ids=[source_ids[0]], status="verified", now="2026-08-09T00:00:40+00:00", projects_root=root)
    research.add_claim(project_id, text="个人判断", kind="judgment", source_ids=[], status="verified", now="2026-08-09T00:00:50+00:00", projects_root=root)
    master_documents.save_manual(project_id, title="主稿", body="足够长的真实正文。" * 120, now="2026-08-09T00:01:00+00:00", projects_root=root)
    slots = [
        {"id": f"vsl_{name}", "purpose": purpose, "paragraph_anchor": None if name == "cover" else "正文", "direction": "克制", "aspect_ratio": "16:9"}
        for name, purpose in (("cover", "封面"), ("one", "正文插图一"), ("two", "正文插图二"))
    ]
    visuals.save_plan(project_id, bible={"style": "plain"}, slots=slots, projects_root=root)
    for index, slot in enumerate(slots):
        asset_id = f"vas_delivery_{index}"
        png = root / project_id / "assets" / f"{asset_id}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(_PNG)
        asset = visuals.record_asset(
            project_id, slot_id=slot["id"], prompt="visual", model="fake", size="16:9",
            cost_usd=0, now="2026-08-09T00:02:00+00:00", file_path=f"assets/{asset_id}.png",
            status="candidate", asset_id=asset_id, projects_root=root,
        )
        visuals.select_asset(project_id, asset.id, reason="合适", rating=4, projects_root=root)
    for platform in ("wechat_mp", "toutiao"):
        variants.create_from_master(project_id, platform, now="2026-08-09T00:03:00+00:00", projects_root=root)
        variants.set_locked(project_id, platform, locked=True, now="2026-08-09T00:03:30+00:00", projects_root=root)
    return project_id


def _complete(root: Path, project_id: str) -> None:
    approvals.recheck(project_id, actor="lazy", now="2026-08-09T00:04:00+00:00", projects_root=root)
    for check in ("master", "visuals", "wechat_mp", "toutiao"):
        approvals.decide(project_id, check, approved=True, note=None, actor="lazy", now="2026-08-09T00:05:00+00:00", projects_root=root)


def _official(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    out = {}
    for name in ("topics", "contents", "publications", "metrics"):
        rows = conn.execute(f"SELECT * FROM {name}").fetchall()
        out[name] = [tuple(row) for row in rows]
    return out


def test_preview_does_not_touch_official_tables_or_write_draft_success(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    before = _official(conn)
    adapter = _WechatStub()
    account = AccountConfig(id="main", credentials_path=tmp_path / "missing.json")
    result = preview_deliverable(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, projects_root=root,
    )
    assert result.attempt.mode == "preview"
    assert result.attempt.outcome == "success"
    assert _official(conn) == before
    rows = conn.execute("SELECT mode, outcome FROM delivery_attempts").fetchall()
    assert all(row["mode"] == "preview" for row in rows)
    assert not any(row["mode"] in {"draft", "direct"} and row["outcome"] == "success" for row in rows)


def test_unapproved_draft_and_export_are_409(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    with pytest.raises(DeliveryError) as export_err:
        create_export_delivery(conn, project_id=project_id, deliverable_id="dlv_article_toutiao", actor="lazy", projects_root=root)
    assert export_err.value.http_status == 409
    assert export_err.value.code == "not_approved"
    with pytest.raises(DeliveryError) as draft_err:
        create_draft(
            conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp", actor="lazy",
            adapter=_WechatStub(), account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
            publish_config=PublishConfig(enabled=True, allowed_platforms=["wechat_mp"]),
            projects_root=root,
        )
    assert draft_err.value.http_status == 409


def test_wechat_draft_writes_media_id_empty_url_and_is_idempotent(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    _complete(root, project_id)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = _WechatStub()
    account = AccountConfig(id="main", credentials_path=tmp_path / "x.json")
    cfg = PublishConfig(enabled=True, allowed_platforms=["wechat_mp"])
    first = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg, projects_root=root,
    )
    assert first.attempt.outcome == "success"
    assert first.media_id == "media_fixture_1"
    assert first.attempt.platform_url is None
    pub = db.get_publication(conn, first.publication_id)
    assert pub is not None
    assert pub.status == PublicationStatus.PUBLISHED.value
    assert pub.platform_post_id == "media_fixture_1"
    assert pub.platform_url is None
    assert adapter.calls == 1
    second = create_draft(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", adapter=adapter, account=account, publish_config=cfg, projects_root=root,
    )
    assert second.replayed is True
    assert second.attempt.id == first.attempt.id
    assert adapter.calls == 1
    pubs = conn.execute("SELECT id FROM publications").fetchall()
    assert len(pubs) == 1
    assert is_project_bridged_publication(conn, first.publication_id)


def test_toutiao_export_has_no_publication_and_rollback_keeps_zip(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    _complete(root, project_id)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = create_export_delivery(
        conn, project_id=project_id, deliverable_id="dlv_article_toutiao",
        actor="lazy", projects_root=root,
    )
    assert result.attempt.mode == "export"
    assert result.attempt.outcome == "success"
    assert result.attempt.publication_id is None
    assert conn.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"] == 0
    archive = root / project_id / result.export.path
    assert archive.is_file()
    rolled = project_exports.create_export(project_id, projects_root=root)
    assert Path(root / project_id / rolled.path).is_file()
    assert DeliveryConfig(bridge="off").bridge == "off"


def test_preview_allowed_before_complete(tmp_path):
    root = tmp_path / "projects"
    project_id = _ready(root)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    result = preview_deliverable(
        conn, project_id=project_id, deliverable_id="dlv_article_wechat_mp",
        actor="lazy", projects_root=root,
    )
    assert result.attempt.mode == "preview"
    assert latest_attempts(conn, project_id)
