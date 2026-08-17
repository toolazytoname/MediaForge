"""Product-level CapabilityRegistry (LAZY-40 / RFC §5.3).

Adapter.capabilities() may tighten these flags but must not loosen them.
Old Publish Center still reads adapter/platform defaults in capabilities.py.
Project delivery only exposes the product-effective modes from this registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

REGISTRY_PLATFORMS = ("wechat_mp", "toutiao", "xiaohongshu", "douyin", "x")


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
        adapter="toutiao",
    ),
    "xiaohongshu": Capability(
        platform="xiaohongshu",
        label="小红书",
        formats=(KIND_GALLERY,),
        delivery=_flags(draft=True, direct=True),
        auth=AuthSpec("cli", (), False, "XHS_SKILLS_PATH"),
        review=ReviewSpec(False, True, "public"),
        limits=CapabilityLimits(min_images=1, max_images=9),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec("slide_strip", "将通过外部 CLI 发布小红书图卡，需人工确认。", ("caption", "images")),
        adapter="xiaohongshu",
    ),
    "douyin": Capability(
        platform="douyin",
        label="抖音",
        formats=(KIND_VIDEO,),
        delivery=_flags(draft=False, direct=False),
        auth=AuthSpec("cookie", (), False, "secrets/cookies/douyin_<account>.json"),
        review=ReviewSpec(False, True, "public"),
        limits=CapabilityLimits(max_duration_s=180),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec("video_player", "官方 API 接入前只允许导出或人工辅助，不会自动直发。", ("script", "video")),
        adapter="douyin",
    ),
    "x": Capability(
        platform="x",
        label="X",
        formats=(KIND_ARTICLE,),
        delivery=_flags(draft=False, direct=True),
        auth=AuthSpec("oauth_user", ("tweet.write", "users.read"), True, "secrets/x_<account>.json"),
        review=ReviewSpec(False, True, "public"),
        limits=CapabilityLimits(title_max=280),
        receipts=ReceiptSpec(("platform_post_id",), True),
        ui=UiSpec("html_article", "将发到 X。缺少 user-context 时直发不可用。", ("body",)),
        adapter="x",
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
        if platform == "x":
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
    "REGISTRY_PLATFORMS",
    "ReceiptSpec",
    "ReviewSpec",
    "UiSpec",
    "all_capabilities",
    "assert_unknown_is_failure",
    "capabilities_payload",
    "effective_delivery",
    "get_capability",
    "intersect_delivery",
    "mode_allowed",
    "platforms_for",
]
