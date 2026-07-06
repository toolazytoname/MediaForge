"""tests/test_metrics_collectors.py — 补 collectors.py 覆盖（59%→70%）。

聚焦：_real_x_get 错误路径 / _parse_toutiao_manage_html / _safe_int 边界 /
XiaohongshuMetricsCollector 异常 / DouyinMetricsCollector 实际路径 /
build_collector 完整分支。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.metrics.collectors import (
    DouyinMetricsCollector,
    MetricsSnapshot,
    ToutiaoMetricsCollector,
    XiaohongshuMetricsCollector,
    XMetricsCollector,
    _parse_douyin_manage_html,
    _parse_toutiao_manage_html,
    _real_x_get,
    _safe_int,
    build_collector,
)
from pipeline.models import Publication, PublicationStatus


def _pub(platform: str, post_id: str | None) -> Publication:
    return Publication(
        id="p_x",
        content_id="c_1",
        platform=platform,
        account_id="main",
        scheduled_at="2026-01-01T00:00:00Z",
        published_at="2026-01-01T00:00:00Z",
        platform_post_id=post_id,
        platform_url=None,
        error=None,
        retry_count=0,
        status=PublicationStatus.PUBLISHED,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


# ── _real_x_get 错误路径（line 143-150）──────────────────────


def test_real_x_get_network_error_returns_zero():
    """httpx 抛异常 → (0, None)。"""
    with patch("httpx.get", side_effect=RuntimeError("net err")):
        status, body = _real_x_get("https://api.example.com")
    assert status == 0
    assert body is None


def test_real_x_get_4xx_returns_status_no_body():
    """HTTP 4xx → (status_code, None)。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 404
    with patch("httpx.get", return_value=fake_resp):
        status, body = _real_x_get("https://api.example.com")
    assert status == 404
    assert body is None


def test_real_x_get_invalid_json_returns_status_none():
    """200 但 body 非 JSON → (200, None)。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.side_effect = ValueError("not json")
    with patch("httpx.get", return_value=fake_resp):
        status, body = _real_x_get("https://api.example.com")
    assert status == 200
    assert body is None


# ── _safe_int 边界（line 256-257）──────────────────────────


def test_safe_int_various():
    assert _safe_int(None) is None
    assert _safe_int("123") == 123
    assert _safe_int(0) == 0
    assert _safe_int("abc") is None
    assert _safe_int([1, 2]) is None  # unhashable
    assert _safe_int(3.7) == 3  # 浮点转 int（截断）


# ── _parse_toutiao_manage_html 边界（line 243）─────────────


def test_parse_toutiao_html_no_match_returns_none():
    """HTML 不含 post_id → None。"""
    html = "<div>other content</div>"
    assert _parse_toutiao_manage_html(html, "tt_abc_123") is None


def test_parse_toutiao_html_match_extracts_first_number():
    """HTML 含 post_id + 后续数字 → 提取 views。"""
    html = '<tr data-id="tt_abc_123">阅读量 42</tr>'
    out = _parse_toutiao_manage_html(html, "tt_abc_123")
    assert out is not None
    assert out["views"] == 42
    # 启发式只返 views；likes/comments/shares 键不存在
    assert "likes" not in out
    assert "comments" not in out


# ── _parse_douyin_manage_html 边界（line 370-377）────────


def test_parse_douyin_html_no_match_returns_none():
    html = "<div>no video_id here</div>"
    assert _parse_douyin_manage_html(html, "v_xyz") is None


def test_parse_douyin_html_match_extracts_metrics():
    """_parse_douyin_manage_html 启发式只返 views（与头条一致）。"""
    html = '<div data-id="v_xyz">播放 1000 点赞 50 评论 5</div>'
    out = _parse_douyin_manage_html(html, "v_xyz")
    assert out is not None
    assert out["views"] == 1000
    # likes/comments/shares 键不存在（启发式不靠谱）
    assert "likes" not in out


# ── X collector 防御（line 122-123）────────────────────────


def test_x_collector_requires_bearer_token():
    """XMetricsCollector() 无 token → ValueError（line 78）。"""
    with pytest.raises(ValueError, match="bearer_token"):
        XMetricsCollector(bearer_token="")


def test_x_collector_returns_none_when_no_post_id():
    pub = _pub("x", None)
    collector = XMetricsCollector(bearer_token="T")
    assert collector.collect(pub) is None


def test_x_collector_http_get_raises_returns_none():
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    def boom(url, **kw):
        raise RuntimeError("net down")
    with patch.object(collector, "_get", side_effect=boom):
        assert collector.collect(pub) is None


def test_x_collector_status_401_returns_none():
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(401, None)):
        assert collector.collect(pub) is None


def test_x_collector_status_403_returns_none():
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(403, None)):
        assert collector.collect(pub) is None


def test_x_collector_status_429_returns_none():
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(429, None)):
        assert collector.collect(pub) is None


def test_x_collector_status_500_returns_none():
    """非 200 状态（5xx）→ None。"""
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(500, None)):
        assert collector.collect(pub) is None


def test_x_collector_payload_not_dict_returns_none():
    """200 但 body 不是 dict → None。"""
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(200, "not a dict")):
        assert collector.collect(pub) is None


def test_x_collector_data_not_dict_returns_none():
    """200 + payload['data'] 不是 dict → None。"""
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    with patch.object(collector, "_get", return_value=(200, {"data": "string"})):
        assert collector.collect(pub) is None


def test_x_collector_no_public_metrics_uses_empty_dict():
    """data['public_metrics'] 缺失 → 空字典 fallback（line 111-113）。"""
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    payload = {"data": {"id": "12345", "text": "hi"}}  # 无 public_metrics
    with patch.object(collector, "_get", return_value=(200, payload)):
        snap = collector.collect(pub)
    assert snap is not None
    assert snap.views == 0
    assert snap.likes == 0


def test_x_collector_returns_none_on_non_int_metrics():
    """public_metrics 字段不是 int → None（不抛）—— 覆盖 line 122-123 except 路径。"""
    pub = _pub("x", "12345")
    collector = XMetricsCollector(bearer_token="T")
    # metrics 字段含字符串而非 int → int() 抛 ValueError → return None
    bad_payload = {
        "data": {
            "public_metrics": {
                "impression_count": "not_a_number",
                "like_count": 0,
                "reply_count": 0,
                "retweet_count": 0,
                "quote_count": 0,
            }
        }
    }
    with patch.object(collector, "_get", return_value=(200, bad_payload)):
        assert collector.collect(pub) is None


# ── XiaohongshuMetricsCollector 异常（line 285-288）─────────


def test_xhs_collector_probe_raises_returns_none():
    """probe_fn 抛异常 → None（不外泄）。"""
    pub = _pub("xiaohongshu", "note_123")

    def boom(post_id: str):
        raise RuntimeError("xhs not ready")

    collector = XiaohongshuMetricsCollector(probe_fn=boom)
    assert collector.collect(pub) is None


def test_xhs_collector_probe_returns_non_dict_returns_none():
    """probe_fn 返非 dict → None。"""
    pub = _pub("xiaohongshu", "note_123")
    collector = XiaohongshuMetricsCollector(probe_fn=lambda pid: "not a dict")
    assert collector.collect(pub) is None


# ── DouyinMetricsCollector 实际路径（line 343-365）───────


def test_douyin_collector_returns_none_when_no_post_id():
    pub = _pub("douyin", None)
    collector = DouyinMetricsCollector(
        cookies_path=Path("secrets/dy.json"), probe_fn=lambda vid: {"views": 1},
    )
    assert collector.collect(pub) is None


def test_douyin_collector_via_probe_fn():
    pub = _pub("douyin", "v_001")
    fake_data = {"views": 1000, "likes": 50, "comments": 5, "shares": 2}
    # probe_fn 签名: (cookies_path, video_id) → dict | None
    collector = DouyinMetricsCollector(
        cookies_path=Path("secrets/dy.json"),
        probe_fn=lambda cp, vid: fake_data,
    )
    snap = collector.collect(pub)
    assert snap is not None
    assert snap.views == 1000
    assert snap.likes == 50
    assert snap.platform == "douyin"


def test_douyin_collector_probe_raises_returns_none():
    pub = _pub("douyin", "v_001")
    def boom(cp, vid):
        raise RuntimeError("x")
    collector = DouyinMetricsCollector(
        cookies_path=Path("secrets/dy.json"),
        probe_fn=boom,
    )
    assert collector.collect(pub) is None


def test_douyin_collector_probe_non_dict_returns_none():
    pub = _pub("douyin", "v_001")
    collector = DouyinMetricsCollector(
        cookies_path=Path("secrets/dy.json"),
        probe_fn=lambda cp, vid: [1, 2, 3],
    )
    assert collector.collect(pub) is None


# ── build_collector 各分支（line 411-419）─────────────


def test_build_collector_returns_x_with_credentials(tmp_path: Path):
    import json

    from pipeline.config import (
        AccountAPI, AppConfig, LLMConfig, LLMTiers, Pillar, PlatformAPI, PlatformsConfig,
    )

    creds = tmp_path / "x.json"
    creds.write_text(json.dumps({"bearer_token": "TOK"}))

    cfg = AppConfig(
        pillars=[Pillar(id="t", name="T", description="d", scoring_hint="s")],
        llm=LLMConfig(tiers=LLMTiers(cheap="x", creative="y", critical="z")),
        platforms=PlatformsConfig(
            x=PlatformAPI(
                kind="api",
                windows=[],
                accounts=[AccountAPI(id="main", credentials=str(creds))],
            ),
        ),
    )
    c = build_collector("x", config=cfg)
    assert c is not None
    assert c.platform == "x"


def test_build_collector_returns_none_for_unconfigured_platform():
    from pipeline.config import (
        AppConfig, LLMConfig, LLMTiers, Pillar, PlatformsConfig,
    )

    cfg = AppConfig(
        pillars=[Pillar(id="t", name="T", description="d", scoring_hint="s")],
        llm=LLMConfig(tiers=LLMTiers(cheap="x", creative="y", critical="z")),
        platforms=PlatformsConfig(),  # 全部 None
    )
    # 平台在 cfg 中未配置 → build_collector 应返 None（不抛）
    assert build_collector("x", config=cfg) is None


def test_build_collector_returns_none_for_unknown_platform():
    from pipeline.config import (
        AppConfig, LLMConfig, LLMTiers, Pillar, PlatformsConfig,
    )

    cfg = AppConfig(
        pillars=[Pillar(id="t", name="T", description="d", scoring_hint="s")],
        llm=LLMConfig(tiers=LLMTiers(cheap="x", creative="y", critical="z")),
        platforms=PlatformsConfig(),
    )
    # "wechat" 不在 build_collector 已知平台 → None
    assert build_collector("wechat", config=cfg) is None
