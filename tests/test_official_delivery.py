"""Official Wave 1 delivery: receipts, fail-closed, compensation, oauth metadata."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import db, deliverables
from pipeline.delivery.service import (
    DeliveryError,
    compensate_delivery,
    create_official_delivery,
)
from pipeline.oauth.store import get_oauth_metadata, list_oauth_metadata
from pipeline.publishers.base import AccountConfig, PostBundle
from pipeline.publishers.douyin_api import DouyinApiPublisher, DouyinCredentials
from pipeline.publishers.instagram import InstagramCredentials, InstagramPublisher
from pipeline.publishers.tiktok import TikTokCredentials, TikTokPublisher
from pipeline.publishers.x_api import XApiPublisher
from pipeline.publishers.youtube import YoutubeCredentials, YoutubePublisher

from tests.test_gallery_deliverable import _approve_gallery, _make_gallery

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _image_bundle(tmp_path: Path) -> PostBundle:
    image = tmp_path / "slide.png"
    image.write_bytes(_PNG)
    return PostBundle(
        content_id="c_off",
        title="官方图文",
        body_path=tmp_path / "body.md",
        media_paths=(image,),
        tags=(),
        extra={"description": "fixture"},
    )


def _video_bundle(tmp_path: Path, visibility: str = "private") -> PostBundle:
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16)
    return PostBundle(
        content_id="c_yt",
        title="YouTube fixture",
        body_path=tmp_path / "script.md",
        media_paths=(video,),
        tags=(),
        extra={"description": "fixture", "visibility": visibility},
    )


def test_official_fail_closed_without_user_context(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_off_closed", slides=1, prefix="off", caption="官方", targets=["douyin"])
    _approve_gallery(root, "prj_off_closed", item)
    video = deliverables.create_video(
        "prj_off_closed", title="短视频", script="口播", duration_s=12, aspect="9:16",
        now="2026-08-17T00:04:00+00:00", targets=["douyin"], projects_root=root,
    )
    # Re-approve so snapshot includes the video deliverable.
    _approve_gallery(root, "prj_off_closed", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = DouyinApiPublisher(credentials=DouyinCredentials(access_token="act-test-token"))
    with pytest.raises(DeliveryError) as err:
        create_official_delivery(
            conn, project_id="prj_off_closed", deliverable_id=video.id, actor="lazy",
            adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "dy.json"),
            confirm_token="confirm-1", bundle=_image_bundle(tmp_path), projects_root=root,
        )
    assert err.value.code == "mode_not_allowed"
    assert conn.execute("SELECT COUNT(*) AS n FROM delivery_attempts").fetchone()["n"] == 0


def test_official_requires_confirm_token(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_off_token", slides=1, prefix="tok", caption="确认", targets=["douyin"])
    _approve_gallery(root, "prj_off_token", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = DouyinApiPublisher(
        credentials=DouyinCredentials(
            access_token="act-test-token", open_id="open-1", scopes=("video.create",),
        ),
        http_upload=lambda *a, **k: {"data": {"image_id": "img-1"}},
        http_post=lambda *a, **k: {"data": {"item_id": "item-1"}},
    )
    with pytest.raises(DeliveryError) as err:
        create_official_delivery(
            conn, project_id="prj_off_token", deliverable_id=item.id, actor="lazy",
            adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "dy.json"),
            confirm_token="", bundle=_image_bundle(tmp_path), projects_root=root,
        )
    assert err.value.code == "confirm_required"


def test_no_receipt_is_not_success(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_off_receipt", slides=1, prefix="rcpt", caption="回执", targets=["douyin"])
    _approve_gallery(root, "prj_off_receipt", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = DouyinApiPublisher(
        credentials=DouyinCredentials(
            access_token="act-test-token", open_id="open-1", scopes=("video.create",),
        ),
        http_upload=lambda *a, **k: {"data": {"image_id": "img-1"}},
        http_post=lambda *a, **k: {"data": {"item_id": ""}},
    )
    result = create_official_delivery(
        conn, project_id="prj_off_receipt", deliverable_id=item.id, actor="lazy",
        adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "dy.json"),
        confirm_token="confirm-ok", bundle=_image_bundle(tmp_path), projects_root=root,
    )
    assert result.attempt.outcome == "failure"
    assert result.attempt.platform_post_id is None
    assert result.attempt.mode == "direct"


def test_official_success_writes_receipt_metadata_and_compensate(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_off_ok", slides=1, prefix="ok", caption="成功", targets=["douyin"])
    _approve_gallery(root, "prj_off_ok", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    deleted: list[str] = []

    def upload(*_a, **_k):
        return {"data": {"image_id": "img-1"}}

    def post(url, **kwargs):
        if "delete" in url:
            deleted.append(kwargs["body"]["item_id"])
            return {"data": {"error_code": 0}}
        return {"data": {"item_id": "item-ok-1"}}

    creds_path = tmp_path / "dy.json"
    creds_path.write_text("{}")
    adapter = DouyinApiPublisher(
        credentials=DouyinCredentials(
            access_token="act-test-token", open_id="open-1", scopes=("video.create",),
        ),
        http_upload=upload,
        http_post=post,
    )
    account = AccountConfig(id="main", credentials_path=creds_path)
    result = create_official_delivery(
        conn, project_id="prj_off_ok", deliverable_id=item.id, actor="lazy",
        adapter=adapter, account=account, confirm_token="confirm-ok",
        bundle=_image_bundle(tmp_path), projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.platform_post_id == "item-ok-1"
    meta = get_oauth_metadata(conn, "douyin", "main", "oauth_user")
    assert meta is not None
    assert meta.last4 == "oken"
    assert meta.has_user_context is True
    assert "act-test-token" not in json.dumps([row.__dict__ for row in list_oauth_metadata(conn)])
    compensated = compensate_delivery(
        conn, attempt_id=result.attempt.id, actor="lazy", adapter=adapter,
    )
    assert compensated.attempt.compensation_of_id == result.attempt.id
    assert compensated.attempt.outcome == "success"
    assert deleted == ["item-ok-1"]


def test_youtube_public_without_review_fails(tmp_path):
    root = tmp_path / "projects"
    gallery = _make_gallery(root, "prj_yt", slides=1, prefix="yt", caption="yt")
    video = deliverables.create_video(
        "prj_yt", title="短视频", script="口播", duration_s=12, aspect="9:16",
        now="2026-08-17T00:04:00+00:00", targets=["youtube"], projects_root=root,
    )
    _approve_gallery(root, "prj_yt", gallery)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = YoutubePublisher(
        credentials=YoutubeCredentials(
            access_token="yt-test-token", user_id="ch-1",
            scopes=("youtube.upload",), app_reviewed=False,
        ),
        http_upload=lambda *a, **k: {"id": "should-not-run"},
    )
    result = create_official_delivery(
        conn, project_id="prj_yt", deliverable_id=video.id, actor="lazy",
        adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "yt.json"),
        confirm_token="confirm-yt", bundle=_video_bundle(tmp_path, "public"),
        visibility="public", projects_root=root,
    )
    assert result.attempt.outcome == "failure"
    assert result.attempt.platform_post_id is None
    assert "app review" in (result.attempt.error or "")


def test_youtube_private_success_needs_id_and_url(tmp_path):
    root = tmp_path / "projects"
    gallery = _make_gallery(root, "prj_yt2", slides=1, prefix="yt2", caption="yt2")
    video = deliverables.create_video(
        "prj_yt2", title="短视频", script="口播", duration_s=12, aspect="9:16",
        now="2026-08-17T00:04:00+00:00", targets=["youtube"], projects_root=root,
    )
    _approve_gallery(root, "prj_yt2", gallery)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = YoutubePublisher(
        credentials=YoutubeCredentials(
            access_token="yt-test-token", user_id="ch-1",
            scopes=("youtube.upload",), app_reviewed=False,
        ),
        http_upload=lambda *a, **k: {"id": "ytVid1"},
    )
    result = create_official_delivery(
        conn, project_id="prj_yt2", deliverable_id=video.id, actor="lazy",
        adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "yt.json"),
        confirm_token="confirm-yt", bundle=_video_bundle(tmp_path, "private"),
        projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.platform_post_id == "ytVid1"
    assert result.attempt.platform_url == "https://www.youtube.com/watch?v=ytVid1"


def test_tiktok_fail_closed_without_user_context(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_tt_closed", slides=1, prefix="tt", caption="tt", targets=["tiktok"])
    video = deliverables.create_video(
        "prj_tt_closed", title="短视频", script="口播", duration_s=12, aspect="9:16",
        now="2026-08-17T00:04:00+00:00", targets=["tiktok"], projects_root=root,
    )
    _approve_gallery(root, "prj_tt_closed", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = TikTokPublisher(credentials=TikTokCredentials(access_token="tt-test-token"))
    with pytest.raises(DeliveryError) as err:
        create_official_delivery(
            conn, project_id="prj_tt_closed", deliverable_id=video.id, actor="lazy",
            adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "tt.json"),
            confirm_token="confirm-1", bundle=_video_bundle(tmp_path), projects_root=root,
        )
    assert err.value.code == "mode_not_allowed"


def test_tiktok_inbox_success_writes_publish_id(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_tt_ok", slides=1, prefix="tto", caption="tt", targets=["tiktok"])
    video = deliverables.create_video(
        "prj_tt_ok", title="短视频", script="口播", duration_s=12, aspect="9:16",
        now="2026-08-17T00:04:00+00:00", targets=["tiktok"], projects_root=root,
    )
    _approve_gallery(root, "prj_tt_ok", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)

    def post(url, **_kwargs):
        assert "inbox" in url
        return {"data": {"publish_id": "v_pub_ok", "upload_url": "https://upload.example/t"}}

    adapter = TikTokPublisher(
        credentials=TikTokCredentials(
            access_token="tt-test-token", open_id="open-1",
            scopes=("video.publish",), app_reviewed=False,
        ),
        http_post=post,
        http_upload=lambda *a, **k: {},
    )
    result = create_official_delivery(
        conn, project_id="prj_tt_ok", deliverable_id=video.id, actor="lazy",
        adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "tt.json"),
        confirm_token="confirm-tt", bundle=_video_bundle(tmp_path), projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.platform_post_id == "v_pub_ok"


def test_instagram_fail_closed_without_user_context(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_ig_closed", slides=1, prefix="ig", caption="ig", targets=["instagram"])
    _approve_gallery(root, "prj_ig_closed", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = InstagramPublisher(credentials=InstagramCredentials(access_token="ig-test-token"))
    with pytest.raises(DeliveryError) as err:
        create_official_delivery(
            conn, project_id="prj_ig_closed", deliverable_id=item.id, actor="lazy",
            adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "ig.json"),
            confirm_token="confirm-1",
            bundle=PostBundle(
                content_id="c_ig", title="ig", body_path=tmp_path / "b.md",
                media_paths=(), tags=(), extra={"media_url": "https://cdn.example.com/a.jpg"},
            ),
            projects_root=root,
        )
    assert err.value.code == "mode_not_allowed"


def test_instagram_success_needs_media_id(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_ig_ok", slides=1, prefix="igo", caption="ig", targets=["instagram"])
    _approve_gallery(root, "prj_ig_ok", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)

    def post(url, **kwargs):
        if kwargs.get("method") == "GET":
            return {"id": "ig-media-1"}
        if url.endswith("/media_publish"):
            return {"id": "ig-media-1"}
        return {"id": "ig-ctr-1"}

    adapter = InstagramPublisher(
        credentials=InstagramCredentials(
            access_token="ig-test-token", user_id="178414000",
            scopes=("instagram_content_publish",), app_reviewed=True,
        ),
        http_post=post,
    )
    bundle = PostBundle(
        content_id="c_ig", title="ig", body_path=tmp_path / "b.md",
        media_paths=(), tags=(), extra={"caption": "hi", "media_url": "https://cdn.example.com/a.jpg"},
    )
    result = create_official_delivery(
        conn, project_id="prj_ig_ok", deliverable_id=item.id, actor="lazy",
        adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "ig.json"),
        confirm_token="confirm-ig", bundle=bundle, projects_root=root,
    )
    assert result.attempt.outcome == "success"
    assert result.attempt.platform_post_id == "ig-media-1"
    assert result.attempt.platform_url is None


def test_x_app_only_is_not_official_success(tmp_path):
    root = tmp_path / "projects"
    item = _make_gallery(root, "prj_x_closed", slides=1, prefix="xx", caption="x", targets=["x"])
    _approve_gallery(root, "prj_x_closed", item)
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    adapter = XApiPublisher(bearer_token="app-only")
    with pytest.raises(DeliveryError) as err:
        create_official_delivery(
            conn, project_id="prj_x_closed", deliverable_id=item.id, actor="lazy",
            adapter=adapter, account=AccountConfig(id="main", credentials_path=tmp_path / "x.json"),
            confirm_token="confirm-1", bundle=_image_bundle(tmp_path), projects_root=root,
        )
    assert err.value.code == "mode_not_allowed"
