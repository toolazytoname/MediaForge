"""RFC §5.6 oauth_token_metadata: refs, scopes, last4. Never store secrets."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from pipeline import db
from pipeline.utils.ids import new_id
from pipeline.utils.redact import token_last4

_SECRET_FIELDS = frozenset({
    "access_token", "refresh_token", "bearer_token", "token", "secret",
    "app_secret", "client_secret", "api_key", "password", "cookie",
})


@dataclass(frozen=True)
class OAuthTokenMetadata:
    id: str
    platform: str
    account_id: str
    auth_kind: str
    key_ref: str
    last4: str | None
    scopes: tuple[str, ...]
    user_id: str | None
    expires_at: str | None
    has_user_context: bool
    created_at: str
    updated_at: str


def _scopes_json(scopes: Iterable[str]) -> str:
    return json.dumps(list(scopes), ensure_ascii=False)


def _parse_scopes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if str(item).strip())
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return tuple(part for part in text.replace(",", " ").split() if part)
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed if str(item).strip())
        return ()
    return ()


def upsert_oauth_metadata(
    conn: sqlite3.Connection,
    *,
    platform: str,
    account_id: str,
    auth_kind: str,
    key_ref: str,
    last4: str | None,
    scopes: Iterable[str] = (),
    user_id: str | None = None,
    expires_at: str | None = None,
    has_user_context: bool = False,
    now: str | None = None,
) -> OAuthTokenMetadata:
    """Insert or replace metadata. Rejects secret-looking values in last4/key_ref."""
    if not platform or not account_id or not auth_kind or not key_ref:
        raise ValueError("platform, account_id, auth_kind, and key_ref are required")
    if last4 and len(last4) > 8:
        raise ValueError("last4 must be a short suffix, not a token")
    lowered_ref = key_ref.lower()
    if any(name in lowered_ref for name in ("access_token=", "bearer ", "secret=")):
        raise ValueError("key_ref must be a path or keychain name, not a secret")
    stamp = now or db.now_utc()
    scope_tuple = tuple(str(item) for item in scopes if str(item).strip())
    existing = get_oauth_metadata(conn, platform, account_id, auth_kind)
    if existing is None:
        row = OAuthTokenMetadata(
            new_id("otm"), platform, account_id, auth_kind, key_ref, last4,
            scope_tuple, user_id, expires_at, bool(has_user_context), stamp, stamp,
        )
        conn.execute(
            """
            INSERT INTO oauth_token_metadata (
                id, platform, account_id, auth_kind, key_ref, last4, scopes,
                user_id, expires_at, has_user_context, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id, row.platform, row.account_id, row.auth_kind, row.key_ref,
                row.last4, _scopes_json(row.scopes), row.user_id, row.expires_at,
                1 if row.has_user_context else 0, row.created_at, row.updated_at,
            ),
        )
        conn.commit()
        return row
    conn.execute(
        """
        UPDATE oauth_token_metadata
        SET key_ref = ?, last4 = ?, scopes = ?, user_id = ?, expires_at = ?,
            has_user_context = ?, updated_at = ?
        WHERE platform = ? AND account_id = ? AND auth_kind = ?
        """,
        (
            key_ref, last4, _scopes_json(scope_tuple), user_id, expires_at,
            1 if has_user_context else 0, stamp, platform, account_id, auth_kind,
        ),
    )
    conn.commit()
    updated = get_oauth_metadata(conn, platform, account_id, auth_kind)
    assert updated is not None
    return updated


def metadata_from_token(
    *,
    platform: str,
    account_id: str,
    auth_kind: str,
    key_ref: str,
    access_token: str | None,
    scopes: Iterable[str],
    user_id: str | None,
    has_user_context: bool,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Build a persistable payload that never includes the token itself."""
    return {
        "platform": platform,
        "account_id": account_id,
        "auth_kind": auth_kind,
        "key_ref": key_ref,
        "last4": token_last4(access_token),
        "scopes": tuple(scopes),
        "user_id": user_id,
        "has_user_context": has_user_context,
        "expires_at": expires_at,
    }


def get_oauth_metadata(
    conn: sqlite3.Connection,
    platform: str,
    account_id: str,
    auth_kind: str,
) -> OAuthTokenMetadata | None:
    row = conn.execute(
        """
        SELECT * FROM oauth_token_metadata
        WHERE platform = ? AND account_id = ? AND auth_kind = ?
        """,
        (platform, account_id, auth_kind),
    ).fetchone()
    return _row_to_meta(row) if row else None


def list_oauth_metadata(conn: sqlite3.Connection) -> list[OAuthTokenMetadata]:
    rows = conn.execute(
        "SELECT * FROM oauth_token_metadata ORDER BY platform, account_id",
    ).fetchall()
    return [_row_to_meta(row) for row in rows]


def assert_row_has_no_secrets(row: OAuthTokenMetadata | dict[str, Any]) -> None:
    payload = row if isinstance(row, dict) else {
        "key_ref": row.key_ref,
        "last4": row.last4,
        "scopes": row.scopes,
        "user_id": row.user_id,
    }
    blob = json.dumps(payload, ensure_ascii=False)
    for field in _SECRET_FIELDS:
        if f'"{field}"' in blob and field not in {"last4"}:
            raise ValueError(f"oauth metadata must not persist {field}")


def _row_to_meta(row: sqlite3.Row) -> OAuthTokenMetadata:
    return OAuthTokenMetadata(
        row["id"], row["platform"], row["account_id"], row["auth_kind"],
        row["key_ref"], row["last4"], _parse_scopes(row["scopes"]),
        row["user_id"], row["expires_at"], bool(row["has_user_context"]),
        row["created_at"], row["updated_at"],
    )


__all__ = [
    "OAuthTokenMetadata",
    "assert_row_has_no_secrets",
    "get_oauth_metadata",
    "list_oauth_metadata",
    "metadata_from_token",
    "upsert_oauth_metadata",
]
