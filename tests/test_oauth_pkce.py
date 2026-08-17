"""OAuth 2.0 PKCE helpers never persist secrets."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.oauth.pkce import build_authorize_request, generate_pkce_pair
from pipeline.publishers.x_api import (
    AUTH_APP_BEARER,
    build_x_authorize_request,
    exchange_x_authorization_code,
    load_x_credential_set,
)


def test_pkce_challenge_is_s256_and_not_the_verifier() -> None:
    pair = generate_pkce_pair(entropy=b"\x01" * 32)
    assert pair.method == "S256"
    assert pair.verifier != pair.challenge
    assert "=" not in pair.verifier
    assert "=" not in pair.challenge


def test_authorize_url_includes_challenge_not_verifier() -> None:
    pair = generate_pkce_pair(entropy=b"\x02" * 32)
    req = build_authorize_request(
        "https://example.com/oauth/authorize",
        client_id="client-1",
        redirect_uri="https://app.example/cb",
        scopes=("tweet.write", "users.read"),
        state="st",
        pkce=pair,
    )
    assert "code_challenge=" in req.url
    assert pair.challenge in req.url
    assert pair.verifier not in req.url
    assert "client-secret" not in req.url


def test_x_pkce_exchange_writes_user_file_and_metadata_only(tmp_path: Path) -> None:
    path = tmp_path / "x_main.json"

    def post(_url, **_kwargs):
        return {
            "token_type": "bearer",
            "access_token": "x-user-access-token",
            "refresh_token": "x-user-refresh-token",
            "scope": "tweet.write users.read tweet.read",
        }

    def get(_url, **_kwargs):
        return {"data": {"id": "user-42", "username": "fixture"}}

    meta = exchange_x_authorization_code(
        client_id="client-1",
        redirect_uri="https://app.example/cb",
        code="auth-code",
        verifier="verifier-1",
        credentials_path=path,
        http_post=post,
        http_get=get,
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["auth_mode"] != AUTH_APP_BEARER
    assert raw["user_id"] == "user-42"
    assert meta["last4"] == "oken"
    blob = json.dumps(meta)
    assert "x-user-access-token" not in blob
    assert "x-user-refresh-token" not in blob
    creds = load_x_credential_set(path)
    assert creds.has_user_context is True


def test_x_pkce_exchange_without_user_id_fails(tmp_path: Path) -> None:
    from pipeline.publishers.base import PublishError

    def post(_url, **_kwargs):
        return {"token_type": "bearer", "access_token": "x-user-access-token", "scope": "tweet.write"}

    def get(_url, **_kwargs):
        return {"data": {}}

    with pytest.raises(PublishError, match="user_id"):
        exchange_x_authorization_code(
            client_id="client-1",
            redirect_uri="https://app.example/cb",
            code="auth-code",
            verifier="verifier-1",
            credentials_path=tmp_path / "x.json",
            http_post=post,
            http_get=get,
        )


def test_x_authorize_request_is_pkce() -> None:
    req = build_x_authorize_request(
        client_id="client-1",
        redirect_uri="https://app.example/cb",
    )
    assert "twitter.com" in req.url
    assert "code_challenge_method=S256" in req.url
