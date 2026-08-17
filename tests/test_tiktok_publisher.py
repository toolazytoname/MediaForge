"""TikTok Content Posting API: fail-closed, inbox vs direct, receipts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers.base import AccountConfig, PostBundle, PublishError
from pipeline.publishers.tiktok import TikTokCredentials, TikTokPublisher, load_tiktok_credential_set


def _account() -> AccountConfig:
    return AccountConfig(id="main", credentials_path=Path("secrets/tiktok_main.json"))


def _video_bundle(tmp_path: Path, visibility: str = "private") -> PostBundle:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    return PostBundle(
        content_id="c_tt",
        title="TikTok fixture",
        body_path=tmp_path / "caption.md",
        media_paths=(video,),
        tags=(),
        extra={"description": "fixture", "visibility": visibility},
    )


def test_missing_user_context_fail_closes(tmp_path: Path) -> None:
    adapter = TikTokPublisher(access_token="tt-test-token")
    assert adapter.capabilities().direct is False
    assert "public" in adapter.capabilities().detail.lower()
    with pytest.raises(PublishError, match="user-context"):
        adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)


def test_unreviewed_cannot_claim_public_direct(tmp_path: Path) -> None:
    creds = TikTokCredentials(
        access_token="tt-test-token",
        open_id="open-1",
        scopes=("video.publish",),
        app_reviewed=False,
    )
    adapter = TikTokPublisher(
        credentials=creds,
        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no public")),
    )
    assert adapter.capabilities().direct is True
    assert "Inbox" in adapter.capabilities().detail
    with pytest.raises(PublishError, match="app review"):
        adapter.publish(_video_bundle(tmp_path, "public"), _account(), dry_run=False)


def test_unreviewed_inbox_requires_publish_id(tmp_path: Path) -> None:
    creds = TikTokCredentials(
        access_token="tt-test-token",
        open_id="open-1",
        scopes=("video.publish",),
        app_reviewed=False,
    )
    calls: list[str] = []

    def post(url, **_kwargs):
        calls.append(url)
        return {"data": {"upload_url": "https://upload.example/inbox"}}

    adapter = TikTokPublisher(credentials=creds, http_post=post, http_upload=lambda *a, **k: {})
    with pytest.raises(PublishError, match="publish_id"):
        adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)
    assert any("inbox" in url for url in calls)


def test_unreviewed_inbox_success_and_compensate(tmp_path: Path) -> None:
    creds = TikTokCredentials(
        access_token="tt-test-token",
        open_id="open-1",
        scopes=("video.publish",),
        app_reviewed=False,
    )
    deleted: list[str] = []

    def post(url, **kwargs):
        if "cancel" in url:
            deleted.append(kwargs["body"]["publish_id"])
            return {"error": {"code": "ok"}}
        assert "inbox" in url
        return {"data": {"publish_id": "v_pub_inbox_1", "upload_url": "https://upload.example/1"}}

    uploaded: list[str] = []

    def upload(url, **_kwargs):
        uploaded.append(url)
        return {"uploaded": True}

    adapter = TikTokPublisher(credentials=creds, http_post=post, http_upload=upload)
    result = adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)
    assert result.platform_post_id == "v_pub_inbox_1"
    assert result.url is None
    raw = json.loads(result.raw_response)
    assert raw["mode"] == "inbox"
    assert raw["user_continues_in_app"] is True
    assert raw["public_direct_claimed"] is False
    assert uploaded == ["https://upload.example/1"]
    adapter.compensate(result.platform_post_id)
    assert deleted == ["v_pub_inbox_1"]


def test_reviewed_direct_post_uses_video_init(tmp_path: Path) -> None:
    creds = TikTokCredentials(
        access_token="tt-test-token",
        open_id="open-1",
        scopes=("video.publish",),
        app_reviewed=True,
    )

    def post(url, **kwargs):
        assert "post/publish/video/init" in url
        assert kwargs["body"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
        return {"data": {"publish_id": "v_pub_direct_1", "upload_url": "https://upload.example/d"}}

    adapter = TikTokPublisher(
        credentials=creds, http_post=post, http_upload=lambda *a, **k: {},
    )
    result = adapter.publish(_video_bundle(tmp_path, "public"), _account(), dry_run=False)
    assert result.platform_post_id == "v_pub_direct_1"


def test_load_credentials_cookie_dump_has_no_user_context(tmp_path: Path) -> None:
    path = tmp_path / "tt.json"
    path.write_text(json.dumps({"cookies": [{"name": "sessionid", "value": "x"}]}))
    assert load_tiktok_credential_set(path).has_user_context is False
