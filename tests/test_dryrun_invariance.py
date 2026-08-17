"""dry-run 不得改正式业务表（LAZY-25）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import db
from pipeline.config import PublishConfig
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.safe_publish import safe_publish


_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()
_PAST_ISO = (_NOW - timedelta(hours=1)).isoformat()


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    out: dict[str, list[tuple]] = {}
    for name in names:
        rows = conn.execute(f"SELECT * FROM {name}").fetchall()
        out[name] = [tuple(r) for r in rows]
    return out


def test_dry_run_official_tables_field_level_unchanged(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    now = _NOW_ISO
    db.insert_topic(conn, Topic(
        id="t_dry0001", source="rss:test", title="T", url=None,
        summary=None, content_hash="h-dry", pillar="ai_daily",
        score=7.0, score_reason=None, status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    ))
    db.insert_content(conn, Content(
        id="c_dry0001", topic_id="t_dry0001", pillar="ai_daily",
        title="C", canonical_path=str(tmp_path / "canonical.md"),
        formats='["x"]', gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}', gate_verdict="通过",
        status=ContentStatus.APPROVED.value, created_at=now, updated_at=now,
    ))
    db.insert_publication(conn, Publication(
        id="p_dry0001", content_id="c_dry0001", platform="x",
        account_id="main", scheduled_at=_PAST_ISO,
        published_at=None, platform_post_id=None, platform_url=None,
        error=None, retry_count=0, status=PublicationStatus.QUEUED.value,
        created_at=now, updated_at=now,
    ))
    pub = db.get_publication(conn, "p_dry0001")
    assert pub is not None

    class PreviewAdapter(PublisherAdapter):
        platform = "x"

        def validate(self, bundle: PostBundle) -> list[str]:
            return []

        def publish(self, bundle, account, dry_run=False) -> PublishResult:
            assert dry_run is True
            return PublishResult("dry-xyz", None, '{"dry_run": true}')

    before = _snapshot(conn)
    result = safe_publish(
        conn, pub, PreviewAdapter(),
        config=PublishConfig(
            enabled=True, allowed_platforms=["x"],
            min_gap_hours=4, max_daily_per_account=3,
            cross_platform_gap_minutes=30,
        ),
        account=AccountConfig(id="main", credentials_path=Path("secrets/x.json")),
        dry_run=True, now_iso=_NOW_ISO, log_dir=tmp_path / "logs",
    )
    after = _snapshot(conn)
    assert result.published is False
    assert result.dry_run is True
    assert before == after
