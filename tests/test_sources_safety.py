"""域名安全校验纯函数单元测试。

借鉴 Horizon src/orchestrator.py 与 sansan0/TrendRadar
trendradar/crawler/fetcher.py::_check_domain_safety 的设计：
当 source 声明"返回 URL 的预期域名"时，丢弃 URL 不匹配的条目
（防 API 被投毒 / 中间人劫持 / 自部署实例被入侵）。

`safety.py` 是纯函数模块，不接触 DB、不接触网络。
"""
from __future__ import annotations

from pipeline.sources.base import RawItem
from pipeline.sources.safety import (
    KNOWN_DOMAIN_RULES,
    check_url,
    resolve_expected_domain,
    validate_items,
)


# ── check_url 基础语义 ────────────────────────────────────────


def test_check_url_no_expected_domain_passes_any_url() -> None:
    """expected_domain=None → 不校验，任何 URL 都通过（None 标记 None）。"""
    assert check_url("https://evil.com/x", None) is None
    assert check_url("http://insecure.example.com/x", None) is None
    assert check_url("not-a-url", None) is None


def test_check_url_empty_url_passes_when_domain_required() -> None:
    """url=None/空 → 不拒绝（无 URL 可校验，留给下游）。"""
    assert check_url(None, "example.com") is None
    assert check_url("", "example.com") is None


def test_check_url_rejects_non_https() -> None:
    """expected_domain 有值时 → http:// 直接拒绝（理由 'non_https'）。"""
    reason = check_url("http://example.com/x", "example.com")
    assert reason == "non_https"


def test_check_url_rejects_invalid_url_scheme() -> None:
    """scheme 异常（不是 http/https）→ 拒绝。"""
    reason = check_url("ftp://example.com/x", "example.com")
    assert reason is not None
    assert "scheme" in reason or "non_https" in reason


def test_check_url_rejects_urlparse_failure() -> None:
    """urlparse 抛 ValueError → 拒绝。"""
    reason = check_url("://broken", "example.com")
    assert reason is not None


# ── check_url 域名匹配 ────────────────────────────────────────


def test_check_url_exact_match_ok() -> None:
    """域名完全匹配 → 通过。"""
    assert check_url("https://example.com/article/1", "example.com") is None


def test_check_url_subdomain_match_ok() -> None:
    """子域名匹配（如 m.example.com vs example.com）→ 通过。"""
    assert check_url("https://m.example.com/x", "example.com") is None
    assert check_url("https://news.example.com/y", "example.com") is None


def test_check_url_www_prefix_stripped() -> None:
    """www. 前缀不影响匹配（example.com == www.example.com）。"""
    assert check_url("https://www.example.com/x", "example.com") is None


def test_check_url_case_insensitive() -> None:
    """域名比较大小写不敏感。"""
    assert check_url("https://EXAMPLE.com/x", "example.com") is None
    assert check_url("https://example.com/x", "EXAMPLE.com") is None


def test_check_url_mismatch_rejected() -> None:
    """完全不同域名 → 拒绝（理由含 'domain_mismatch'）。"""
    reason = check_url("https://attacker.com/x", "example.com")
    assert reason is not None
    assert "domain_mismatch" in reason
    # 理由里同时含检测到的 hostname 便于排障
    assert "attacker.com" in reason


def test_check_url_lookalike_not_subdomain() -> None:
    """同前缀但非子域（如 example.com.attacker.com）→ 拒绝。"""
    reason = check_url("https://example.com.attacker.com/x", "example.com")
    assert reason is not None
    assert "domain_mismatch" in reason


# ── resolve_expected_domain ───────────────────────────────────


def test_resolve_unknown_source_returns_none() -> None:
    """未登记的源 → None（= 不校验，安全默认）。"""
    assert resolve_expected_domain("rss:hn") is None
    assert resolve_expected_domain("custom:foo") is None


def test_resolve_known_source_returns_domain() -> None:
    """已登记的源 → 返回预期域名。"""
    # 测试用临时登记条目（直接在模块 dict 上写）
    KNOWN_DOMAIN_RULES["test:demo"] = "demo.com"
    try:
        assert resolve_expected_domain("test:demo") == "demo.com"
    finally:
        KNOWN_DOMAIN_RULES.pop("test:demo", None)


def test_resolve_default_rules_empty_for_rss() -> None:
    """默认 KNOWN_DOMAIN_RULES 不含 rss:* 条目（feed items 合法链到任意站）。"""
    # 任意 RSS 源都不应被校验
    assert resolve_expected_domain("rss:hn") is None
    assert resolve_expected_domain("rss:reddit") is None


# ── validate_items 批量校验 ──────────────────────────────────


def test_validate_items_no_domain_keeps_all() -> None:
    """expected_domain=None → 全部保留，dropped_count=0。"""
    items = [
        RawItem("A", "https://foo.com/1", None, None),
        RawItem("B", "https://bar.com/2", None, None),
    ]
    kept, dropped, reasons = validate_items(items, None)
    assert len(kept) == 2
    assert dropped == 0
    assert reasons == []


def test_validate_items_with_domain_drops_bad() -> None:
    """expected_domain 有值 → 不匹配的丢弃，理由含 (title, reason)。"""
    items = [
        RawItem("Good", "https://example.com/1", None, None),
        RawItem("Bad", "https://attacker.com/2", None, None),
    ]
    kept, dropped, reasons = validate_items(items, "example.com")
    assert len(kept) == 1
    assert kept[0].title == "Good"
    assert dropped == 1
    assert len(reasons) == 1
    title, reason = reasons[0]
    assert title == "Bad"
    assert "domain_mismatch" in reason


def test_validate_items_keeps_mixed_results() -> None:
    """混合条目：匹配/不匹配/无 URL 各按规则处理。"""
    items = [
        RawItem("Good1", "https://example.com/a", None, None),
        RawItem("Bad", "https://attacker.com/b", None, None),
        RawItem("Good2", "https://www.example.com/c", None, None),
        RawItem("NoURL", None, None, None),
        RawItem("Http", "http://example.com/d", None, None),
    ]
    kept, dropped, reasons = validate_items(items, "example.com")
    assert len(kept) == 3  # Good1, Good2, NoURL
    kept_titles = [it.title for it in kept]
    assert "Good1" in kept_titles
    assert "Good2" in kept_titles
    assert "NoURL" in kept_titles
    assert dropped == 2  # Bad, Http
    assert len(reasons) == 2


def test_validate_items_returns_dropped_reasons_in_input_order() -> None:
    """reasons 列表按输入顺序追加（便于日志对齐）。"""
    items = [
        RawItem("A-bad", "https://evil.com/1", None, None),
        RawItem("B-ok", "https://example.com/2", None, None),
        RawItem("C-bad", "https://evil.com/3", None, None),
    ]
    kept, dropped, reasons = validate_items(items, "example.com")
    assert dropped == 2
    assert [r[0] for r in reasons] == ["A-bad", "C-bad"]