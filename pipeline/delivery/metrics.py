"""DeliveryAttempt MetricSnapshot (LAZY-88). Fixture/manual only.

Views and engagement may be null. Source and collected_at are required.
This table is independent of the frozen publications/metrics pair.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from pipeline.utils.ids import new_id

ALLOWED_SOURCES = frozenset({"fixture", "manual"})


class MetricSnapshotError(ValueError):
    """MetricSnapshot is missing required fields or the attempt is unknown."""


@dataclass(frozen=True)
class MetricSnapshot:
    id: str
    delivery_attempt_id: str
    project_id: str
    source: str
    collected_at: str
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    raw: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def insert_metric_snapshot(
    conn: sqlite3.Connection,
    *,
    delivery_attempt_id: str,
    source: str,
    collected_at: str,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    raw: str | None = None,
    snapshot_id: str | None = None,
) -> MetricSnapshot:
    if source not in ALLOWED_SOURCES:
        raise MetricSnapshotError("source must be fixture or manual")
    if not isinstance(collected_at, str) or not collected_at.strip():
        raise MetricSnapshotError("collected_at is required")
    attempt = conn.execute(
        "SELECT id, project_id FROM delivery_attempts WHERE id = ?",
        (delivery_attempt_id,),
    ).fetchone()
    if attempt is None:
        raise MetricSnapshotError("delivery attempt not found")
    snap = MetricSnapshot(
        snapshot_id or new_id("ms"),
        delivery_attempt_id,
        attempt["project_id"],
        source,
        collected_at,
        _optional_int("views", views),
        _optional_int("likes", likes),
        _optional_int("comments", comments),
        _optional_int("shares", shares),
        raw,
    )
    conn.execute(
        """
        INSERT INTO delivery_metrics (
            id, delivery_attempt_id, project_id, source, collected_at,
            views, likes, comments, shares, raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snap.id, snap.delivery_attempt_id, snap.project_id, snap.source,
            snap.collected_at, snap.views, snap.likes, snap.comments,
            snap.shares, snap.raw,
        ),
    )
    conn.commit()
    return snap


def list_metric_snapshots(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    delivery_attempt_id: str | None = None,
) -> list[MetricSnapshot]:
    sql = "SELECT * FROM delivery_metrics"
    params: list[Any] = []
    clauses: list[str] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if delivery_attempt_id:
        clauses.append("delivery_attempt_id = ?")
        params.append(delivery_attempt_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY collected_at DESC"
    return [_row_to_snapshot(row) for row in conn.execute(sql, params).fetchall()]


def _optional_int(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetricSnapshotError(f"{name} must be an int or null")
    return value


def _row_to_snapshot(row: sqlite3.Row) -> MetricSnapshot:
    return MetricSnapshot(
        row["id"], row["delivery_attempt_id"], row["project_id"], row["source"],
        row["collected_at"], row["views"], row["likes"], row["comments"],
        row["shares"], row["raw"],
    )


__all__ = [
    "ALLOWED_SOURCES",
    "MetricSnapshot",
    "MetricSnapshotError",
    "insert_metric_snapshot",
    "list_metric_snapshots",
]
