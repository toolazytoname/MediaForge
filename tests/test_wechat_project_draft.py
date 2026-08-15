from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import projects as project_store
from pipeline import variants as variant_store
from pipeline import visuals
from pipeline.publishers.base import PublishError
from pipeline.publishers.wechat_mp import WechatMpPublisher
from pipeline.wechat_project_draft import WechatDraftError, send_project_wechat_draft


NOW = "2026-08-14T12:00:00+00:00"


def _project(root: Path) -> None:
    project_store.create_project(
        title="在这个AI时代，我该如何自处",
        idea="一团想法",
        audience="创作者",
        goal="草稿",
        voice="克制",
        autonomy="draft",
        now=NOW,
        project_id="prj_draft01",
        projects_root=root,
    )
    from pipeline import master_documents
    master_documents.save_manual(
        "prj_draft01",
        title="在这个AI时代，我该如何自处",
        body="## 问题\n\n正文。",
        now=NOW,
        projects_root=root,
    )


def _seed_variant_and_cover(root: Path, *, with_cover: bool = True, with_inline: bool = True) -> None:
    plan = visuals.save_plan(
        "prj_draft01",
        bible={"风格": "克制"},
        slots=[
            {"id": "vsl_cover1", "purpose": "封面", "paragraph_anchor": None, "direction": "封面", "aspect_ratio": "16:9"},
            {"id": "vsl_open1", "purpose": "正文插图一", "paragraph_anchor": "问题", "direction": "插图", "aspect_ratio": "16:9"},
        ],
        projects_root=root,
    )
    assets_dir = root / "prj_draft01" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    cover = assets_dir / "vas_cover01.png"
    inline = assets_dir / "vas_open01.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"c" * 80)
    inline.write_bytes(b"\x89PNG\r\n\x1a\n" + b"i" * 80)
    if with_cover:
        asset = visuals.record_asset(
            "prj_draft01", slot_id="vsl_cover1", prompt="cover", model="test", size="16:9",
            cost_usd=0.01, now=NOW, file_path="assets/vas_cover01.png", status="candidate",
            projects_root=root, asset_id="vas_cover01",
        )
        visuals.select_asset("prj_draft01", asset.id, reason="cover", rating=None, projects_root=root)
    inline_asset = visuals.record_asset(
        "prj_draft01", slot_id="vsl_open1", prompt="inline", model="test", size="16:9",
        cost_usd=0.01, now=NOW, file_path="assets/vas_open01.png", status="candidate",
        projects_root=root, asset_id="vas_open01",
    )
    visuals.select_asset("prj_draft01", inline_asset.id, reason="inline", rating=None, projects_root=root)
    body = "## 问题\n\n正文。"
    if with_inline:
        body = f"![封面](/output/projects/prj_draft01/assets/vas_cover01.png)\n\n{body}\n\n![插图](/output/projects/prj_draft01/assets/vas_open01.png)"
    variant_store.create_adapted(
        "prj_draft01",
        "wechat_mp",
        title="在这个AI时代，我该如何自处",
        summary="效率提高了，节奏却更快了。",
        body=body,
        now=NOW,
        projects_root=root,
    )
    assert plan.project_id == "prj_draft01"


def _publisher(calls: list[str]) -> WechatMpPublisher:
    def http_get(url, *, params, timeout=30.0):
        calls.append(f"GET {url}")
        return {"access_token": "tok_test", "expires_in": 7200}

    def http_post(url, *, params, json_body, timeout=30.0):
        calls.append(f"POST {url}")
        assert "freepublish" not in url
        assert "mass/send" not in url
        if url.endswith("/draft/add"):
            article = json_body["articles"][0]
            assert article["title"] == "在这个AI时代，我该如何自处"
            assert "mmbiz.qpic.cn" in article["content"]
            return {"media_id": "media_draft_1"}
        raise AssertionError(url)

    def http_upload(url, *, params, file_path, timeout=60.0):
        calls.append(f"UPLOAD {url} {Path(file_path).name}")
        if "add_material" in url:
            return {"media_id": "thumb_1"}
        if "uploadimg" in url:
            return {"url": f"https://mmbiz.qpic.cn/{Path(file_path).name}"}
        raise AssertionError(url)

    return WechatMpPublisher(
        app_id="wx_test",
        app_secret="secret_test",
        http_get=http_get,
        http_post=http_post,
        http_upload=http_upload,
    )


def test_send_project_draft_uploads_images_and_only_creates_a_draft(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    _seed_variant_and_cover(root)
    calls: list[str] = []

    receipt = send_project_wechat_draft(
        "prj_draft01",
        projects_root=root,
        publisher=_publisher(calls),
    )

    assert receipt.media_id == "media_draft_1"
    assert receipt.published is False
    assert receipt.destination == "wechat_draft"
    assert any("draft/add" in item for item in calls)
    assert not any("freepublish" in item or "mass/send" in item for item in calls)
    saved = json.loads((root / "prj_draft01" / "wechat_draft.json").read_text(encoding="utf-8"))
    assert saved["media_id"] == "media_draft_1"
    assert saved["published"] is False


def test_send_requires_wechat_variant_and_cover(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    with pytest.raises(WechatDraftError, match="微信稿"):
        send_project_wechat_draft("prj_draft01", projects_root=root, publisher=_publisher([]))

    _seed_variant_and_cover(root, with_cover=False)
    with pytest.raises(WechatDraftError, match="封面"):
        send_project_wechat_draft("prj_draft01", projects_root=root, publisher=_publisher([]))


def test_wechat_api_error_is_visible(tmp_path):
    root = tmp_path / "projects"
    _project(root)
    _seed_variant_and_cover(root)

    def http_get(url, *, params, timeout=30.0):
        return {"access_token": "tok", "expires_in": 7200}

    def http_post(url, *, params, json_body, timeout=30.0):
        raise PublishError("wechat_mp API error 48001: api unauthorized")

    def http_upload(url, *, params, file_path, timeout=60.0):
        if "uploadimg" in url:
            return {"url": "https://mmbiz.qpic.cn/img"}
        return {"media_id": "thumb_1"}

    publisher = WechatMpPublisher(
        app_id="wx_test",
        app_secret="secret_test",
        http_get=http_get,
        http_post=http_post,
        http_upload=http_upload,
    )
    with pytest.raises(WechatDraftError, match="48001"):
        send_project_wechat_draft("prj_draft01", projects_root=root, publisher=publisher)
