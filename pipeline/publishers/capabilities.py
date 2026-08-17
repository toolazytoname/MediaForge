"""Adapter capability / receipt-status contract.

Modes (preview / export / draft / direct) and outcomes (success / failure /
unknown) must stay distinguishable. Adapters that cannot prove a mode is safe
must report it as unavailable instead of claiming they can publish.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_UNKNOWN = "unknown"
KNOWN_OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_UNKNOWN)

MODE_PREVIEW = "preview"
MODE_EXPORT = "export"
MODE_DRAFT = "draft"
MODE_DIRECT = "direct"
KNOWN_MODES = (MODE_PREVIEW, MODE_EXPORT, MODE_DRAFT, MODE_DIRECT)


@dataclass(frozen=True)
class AdapterCapabilities:
    preview: bool
    export: bool
    draft: bool
    direct: bool
    outcomes: tuple[str, ...] = KNOWN_OUTCOMES
    detail: str = ""

    def enabled_modes(self) -> tuple[str, ...]:
        flags = {
            MODE_PREVIEW: self.preview,
            MODE_EXPORT: self.export,
            MODE_DRAFT: self.draft,
            MODE_DIRECT: self.direct,
        }
        return tuple(mode for mode in KNOWN_MODES if flags[mode])


def default_capabilities(
    *,
    preview: bool = True,
    export: bool = True,
    draft: bool = False,
    direct: bool = True,
    detail: str = "",
) -> AdapterCapabilities:
    return AdapterCapabilities(
        preview=preview,
        export=export,
        draft=draft,
        direct=direct,
        outcomes=KNOWN_OUTCOMES,
        detail=detail,
    )


def capabilities_from_adapter(adapter: object) -> AdapterCapabilities:
    """Read adapter.capabilities() when present; otherwise platform defaults."""
    getter = getattr(adapter, "capabilities", None)
    if callable(getter):
        caps = getter()
        if isinstance(caps, AdapterCapabilities):
            return caps
    platform = getattr(adapter, "platform", "")
    return capabilities_for_platform(str(platform))


def capabilities_for_platform(
    platform: str,
    *,
    x_has_user_context: bool | None = None,
    douyin_has_user_context: bool | None = None,
    youtube_has_user_context: bool | None = None,
    tiktok_has_user_context: bool | None = None,
    instagram_has_user_context: bool | None = None,
) -> AdapterCapabilities:
    """Static defaults used by registry / contract tests."""
    if platform == "wechat_mp":
        return default_capabilities(
            draft=True,
            direct=False,
            detail="WeChat MP official API is draft/export only; no direct publish",
        )
    if platform == "x":
        direct = bool(x_has_user_context)
        detail = (
            "X direct publish requires verifiable user-context OAuth "
            "(user_id + tweet.write/users.read)"
            if not direct
            else "X user-context OAuth present; direct publish enabled"
        )
        return default_capabilities(direct=direct, detail=detail)
    if platform == "xiaohongshu":
        return default_capabilities(
            draft=True,
            detail="Xiaohongshu CLI: published/ready_to_publish succeed; unknown must fail",
        )
    if platform == "douyin":
        direct = bool(douyin_has_user_context)
        detail = (
            "Douyin official API requires user-context OAuth "
            "(open_id + video.create). Playwright is assisted-only."
            if not direct
            else "Douyin official API user-context present; video.create available"
        )
        return default_capabilities(direct=direct, detail=detail)
    if platform == "youtube":
        direct = bool(youtube_has_user_context)
        detail = (
            "YouTube videos.insert requires user OAuth (youtube.upload). "
            "Without app review only private/unlisted are allowed."
            if not direct
            else "YouTube user-context present; public still requires app review"
        )
        return default_capabilities(direct=direct, detail=detail)
    if platform == "tiktok":
        direct = bool(tiktok_has_user_context)
        detail = (
            "TikTok Content Posting API requires user OAuth (video.publish). "
            "Without app review only Inbox Upload is allowed; public Direct Post is not claimed."
            if not direct
            else "TikTok user-context present; public Direct Post still requires app review"
        )
        return default_capabilities(direct=direct, detail=detail)
    if platform == "instagram":
        direct = bool(instagram_has_user_context)
        detail = (
            "Instagram media_publish requires Professional user OAuth "
            "(instagram_content_publish) plus a public HTTPS media URL and app review."
            if not direct
            else "Instagram Professional user-context present; public still requires app review"
        )
        return default_capabilities(direct=direct, detail=detail)
    return default_capabilities(detail=f"platform {platform!r} default capabilities")


def assert_known_outcomes(outcomes: Iterable[str]) -> None:
    unknown = [item for item in outcomes if item not in KNOWN_OUTCOMES]
    if unknown:
        raise ValueError(f"unknown publish outcomes: {unknown}")


__all__ = [
    "AdapterCapabilities",
    "OUTCOME_SUCCESS",
    "OUTCOME_FAILURE",
    "OUTCOME_UNKNOWN",
    "KNOWN_OUTCOMES",
    "MODE_PREVIEW",
    "MODE_EXPORT",
    "MODE_DRAFT",
    "MODE_DIRECT",
    "KNOWN_MODES",
    "default_capabilities",
    "capabilities_from_adapter",
    "capabilities_for_platform",
    "assert_known_outcomes",
]
