"""Product-level CapabilityRegistry (LAZY-40 / RFC §5.3).

Adapter.capabilities() may tighten these flags but must not loosen them.
Old Publish Center still reads adapter/platform defaults in capabilities.py.
Project delivery only exposes the product-effective modes from this registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from pipeline.publishers.capabilities import (
    KNOWN_OUTCOMES,
    MODE_DIRECT,
    MODE_DRAFT,
    MODE_EXPORT,
    MODE_PREVIEW,
    AdapterCapabilities,
    capabilities_from_adapter,
)

KIND_ARTICLE = "article"
KIND_GALLERY = "gallery"
KIND_VIDEO = "video"
KNOWN_KINDS = (KIND_ARTICLE, KIND_GALLERY, KIND_VIDEO)

# Platforms without an official image-count rule inherit these and they are
# written onto Capability.limits so UI/approval/export read one place.
DEFAULT_GALLERY_MIN_IMAGES = 1
DEFAULT_GALLERY_MAX_IMAGES = 9

LANE_OFFICIAL = "official"
LANE_ASSISTED = "assisted"
LANE_EXPORT = "export"

REGISTRY_PLATFORMS = (
    "wechat_mp", "toutiao", "xiaohongshu", "douyin",
    "x", "youtube", "tiktok", "instagram",
    "bilibili", "shipinhao", "weibo", "kuaishou", "zhihu",
)

# Audit P3 / long-tail: catalog only. No official creator publish API.
P3_PLATFORMS = ("bilibili", "shipinhao", "weibo", "kuaishou", "zhihu")


@dataclass(frozen=True)
class DeliveryFlags:
    preview: bool
    export: bool
    draft: bool
    direct: bool

    def enabled_modes(self) -> tuple[str, ...]:
        flags = {
            MODE_PREVIEW: self.preview,
            MODE_EXPORT: self.export,
            MODE_DRAFT: self.draft,
            MODE_DIRECT: self.direct,
        }
        return tuple(mode for mode in (MODE_PREVIEW, MODE_EXPORT, MODE_DRAFT, MODE_DIRECT) if flags[mode])


@dataclass(frozen=True)
class AuthSpec:
    kind: str
    required_scopes: tuple[str, ...]
    user_context_required: bool
    secret_ref_pattern: str


@dataclass(frozen=True)
class ReviewSpec:
    requires_app_review: bool
    human_in_loop_required: bool
    default_visibility: str


@dataclass(frozen=True)
class CapabilityLimits:
    title_max: int | None = None
    digest_max: int | None = None
    body_min: int | None = None
    min_images: int | None = None
    max_images: int | None = None
    max_duration_s: int | None = None
    max_cover_bytes: int | None = None


@dataclass(frozen=True)
class ReceiptSpec:
    success_requires: tuple[str, ...]
    unknown_is_failure: bool = True


@dataclass(frozen=True)
class UiSpec:
    preview_kind: str
    confirm_copy: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capability:
    platform: str
    label: str
    formats: tuple[str, ...]
    delivery: DeliveryFlags
    auth: AuthSpec
    review: ReviewSpec
    limits: CapabilityLimits
    receipts: ReceiptSpec
    ui: UiSpec
    official_api: bool
    lane: str
    adapter: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _flags(*, preview=True, export=True, draft=False, direct=False) -> DeliveryFlags:
    return DeliveryFlags(preview=preview, export=export, draft=draft, direct=direct)


_REGISTRY: dict[str, Capability] = {
    "wechat_mp": Capability(
        platform="wechat_mp",
        label="微信公众号",
        formats=(KIND_ARTICLE,),
        delivery=_flags(draft=True, direct=False),
        auth=AuthSpec("app_secret", (), False, "secrets/wechat_mp_<account>.json"),
        review=ReviewSpec(True, True, "draft"),
        limits=CapabilityLimits(title_max=64, digest_max=120, body_min=600, max_cover_bytes=10 * 1024 * 1024),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec("html_article", "将创建公众号草稿，不会群发或公开可见。", ("title", "digest", "body")),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="wechat_mp",
    ),
    "toutiao": Capability(
        platform="toutiao",
        label="今日头条",
        formats=(KIND_ARTICLE,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("cookie", (), False, "secrets/cookies/toutiao_<account>.json"),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(title_max=30, body_min=600),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec("html_article", "仅本地安全导出，供人工导入头条。不会直发。", ("title", "body")),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="toutiao",
    ),
    "xiaohongshu": Capability(
        platform="xiaohongshu",
        label="小红书",
        formats=(KIND_GALLERY,),
        # Project path is assisted/export only. Adapter CLI may still exist,
        # but product delivery must not expose draft/direct or treat unknown
        # receipts as platform success.
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("cli", (), False, "XHS_SKILLS_PATH"),
        review=ReviewSpec(False, True, "public"),
        limits=CapabilityLimits(
            min_images=DEFAULT_GALLERY_MIN_IMAGES,
            max_images=DEFAULT_GALLERY_MAX_IMAGES,
        ),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "slide_strip",
            "仅本地安全导出，供人工导入小红书。无 post_id/URL 不得记为平台成功。",
            ("caption", "images"),
        ),
        official_api=False,
        lane=LANE_ASSISTED,
        adapter="xiaohongshu",
    ),
    "douyin": Capability(
        platform="douyin",
        label="抖音",
        formats=(KIND_VIDEO, KIND_GALLERY),
        # Product upper bound. Adapter tightens to direct=false without user-context.
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec("oauth_user", ("video.create",), True, "secrets/douyin_<account>.json"),
        review=ReviewSpec(True, True, "private"),
        limits=CapabilityLimits(
            title_max=30,
            max_duration_s=180,
            min_images=DEFAULT_GALLERY_MIN_IMAGES,
            max_images=DEFAULT_GALLERY_MAX_IMAGES,
        ),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "video_player",
            "将通过抖音官方 API 发布（video.create）。缺少用户授权时不可直发。"
            "Playwright 仅作显式 assisted 降级。无 item_id 不得记成功。",
            ("title", "video", "images"),
        ),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="douyin",
    ),
    "x": Capability(
        platform="x",
        label="X",
        formats=(KIND_ARTICLE, KIND_GALLERY),
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec(
            "oauth_user",
            ("tweet.write", "users.read"),
            True,
            "secrets/x_<account>.json",
        ),
        review=ReviewSpec(False, True, "public"),
        limits=CapabilityLimits(title_max=280),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "html_article",
            "将通过 X 用户 OAuth（PKCE / 1.0a）发帖。app-only bearer 不能发帖。"
            "缺少 user-context 时直发不可用。无 tweet id 不得记成功。",
            ("body", "images"),
        ),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="x",
    ),
    "youtube": Capability(
        platform="youtube",
        label="YouTube",
        formats=(KIND_VIDEO,),
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec(
            "oauth_user",
            ("https://www.googleapis.com/auth/youtube.upload",),
            True,
            "secrets/youtube_<account>.json",
        ),
        review=ReviewSpec(True, True, "private"),
        limits=CapabilityLimits(max_duration_s=15 * 60),
        receipts=ReceiptSpec(("platform_post_id", "url"), True),
        ui=UiSpec(
            "video_player",
            "将调用 YouTube videos.insert。未完成应用审核时只允许 private/unlisted，"
            "不会公开直发。无视频 id 不得记成功。",
            ("title", "video", "visibility"),
        ),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="youtube",
    ),
    "tiktok": Capability(
        platform="tiktok",
        label="TikTok",
        formats=(KIND_VIDEO, KIND_GALLERY),
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec("oauth_user", ("video.publish",), True, "secrets/tiktok_<account>.json"),
        review=ReviewSpec(True, True, "private"),
        limits=CapabilityLimits(
            title_max=150,
            max_duration_s=180,
            min_images=DEFAULT_GALLERY_MIN_IMAGES,
            max_images=DEFAULT_GALLERY_MAX_IMAGES,
        ),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "video_player",
            "将调用 TikTok Content Posting API。未完成应用审核时只允许 Inbox Upload，"
            "由用户在 App 内继续编辑，不会公开直发。无 publish_id 不得记成功。",
            ("title", "video", "privacy_level"),
        ),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="tiktok",
    ),
    "instagram": Capability(
        platform="instagram",
        label="Instagram",
        formats=(KIND_GALLERY, KIND_VIDEO),
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec(
            "oauth_user",
            ("instagram_content_publish",),
            True,
            "secrets/instagram_<account>.json",
        ),
        review=ReviewSpec(True, True, "public"),
        limits=CapabilityLimits(
            min_images=DEFAULT_GALLERY_MIN_IMAGES,
            max_images=DEFAULT_GALLERY_MAX_IMAGES,
        ),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "slide_strip",
            "将调用 Instagram Professional container + media_publish。"
            "需要公开可抓取的 HTTPS 媒体 URL、Professional 授权和应用审核。"
            "缺少任一条件时不可直发，无 media id 不得记成功。",
            ("caption", "media_url"),
        ),
        official_api=True,
        lane=LANE_OFFICIAL,
        adapter="instagram",
    ),
    "bilibili": Capability(
        platform="bilibili",
        label="B站",
        formats=(KIND_VIDEO,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("none", (), False, ""),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "video_player",
            "B站暂无已核验的官方创作者发布 API。仅可本地安全导出后人工上传。"
            "暂不支持官方发布，不得记为平台直发成功。",
            ("title", "video"),
        ),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="",
    ),
    "shipinhao": Capability(
        platform="shipinhao",
        label="微信视频号",
        formats=(KIND_VIDEO,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("none", (), False, ""),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "video_player",
            "微信视频号暂不支持官方发布。仅可本地安全导出后人工发布。"
            "不得宣称可 API 直发，无平台回执不得记成功。",
            ("title", "video"),
        ),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="",
    ),
    "weibo": Capability(
        platform="weibo",
        label="微博",
        formats=(KIND_ARTICLE, KIND_GALLERY),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("none", (), False, ""),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(
            min_images=DEFAULT_GALLERY_MIN_IMAGES,
            max_images=DEFAULT_GALLERY_MAX_IMAGES,
        ),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "html_article",
            "微博暂不支持官方发布。仅可本地安全导出后人工发布。"
            "不得记为平台直发成功。",
            ("title", "body", "images"),
        ),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="",
    ),
    "kuaishou": Capability(
        platform="kuaishou",
        label="快手",
        formats=(KIND_VIDEO,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("none", (), False, ""),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "video_player",
            "快手暂不支持官方发布。仅可本地安全导出后人工发布。"
            "不得记为平台直发成功。",
            ("title", "video"),
        ),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="",
    ),
    "zhihu": Capability(
        platform="zhihu",
        label="知乎",
        formats=(KIND_ARTICLE,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("none", (), False, ""),
        review=ReviewSpec(False, True, "private"),
        limits=CapabilityLimits(),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec(
            "html_article",
            "知乎暂不支持官方发布。仅可本地安全导出后人工发布。"
            "不得记为平台直发成功。",
            ("title", "body"),
        ),
        official_api=False,
        lane=LANE_EXPORT,
        adapter="",
    ),
}


def get_capability(platform: str) -> Capability:
    try:
        return _REGISTRY[platform]
    except KeyError as exc:
        raise KeyError(f"unknown platform {platform!r}; known: {sorted(_REGISTRY)}") from exc


def all_capabilities() -> tuple[Capability, ...]:
    return tuple(_REGISTRY[name] for name in REGISTRY_PLATFORMS)


def platforms_for(*, kind: str) -> tuple[str, ...]:
    return tuple(item.platform for item in all_capabilities() if kind in item.formats)


def target_platforms_for(*, kind: str) -> tuple[str, ...]:
    """Platforms that may be selected as Project deliverable targets.

    Catalog-only P3 entries have no adapter and stay out of default targets.
    """
    return tuple(
        item.platform
        for item in all_capabilities()
        if kind in item.formats and item.adapter
    )


def official_publish_platforms() -> frozenset[str]:
    """Official OAuth/API platforms that may use confirm+direct delivery."""
    return frozenset(
        item.platform
        for item in all_capabilities()
        if item.official_api and item.delivery.direct
    )


def gallery_image_limits(platform: str) -> tuple[int, int]:
    """Return (min, max) image counts, filling documented defaults when unset."""
    cap = get_capability(platform)
    minimum = cap.limits.min_images if cap.limits.min_images is not None else DEFAULT_GALLERY_MIN_IMAGES
    maximum = cap.limits.max_images if cap.limits.max_images is not None else DEFAULT_GALLERY_MAX_IMAGES
    if minimum < 1 or maximum < minimum:
        raise ValueError(f"{platform} gallery image limits are invalid: {minimum}..{maximum}")
    return minimum, maximum


def gallery_image_limits_for(targets: Iterable[str]) -> tuple[int, int]:
    """Intersect per-target limits; used when a gallery lists several platforms."""
    names = tuple(targets)
    if not names:
        return DEFAULT_GALLERY_MIN_IMAGES, DEFAULT_GALLERY_MAX_IMAGES
    lows, highs = zip(*(gallery_image_limits(name) for name in names))
    return max(lows), min(highs)


def intersect_delivery(product: DeliveryFlags, adapter: AdapterCapabilities) -> DeliveryFlags:
    """Adapter may only tighten product flags."""
    return DeliveryFlags(
        preview=product.preview and adapter.preview,
        export=product.export and adapter.export,
        draft=product.draft and adapter.draft,
        direct=product.direct and adapter.direct,
    )


def effective_delivery(platform: str, adapter: object | None = None) -> DeliveryFlags:
    product = get_capability(platform).delivery
    if adapter is None:
        if platform in {"x", "douyin", "youtube", "tiktok", "instagram"}:
            return DeliveryFlags(
                preview=product.preview,
                export=product.export,
                draft=product.draft,
                direct=False,
            )
        return product
    return intersect_delivery(product, capabilities_from_adapter(adapter))


def mode_allowed(platform: str, mode: str, adapter: object | None = None) -> bool:
    return mode in effective_delivery(platform, adapter).enabled_modes()


def capabilities_payload(adapter_by_platform: dict[str, object] | None = None) -> list[dict[str, Any]]:
    items = []
    adapters = adapter_by_platform or {}
    for cap in all_capabilities():
        payload = cap.to_dict()
        effective = effective_delivery(cap.platform, adapters.get(cap.platform))
        payload["delivery_effective"] = asdict(effective)
        payload["can_claim_direct"] = bool(cap.official_api and effective.direct)
        payload["outcomes"] = list(KNOWN_OUTCOMES)
        items.append(payload)
    return items


def assert_unknown_is_failure(platforms: Iterable[str] = REGISTRY_PLATFORMS) -> None:
    for name in platforms:
        if not get_capability(name).receipts.unknown_is_failure:
            raise ValueError(f"{name} must treat unknown receipts as failure")


__all__ = [
    "AuthSpec",
    "Capability",
    "CapabilityLimits",
    "DeliveryFlags",
    "KIND_ARTICLE",
    "KIND_GALLERY",
    "KIND_VIDEO",
    "KNOWN_KINDS",
    "LANE_ASSISTED",
    "LANE_EXPORT",
    "LANE_OFFICIAL",
    "P3_PLATFORMS",
    "REGISTRY_PLATFORMS",
    "ReceiptSpec",
    "ReviewSpec",
    "UiSpec",
    "DEFAULT_GALLERY_MAX_IMAGES",
    "DEFAULT_GALLERY_MIN_IMAGES",
    "all_capabilities",
    "assert_unknown_is_failure",
    "capabilities_payload",
    "effective_delivery",
    "gallery_image_limits",
    "gallery_image_limits_for",
    "get_capability",
    "intersect_delivery",
    "mode_allowed",
    "official_publish_platforms",
    "platforms_for",
    "target_platforms_for",
]
