"""Instagram Professional container + media_publish: fail-closed, public URL, receipts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers.base import AccountConfig, PostBundle, PublishError
from pipeline.publishers.instagram import (
    InstagramCredentials,
    InstagramPublisher,
    is_public_https_url,
    load_instagram_credential_set,
    permalink,
)


def _account() -> AccountConfig:
    return AccountConfig(id="main", credentials_path=Path("secrets/instagram_main.json"))


def _bundle(tmp_path: Path, url: str | None = "https://cdn.example.com/slide.jpg") -> PostBundle:
    extra = {"caption": "fixture"}
    if url:
        extra["media_url"] = url
    return PostBundle(
        content_id="c_ig",
        title="IG fixture",
        body_path=tmp_path / "caption.md",
        media_paths=(),
        tags=(),
        extra=extra,
    )


def test_public_url_rejects_local_and_http() -> None:
    assert is_public_https_url("https://cdn.example.com/a.jpg") is True
    assert is_public_https_url("http://cdn.example.com/a.jpg") is False
    assert is_public_https_url("https://localhost/a.jpg") is False
    assert is_public_https_url("https://127.0.0.1/a.jpg") is False
    assert is_public_https_url("file:///tmp/a.jpg") is False


def test_missing_user_context_fail_closes(tmp_path: Path) -> None:
    adapter = InstagramPublisher(access_token="ig-test-token")
    assert adapter.capabilities().direct is False
    with pytest.raises(PublishError, match="user-context"):
        adapter.publish(_bundle(tmp_path), _account(), dry_run=False)


def test_unreviewed_cannot_claim_public(tmp_path: Path) -> None:
    creds = InstagramCredentials(
        access_token="ig-test-token",
        user_id="178414000",
        scopes=("instagram_content_publish",),
        app_reviewed=False,
    )
    adapter = InstagramPublisher(
        credentials=creds,
        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no publish")),
    )
    assert adapter.capabilities().direct is False
    assert "app review" in adapter.capabilities().detail.lower()
    with pytest.raises(PublishError, match="app review"):
        adapter.publish(_bundle(tmp_path), _account(), dry_run=False)


def test_missing_public_url_fail_closes(tmp_path: Path) -> None:
    creds = InstagramCredentials(
        access_token="ig-test-token",
        user_id="178414000",
        scopes=("instagram_content_publish",),
        app_reviewed=True,
    )
    adapter = InstagramPublisher(
        credentials=creds,
        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no publish")),
    )
    with pytest.raises(PublishError, match="HTTPS"):
        adapter.publish(_bundle(tmp_path, None), _account(), dry_run=False)


def test_container_without_id_is_failure(tmp_path: Path) -> None:
    creds = InstagramCredentials(
        access_token="ig-test-token",
        user_id="178414000",
        scopes=("instagram_content_publish",),
        app_reviewed=True,
    )
    adapter = InstagramPublisher(credentials=creds, http_post=lambda *a, **k: {})
    with pytest.raises(PublishError, match="no id"):
        adapter.publish(_bundle(tmp_path), _account(), dry_run=False)


def test_permalink_ignores_media_id() -> None:
    assert permalink("media-ok-1") is None
    assert permalink({"id": "media-ok-1"}) is None
    assert permalink({"permalink": "https://www.instagram.com/p/REALSHORT/"}) == (
        "https://www.instagram.com/p/REALSHORT/"
    )


def test_publish_success_and_compensate(tmp_path: Path) -> None:
    creds = InstagramCredentials(
        access_token="ig-test-token",
        user_id="178414000",
        scopes=("instagram_content_publish", "instagram_basic"),
        app_reviewed=True,
    )
    deleted: list[str] = []

    def post(url, **kwargs):
        if kwargs.get("method") == "DELETE":
            deleted.append(url.rsplit("/", 1)[-1])
            return {}
        if kwargs.get("method") == "GET":
            return {"id": "media-ok-1"}
        if url.endswith("/media_publish"):
            assert kwargs["body"]["creation_id"] == "ctr-1"
            return {"id": "media-ok-1"}
        assert url.endswith("/media")
        assert kwargs["body"]["image_url"] == "https://cdn.example.com/slide.jpg"
        return {"id": "ctr-1"}

    adapter = InstagramPublisher(credentials=creds, http_post=post)
    result = adapter.publish(_bundle(tmp_path), _account(), dry_run=False)
    assert result.platform_post_id == "media-ok-1"
    assert result.url is None
    assert result.url != "https://www.instagram.com/p/media-ok-1/"
    adapter.compensate(result.platform_post_id)
    assert deleted == ["media-ok-1"]


def test_publish_uses_graph_permalink_field(tmp_path: Path) -> None:
    creds = InstagramCredentials(
        access_token="ig-test-token",
        user_id="178414000",
        scopes=("instagram_content_publish",),
        app_reviewed=True,
    )

    def post(url, **kwargs):
        if kwargs.get("method") == "GET":
            assert url.endswith("/media-ok-2")
            return {"id": "media-ok-2", "permalink": "https://www.instagram.com/p/REALSHORT/"}
        if url.endswith("/media_publish"):
            return {"id": "media-ok-2"}
        return {"id": "ctr-2"}

    adapter = InstagramPublisher(credentials=creds, http_post=post)
    result = adapter.publish(_bundle(tmp_path), _account(), dry_run=False)
    assert result.platform_post_id == "media-ok-2"
    assert result.url == "https://www.instagram.com/p/REALSHORT/"


def test_load_credentials_requires_token(tmp_path: Path) -> None:
    path = tmp_path / "ig.json"
    path.write_text(json.dumps({"user_id": "1"}))
    assert load_instagram_credential_set(path).has_user_context is False
