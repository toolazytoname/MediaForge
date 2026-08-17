"""Adapter capability / receipt-status contract tests (LAZY-25)."""
from __future__ import annotations

from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.capabilities import (
    KNOWN_MODES,
    KNOWN_OUTCOMES,
    MODE_DIRECT,
    MODE_DRAFT,
    MODE_EXPORT,
    MODE_PREVIEW,
    capabilities_for_platform,
    capabilities_from_adapter,
)
from pipeline.publishers.x_api import XApiPublisher
from pipeline.publishers.wechat_mp import WechatMpPublisher


class _Stub(PublisherAdapter):
    platform = "toutiao"

    def validate(self, bundle):
        return []

    def publish(self, bundle, account, dry_run=False):
        return PublishResult(None, None, "{}")


def test_modes_and_outcomes_are_distinguishable() -> None:
    assert KNOWN_MODES == (MODE_PREVIEW, MODE_EXPORT, MODE_DRAFT, MODE_DIRECT)
    assert set(KNOWN_OUTCOMES) == {"success", "failure", "unknown"}


def test_wechat_is_draft_not_direct() -> None:
    caps = WechatMpPublisher(app_id="id", app_secret="secret").capabilities()
    assert caps.preview is True
    assert caps.export is True
    assert caps.draft is True
    assert caps.direct is False


def test_x_app_only_bearer_hides_direct() -> None:
    adapter = XApiPublisher(bearer_token="dummy")
    caps = adapter.capabilities()
    assert caps.preview is True
    assert caps.direct is False
    assert "user-context" in caps.detail or "disabled" in caps.detail.lower()


def test_x_user_context_enables_direct() -> None:
    adapter = XApiPublisher(
        bearer_token="dummy",
        user_id="123",
        scopes=("tweet.write", "users.read"),
    )
    assert adapter.capabilities().direct is True


def test_xiaohongshu_default_distinguishes_unknown() -> None:
    caps = capabilities_for_platform("xiaohongshu")
    assert "unknown" in caps.outcomes
    assert caps.direct is True
    assert "unknown" in caps.detail


def test_capabilities_from_adapter_reads_override() -> None:
    stub = _Stub()
    caps = capabilities_from_adapter(stub)
    assert caps.preview is True
    assert caps.direct is True
