"""Append-only delivery_attempts / audit_events / legacy_bindings (RFC §5.6, D1)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from pipeline import db
from pipeline.utils.ids import new_id

_RECEIPT_MAX = 4000
_SECRET_KEYS = ("token", "secret", "password", "cookie", "authorization", "app_secret", "access_token")


@dataclass(frozen=True)
class DeliveryAttempt:
    id: str
    project_id: str
    deliverable_id: str
    deliverable_version: int
    approval_fingerprint: str
    publication_id: str | None
    content_id: str | None
    platform: str
    account_id: str
    mode: str
    outcome: str
    idempotency_key: str
    retry_of_id: str | None
    compensation_of_id: str | None
    request_hash: str
    platform_post_id: str | None
    platform_url: str | None
    raw_receipt: str | None
    error: str | None
    actor: str
    confirm_token_hash: str | None
    created_at: str


@dataclass(frozen=True)
class LegacyBinding:
    project_id: str
    deliverable_id: str
    content_id: str
    publication_id: str | None
    platform: str
    account_id: str
    materialize_dir: str
    created_at: str


def make_idempotency_key(
    *,
    project_id: str,
    deliverable_id: str,
    deliverable_version: int,
    platform: str,
    account_id: str,
    mode: str,
    approval_fingerprint: str,
    preview_nonce: str | None = None,
    retry_of_id: str | None = None,
) -> str:
    parts = [
        project_id, deliverable_id, str(deliverable_version), platform,
        account_id, mode, approval_fingerprint,
    ]
    if preview_nonce:
        parts.append(f"preview:{preview_nonce}")
    if retry_of_id:
        parts.append(f"retry:{retry_of_id}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def request_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def redact_receipt(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw[:_RECEIPT_MAX]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(_redact(parsed), ensure_ascii=False)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in _SECRET_KEYS):
                out[key] = "[redacted]"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def get_attempt_by_key(conn: sqlite3.Connection, idempotency_key: str) -> DeliveryAttempt | None:
    row = conn.execute(
        "SELECT * FROM delivery_attempts WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    return _row_to_attempt(row) if row else None


def get_attempt(conn: sqlite3.Connection, attempt_id: str) -> DeliveryAttempt | None:
    row = conn.execute("SELECT * FROM delivery_attempts WHERE id = ?", (attempt_id,)).fetchone()
    return _row_to_attempt(row) if row else None


def latest_attempts(conn: sqlite3.Connection, project_id: str) -> list[DeliveryAttempt]:
    rows = conn.execute(
        "SELECT * FROM delivery_attempts WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    return [_row_to_attempt(row) for row in rows]


def insert_attempt(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str,
    deliverable_version: int,
    approval_fingerprint: str,
    platform: str,
    account_id: str,
    mode: str,
    outcome: str,
    idempotency_key: str,
    request_hash_value: str,
    actor: str,
    publication_id: str | None = None,
    content_id: str | None = None,
    retry_of_id: str | None = None,
    compensation_of_id: str | None = None,
    platform_post_id: str | None = None,
    platform_url: str | None = None,
    raw_receipt: str | None = None,
    error: str | None = None,
    confirm_token_hash: str | None = None,
    created_at: str | None = None,
) -> DeliveryAttempt:
    """Insert one terminal attempt row. Never UPDATE/DELETE."""
    if outcome not in {"success", "failure", "unknown"}:
        raise ValueError(f"invalid delivery outcome: {outcome}")
    if mode not in {"preview", "export", "draft", "direct"}:
        raise ValueError(f"invalid delivery mode: {mode}")
    attempt = DeliveryAttempt(
        new_id("da"), project_id, deliverable_id, deliverable_version,
        approval_fingerprint, publication_id, content_id, platform, account_id,
        mode, outcome, idempotency_key, retry_of_id, compensation_of_id,
        request_hash_value, platform_post_id, platform_url, redact_receipt(raw_receipt),
        error, actor, confirm_token_hash, created_at or db.now_utc(),
    )
    conn.execute(
        """
        INSERT INTO delivery_attempts (
            id, project_id, deliverable_id, deliverable_version, approval_fingerprint,
            publication_id, content_id, platform, account_id, mode, outcome,
            idempotency_key, retry_of_id, compensation_of_id, request_hash,
            platform_post_id, platform_url, raw_receipt, error, actor,
            confirm_token_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt.id, attempt.project_id, attempt.deliverable_id, attempt.deliverable_version,
            attempt.approval_fingerprint, attempt.publication_id, attempt.content_id,
            attempt.platform, attempt.account_id, attempt.mode, attempt.outcome,
            attempt.idempotency_key, attempt.retry_of_id, attempt.compensation_of_id,
            attempt.request_hash, attempt.platform_post_id, attempt.platform_url,
            attempt.raw_receipt, attempt.error, attempt.actor, attempt.confirm_token_hash,
            attempt.created_at,
        ),
    )
    conn.commit()
    return attempt


def get_binding(
    conn: sqlite3.Connection,
    *,
    deliverable_id: str,
    platform: str,
    account_id: str,
) -> LegacyBinding | None:
    row = conn.execute(
        "SELECT * FROM legacy_bindings WHERE deliverable_id = ? AND platform = ? AND account_id = ?",
        (deliverable_id, platform, account_id),
    ).fetchone()
    return _row_to_binding(row) if row else None


def upsert_binding(conn: sqlite3.Connection, binding: LegacyBinding) -> LegacyBinding:
    existing = get_binding(
        conn, deliverable_id=binding.deliverable_id,
        platform=binding.platform, account_id=binding.account_id,
    )
    if existing is not None:
        if existing.content_id != binding.content_id:
            raise ValueError("legacy binding content_id mismatch")
        if binding.publication_id and existing.publication_id != binding.publication_id:
            conn.execute(
                "UPDATE legacy_bindings SET publication_id = ? "
                "WHERE deliverable_id = ? AND platform = ? AND account_id = ?",
                (binding.publication_id, binding.deliverable_id, binding.platform, binding.account_id),
            )
            conn.commit()
            return LegacyBinding(
                existing.project_id, existing.deliverable_id, existing.content_id,
                binding.publication_id, existing.platform, existing.account_id,
                existing.materialize_dir, existing.created_at,
            )
        return existing
    conn.execute(
        """
        INSERT INTO legacy_bindings (
            project_id, deliverable_id, content_id, publication_id,
            platform, account_id, materialize_dir, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            binding.project_id, binding.deliverable_id, binding.content_id,
            binding.publication_id, binding.platform, binding.account_id,
            binding.materialize_dir, binding.created_at,
        ),
    )
    conn.commit()
    return binding


def insert_audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    payload: dict[str, Any],
    project_id: str | None = None,
    deliverable_id: str | None = None,
    publication_id: str | None = None,
    at: str | None = None,
) -> None:
    at_value = at or db.now_utc()
    prev = conn.execute(
        "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev["event_hash"] if prev else None
    body = json.dumps(_redact(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    material = f"{prev_hash or ''}|{at_value}|{actor}|{action}|{body}"
    event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO audit_events (
            at, actor, action, project_id, deliverable_id, publication_id,
            payload, prev_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (at_value, actor, action, project_id, deliverable_id, publication_id, body, prev_hash, event_hash),
    )
    conn.commit()


def is_project_bridged_publication(conn: sqlite3.Connection, publication_id: str) -> bool:
    row = conn.execute(
        """
        SELECT t.source FROM publications p
        JOIN contents c ON c.id = p.content_id
        JOIN topics t ON t.id = c.topic_id
        WHERE p.id = ?
        """,
        (publication_id,),
    ).fetchone()
    if row is not None and str(row["source"]).startswith("project:"):
        return True
    bound = conn.execute(
        "SELECT 1 FROM legacy_bindings WHERE publication_id = ?",
        (publication_id,),
    ).fetchone()
    return bound is not None


def _row_to_attempt(row: sqlite3.Row) -> DeliveryAttempt:
    return DeliveryAttempt(
        row["id"], row["project_id"], row["deliverable_id"], row["deliverable_version"],
        row["approval_fingerprint"], row["publication_id"], row["content_id"],
        row["platform"], row["account_id"], row["mode"], row["outcome"],
        row["idempotency_key"], row["retry_of_id"], row["compensation_of_id"],
        row["request_hash"], row["platform_post_id"], row["platform_url"],
        row["raw_receipt"], row["error"], row["actor"], row["confirm_token_hash"],
        row["created_at"],
    )


def _row_to_binding(row: sqlite3.Row) -> LegacyBinding:
    return LegacyBinding(
        row["project_id"], row["deliverable_id"], row["content_id"],
        row["publication_id"], row["platform"], row["account_id"],
        row["materialize_dir"], row["created_at"],
    )


__all__ = [
    "DeliveryAttempt",
    "LegacyBinding",
    "get_attempt",
    "get_attempt_by_key",
    "get_binding",
    "insert_attempt",
    "insert_audit",
    "is_project_bridged_publication",
    "latest_attempts",
    "make_idempotency_key",
    "redact_receipt",
    "request_hash",
    "upsert_binding",
]
