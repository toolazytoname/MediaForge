import pytest

from pipeline.publishers import get_adapter
from pipeline.publishers.capability_registry import (
    P3_PLATFORMS,
    capabilities_payload,
    effective_delivery,
    get_capability,
    official_publish_platforms,
    platforms_for,
    target_platforms_for,
)
from pipeline.publishers.capabilities import KNOWN_OUTCOMES
from pipeline.publishers.douyin_api import DouyinApiPublisher, DouyinCredentials
from pipeline.publishers.instagram import InstagramPublisher
from pipeline.publishers.tiktok import TikTokPublisher
from pipeline.publishers.wechat_mp import WechatMpPublisher
from pipeline.publishers.x_api import XApiPublisher
from pipeline.publishers.youtube import YoutubePublisher


def test_registry_covers_five_adapters_and_wechat_hides_direct():
    for name in ("wechat_mp", "toutiao", "xiaohongshu", "douyin", "x", "youtube", "tiktok", "instagram"):
        cap = get_capability(name)
        assert cap.receipts.unknown_is_failure is True
        assert "unknown" in KNOWN_OUTCOMES
    wechat = get_capability("wechat_mp")
    assert wechat.delivery.direct is False
    assert wechat.delivery.draft is True
    toutiao = get_capability("toutiao")
    assert toutiao.delivery.direct is False
    assert toutiao.delivery.export is True
    assert toutiao.delivery.draft is False
    xhs = get_capability("xiaohongshu")
    assert xhs.delivery.direct is False
    assert xhs.delivery.draft is False
    assert xhs.delivery.export is True
    assert xhs.limits.min_images == 1
    assert xhs.limits.max_images == 9
    douyin = get_capability("douyin")
    assert douyin.auth.kind == "oauth_user"
    assert douyin.review.requires_app_review is True
    assert "video" in douyin.formats
    assert "gallery" in douyin.formats
    youtube = get_capability("youtube")
    assert youtube.auth.kind == "oauth_user"
    assert youtube.review.requires_app_review is True
    assert youtube.review.default_visibility == "private"
    tiktok = get_capability("tiktok")
    assert tiktok.auth.kind == "oauth_user"
    assert tiktok.review.requires_app_review is True
    assert "video.publish" in tiktok.auth.required_scopes
    instagram = get_capability("instagram")
    assert instagram.auth.kind == "oauth_user"
    assert instagram.review.requires_app_review is True
    assert "instagram_content_publish" in instagram.auth.required_scopes
    assert set(platforms_for(kind="article")) >= {"wechat_mp", "toutiao", "x"}
    assert set(platforms_for(kind="gallery")) >= {"xiaohongshu", "douyin", "instagram"}
    assert "youtube" in platforms_for(kind="video")
    assert "tiktok" in platforms_for(kind="video")
    assert wechat.official_api is True
    assert toutiao.official_api is False
    assert toutiao.lane == "export"
    assert xhs.official_api is False
    assert xhs.lane == "assisted"


def test_x_without_user_context_direct_is_false():
    adapter = XApiPublisher(bearer_token="dummy")
    assert effective_delivery("x", adapter).direct is False
    assert WechatMpPublisher(app_id="id", app_secret="secret").capabilities().direct is False
    assert effective_delivery("douyin").direct is False
    assert effective_delivery("youtube").direct is False
    assert effective_delivery("tiktok").direct is False
    assert effective_delivery("instagram").direct is False
    assert DouyinApiPublisher(credentials=DouyinCredentials(access_token="t")).capabilities().direct is False
    assert YoutubePublisher(access_token="t").capabilities().direct is False
    assert TikTokPublisher(access_token="t").capabilities().direct is False
    assert InstagramPublisher(access_token="t").capabilities().direct is False


def test_p3_catalog_is_export_only_and_has_no_adapter():
    official = official_publish_platforms()
    assert official == frozenset({"douyin", "youtube", "tiktok", "instagram", "x"})
    for name in P3_PLATFORMS:
        cap = get_capability(name)
        assert cap.official_api is False
        assert cap.lane == "export"
        assert cap.delivery.direct is False
        assert cap.delivery.draft is False
        assert cap.delivery.export is True
        assert cap.adapter == ""
        assert cap.receipts.unknown_is_failure is True
        assert effective_delivery(name).direct is False
        assert name not in official
        with pytest.raises(ValueError, match="unknown"):
            get_adapter(name, account=None, config=None)
    assert "bilibili" in platforms_for(kind="video")
    assert "shipinhao" in platforms_for(kind="video")
    assert "weibo" not in target_platforms_for(kind="gallery")
    assert "xiaohongshu" in target_platforms_for(kind="gallery")
    payload = {item["platform"]: item for item in capabilities_payload()}
    for name in P3_PLATFORMS:
        assert payload[name]["can_claim_direct"] is False
        assert payload[name]["official_api"] is False
        assert "可直发" not in payload[name]["ui"]["confirm_copy"]
