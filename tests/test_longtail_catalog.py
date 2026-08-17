"""LAZY-83 catalog contract: P3 is honest export, assisted stays fail-closed."""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.delivery.service import OFFICIAL_PLATFORMS
from pipeline.publishers import get_adapter
from pipeline.publishers.base import AccountConfig
from pipeline.publishers.capability_registry import (
    P3_PLATFORMS,
    capabilities_payload,
    get_capability,
    mode_allowed,
)
from tests.test_gallery_deliverable import _XhsUnknown, _make_gallery


def test_p3_cannot_claim_official_direct() -> None:
    payload = {item["platform"]: item for item in capabilities_payload()}
    for name in P3_PLATFORMS:
        item = payload[name]
        assert item["official_api"] is False
        assert item["delivery"]["direct"] is False
        assert item["delivery_effective"]["direct"] is False
        assert item["can_claim_direct"] is False
        assert mode_allowed(name, "direct") is False
        assert mode_allowed(name, "export") is True
        assert name not in OFFICIAL_PLATFORMS
        with pytest.raises(ValueError, match="unknown"):
            get_adapter(
                name,
                account=AccountConfig(id="main", credentials_path=Path("missing.json")),
                config=None,
            )


def test_assisted_and_export_lanes_stay_honest() -> None:
    xhs = get_capability("xiaohongshu")
    assert xhs.official_api is False
    assert xhs.lane == "assisted"
    assert xhs.receipts.unknown_is_failure is True
    with pytest.raises(Exception, match="unknown"):
        _XhsUnknown().publish(
            None,  # type: ignore[arg-type]
            AccountConfig(id="main", credentials_path=Path("missing.json")),
        )
    toutiao = get_capability("toutiao")
    assert toutiao.official_api is False
    assert toutiao.lane == "export"
    assert toutiao.delivery.direct is False
    assert toutiao.receipts.unknown_is_failure is True


def test_gallery_cannot_target_p3_as_deliverable(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    with pytest.raises(Exception, match="gallery"):
        _make_gallery(root, "prj_p3_gal", slides=1, prefix="p3", caption="no", targets=["weibo"])
