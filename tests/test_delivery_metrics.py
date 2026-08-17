"""LAZY-88: fixture MetricSnapshot for DeliveryAttempt."""
from __future__ import annotations

import pytest

from pipeline import db
from pipeline.delivery.metrics import (
    MetricSnapshotError,
    insert_metric_snapshot,
    list_metric_snapshots,
)
from pipeline.delivery.store import insert_attempt


def _attempt(conn, project_id="prj_metrics"):
    return insert_attempt(
        conn,
        project_id=project_id,
        deliverable_id="dlv_article_wechat_mp",
        deliverable_version=1,
        approval_fingerprint="a" * 64,
        platform="wechat_mp",
        account_id="main",
        mode="export",
        outcome="success",
        idempotency_key="k" + project_id,
        request_hash_value="h" * 64,
        actor="lazy",
        created_at="2026-08-17T08:00:00+00:00",
    )


def test_metric_snapshot_allows_null_engagement_and_requires_source_time(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    attempt = _attempt(conn)
    snap = insert_metric_snapshot(
        conn,
        delivery_attempt_id=attempt.id,
        source="fixture",
        collected_at="2026-08-17T09:00:00+00:00",
        views=None,
        likes=None,
        comments=None,
        shares=None,
    )
    assert snap.project_id == "prj_metrics"
    assert snap.source == "fixture"
    assert snap.collected_at == "2026-08-17T09:00:00+00:00"
    assert snap.views is None
    listed = list_metric_snapshots(conn, project_id="prj_metrics")
    assert [item.id for item in listed] == [snap.id]

    with pytest.raises(MetricSnapshotError, match="source"):
        insert_metric_snapshot(
            conn, delivery_attempt_id=attempt.id, source="wechat",
            collected_at="2026-08-17T09:01:00+00:00",
        )
    with pytest.raises(MetricSnapshotError, match="collected_at"):
        insert_metric_snapshot(
            conn, delivery_attempt_id=attempt.id, source="fixture", collected_at="",
        )
    with pytest.raises(MetricSnapshotError, match="not found"):
        insert_metric_snapshot(
            conn, delivery_attempt_id="da_missing", source="fixture",
            collected_at="2026-08-17T09:02:00+00:00",
        )


def test_delivery_metrics_table_is_created_by_init_db(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "delivery_metrics" in tables
    version = conn.execute("SELECT name FROM schema_migrations WHERE version=4").fetchone()
    assert version["name"] == "4_delivery_metrics"
