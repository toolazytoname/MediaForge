"""Official Douyin OAuth adapter: fail-closed, receipts, image/video fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.publishers.base import AccountConfig, PostBundle, PublishError
from pipeline.publishers.douyin_api import (
    DouyinApiPublisher,
    DouyinCredentials,
    load_douyin_credential_set,
)


def _account() -> AccountConfig:
    return AccountConfig(id="main", credentials_path=Path("secrets/douyin_main.json"))


def _video_bundle(tmp_path: Path) -> PostBundle:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    return PostBundle(
        content_id="c_dy",
        title="官方视频",
        body_path=tmp_path / "douyin.md",
        media_paths=(video,),
        tags=("#AI",),
        extra={"description": "夹具描述"},
    )


def _image_bundle(tmp_path: Path) -> PostBundle:
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    return PostBundle(
        content_id="c_img",
        title="官方图文",
        body_path=tmp_path / "douyin.md",
        media_paths=(image,),
        tags=(),
        extra={"description": "图文夹具"},
    )


def test_cookie_dump_has_no_user_context(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps({"cookies": [{"name": "sessionid", "value": "x"}]}))
    creds = load_douyin_credential_set(path)
    assert creds.has_user_context is False


def test_video_create_scope_enables_user_context(tmp_path: Path) -> None:
    path = tmp_path / "oauth.json"
    path.write_text(json.dumps({
        "access_token": "act-test-token",
        "open_id": "open-1",
        "scopes": ["video.create.bind"],
    }))
    assert load_douyin_credential_set(path).has_user_context is True


def test_missing_user_context_hides_direct_and_fail_closes(tmp_path: Path) -> None:
    adapter = DouyinApiPublisher(credentials=DouyinCredentials(access_token="act-test-token"))
    assert adapter.capabilities().direct is False
    with pytest.raises(PublishError, match="user-context"):
        adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)


def test_dry_run_skips_http_without_user_context(tmp_path: Path) -> None:
    calls: list[str] = []

    def boom(*_args, **_kwargs):
        calls.append("hit")
        raise AssertionError("network")

    adapter = DouyinApiPublisher(
        credentials=DouyinCredentials(access_token="act-test-token"),
        http_post=boom,
        http_upload=boom,
    )
    result = adapter.publish(_video_bundle(tmp_path), _account(), dry_run=True)
    assert result.platform_post_id == "dry-douyin-video"
    assert calls == []


def test_video_create_requires_item_id(tmp_path: Path) -> None:
    creds = DouyinCredentials(
        access_token="act-test-token", open_id="open-1", scopes=("video.create",),
    )

    def upload(*_args, **_kwargs):
        return {"data": {"error_code": 0, "video_id": "vid-1"}}

    def create(*_args, **_kwargs):
        return {"data": {"error_code": 0, "item_id": ""}}

    adapter = DouyinApiPublisher(credentials=creds, http_post=create, http_upload=upload)
    with pytest.raises(PublishError, match="item_id"):
        adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)


def test_video_create_success_and_compensate(tmp_path: Path) -> None:
    creds = DouyinCredentials(
        access_token="act-test-token", open_id="open-1", scopes=("video.create",),
    )
    deleted: list[str] = []

    def upload(*_args, **_kwargs):
        return {"data": {"error_code": 0, "video_id": "vid-1"}}

    def post(url, **kwargs):
        if "delete" in url:
            deleted.append(kwargs["body"]["item_id"])
            return {"data": {"error_code": 0}}
        return {"data": {"error_code": 0, "item_id": "item-fixture-1"}}

    adapter = DouyinApiPublisher(credentials=creds, http_post=post, http_upload=upload)
    assert adapter.capabilities().direct is True
    result = adapter.publish(_video_bundle(tmp_path), _account(), dry_run=False)
    assert result.platform_post_id == "item-fixture-1"
    adapter.compensate(result.platform_post_id)
    assert deleted == ["item-fixture-1"]


def test_image_create_success(tmp_path: Path) -> None:
    creds = DouyinCredentials(
        access_token="act-test-token", open_id="open-1", scopes=("video.create",),
    )

    def upload(*_args, **_kwargs):
        return {"data": {"error_code": 0, "image_id": "img-1"}}

    def create(*_args, **_kwargs):
        return {"data": {"error_code": 0, "item_id": "item-img-1"}}

    adapter = DouyinApiPublisher(credentials=creds, http_post=create, http_upload=upload)
    result = adapter.publish(_image_bundle(tmp_path), _account(), dry_run=False)
    assert result.platform_post_id == "item-img-1"
