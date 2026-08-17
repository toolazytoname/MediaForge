from pipeline.publishers.capability_registry import (
    effective_delivery,
    get_capability,
    platforms_for,
)
from pipeline.publishers.capabilities import KNOWN_OUTCOMES
from pipeline.publishers.wechat_mp import WechatMpPublisher
from pipeline.publishers.x_api import XApiPublisher


def test_registry_covers_five_adapters_and_wechat_hides_direct():
    for name in ("wechat_mp", "toutiao", "xiaohongshu", "douyin", "x"):
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
    assert set(platforms_for(kind="article")) >= {"wechat_mp", "toutiao", "x"}
    assert platforms_for(kind="gallery") == ("xiaohongshu",)


def test_x_without_user_context_direct_is_false():
    adapter = XApiPublisher(bearer_token="dummy")
    assert effective_delivery("x", adapter).direct is False
    assert WechatMpPublisher(app_id="id", app_secret="secret").capabilities().direct is False
