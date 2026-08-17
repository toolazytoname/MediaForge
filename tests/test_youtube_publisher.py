"""YouTube videos.insert: fail-closed, private/unlisted, no public without review."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers.base import AccountConfig, PostBundle, PublishError
from pipeline.publishers.youtube import YoutubeCredentials, YoutubePublisher, load_youtube_credential_set


def _account() -> AccountConfig:
    return AccountConfig(id="main", credentials_path=Path("secrets/youtube_main.json"))


def _bundle(tmp_path: Path, visibility: str = "private") -> PostBundle:
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    return PostBundle(
        content_id="c_yt",
        title="YouTube fixture",
        body_path=tmp_path / "script.md",
        media_paths=(video,),
        tags=("ai",),
        extra={"description": "fixture", "visibility": visibility},
    )


def test_missing_user_context_fail_closes(tmp_path: Path) -> None:
    adapter = YoutubePublisher(access_token="yt-test-token")
    assert adapter.capabilities().direct is False
    assert "Public" in adapter.capabilities().detail or "public" in adapter.capabilities().detail.lower()
    with pytest.raises(PublishError, match="user-context"):
        adapter.publish(_bundle(tmp_path), _account(), dry_run=False)


def test_unreviewed_app_cannot_claim_public(tmp_path: Path) -> None:
    creds = YoutubeCredentials(
        access_token="yt-test-token",
        user_id="channel-1",
        scopes=("https://www.googleapis.com/auth/youtube.upload",),
        app_reviewed=False,
    )
    adapter = YoutubePublisher(credentials=creds)
    assert adapter.capabilities().direct is True
    assert "private/unlisted" in adapter.capabilities().detail
    with pytest.raises(PublishError, match="app review"):
        adapter.publish(_bundle(tmp_path, "public"), _account(), dry_run=False)


def test_private_upload_requires_video_id(tmp_path: Path) -> None:
    creds = YoutubeCredentials(
        access_token="yt-test-token",
        user_id="channel-1",
        scopes=("youtube.upload",),
        app_reviewed=False,
    )

    def upload(*_args, **kwargs):
        assert kwargs["metadata"]["status"]["privacyStatus"] == "private"
        return {"kind": "youtube#video"}

    adapter = YoutubePublisher(credentials=creds, http_upload=upload)
    with pytest.raises(PublishError, match="no id"):
        adapter.publish(_bundle(tmp_path), _account(), dry_run=False)


def test_unlisted_success_and_compensate(tmp_path: Path) -> None:
    creds = YoutubeCredentials(
        access_token="yt-test-token",
        user_id="channel-1",
        scopes=("https://www.googleapis.com/auth/youtube.upload",),
        app_reviewed=False,
    )
    deleted: list[str] = []

    def upload(*_args, **kwargs):
        assert kwargs["metadata"]["status"]["privacyStatus"] == "unlisted"
        return {"id": "vidFixture1"}

    def delete(*_args, **kwargs):
        deleted.append(kwargs["params"]["id"])
        return {"deleted": True}

    adapter = YoutubePublisher(credentials=creds, http_upload=upload, http_delete=delete)
    result = adapter.publish(_bundle(tmp_path, "unlisted"), _account(), dry_run=False)
    assert result.platform_post_id == "vidFixture1"
    assert result.url == "https://www.youtube.com/watch?v=vidFixture1"
    adapter.compensate(result.platform_post_id)
    assert deleted == ["vidFixture1"]


def test_load_credentials_requires_token(tmp_path: Path) -> None:
    path = tmp_path / "yt.json"
    path.write_text(json.dumps({"user_id": "c1"}))
    with pytest.raises(ValueError, match="access_token"):
        load_youtube_credential_set(path)
