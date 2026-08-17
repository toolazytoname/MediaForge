"""OAuth 2.0 PKCE primitives. Never persist tokens here."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str
    method: str = "S256"


@dataclass(frozen=True)
class OAuthAuthorizeRequest:
    url: str
    state: str
    verifier: str
    challenge: str
    scopes: tuple[str, ...]
    client_id: str
    redirect_uri: str


def generate_pkce_pair(*, entropy: bytes | None = None) -> PkcePair:
    raw = entropy if entropy is not None else os.urandom(32)
    verifier = _urlsafe(raw)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return PkcePair(verifier=verifier, challenge=_urlsafe(digest))


def new_oauth_state(*, entropy: bytes | None = None) -> str:
    if entropy is not None:
        return _urlsafe(entropy)
    return secrets.token_urlsafe(24)


def build_authorize_request(
    authorize_url: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: Iterable[str],
    extra: dict[str, str] | None = None,
    state: str | None = None,
    pkce: PkcePair | None = None,
) -> OAuthAuthorizeRequest:
    if not client_id or not redirect_uri:
        raise ValueError("client_id and redirect_uri are required")
    pair = pkce or generate_pkce_pair()
    scope_tuple = tuple(str(item) for item in scopes if str(item).strip())
    csrf = state or new_oauth_state()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scope_tuple),
        "state": csrf,
        "code_challenge": pair.challenge,
        "code_challenge_method": pair.method,
    }
    if extra:
        params.update(extra)
    joiner = "&" if "?" in authorize_url else "?"
    return OAuthAuthorizeRequest(
        url=f"{authorize_url}{joiner}{urlencode(params)}",
        state=csrf,
        verifier=pair.verifier,
        challenge=pair.challenge,
        scopes=scope_tuple,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )


def _urlsafe(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


__all__ = [
    "OAuthAuthorizeRequest",
    "PkcePair",
    "build_authorize_request",
    "generate_pkce_pair",
    "new_oauth_state",
]
