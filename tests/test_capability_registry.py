from pipeline.publishers.capability_registry import (
    effective_delivery,
    get_capability,
    platforms_for,
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
