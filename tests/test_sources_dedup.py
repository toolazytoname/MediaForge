"""去重 helper 单元测试（TECH_SPEC §3 content_hash 字段）。

content_hash = sha256(normalize(title) + '|' + domain)
normalize: 小写、去空白/标点
domain: url netloc 去 www. 前缀

设计目的：同一条新闻被多源转载（title 略有出入），只要 normalize 后一致就判重；
不同域名（同一内容镜像）则算不同（保留转载源各自的可见度）。
"""
from __future__ import annotations

from pipeline.sources.dedup import (
    content_hash,
    extract_domain,
    normalize_title,
)


def test_normalize_lowercase_strip_punct_whitespace() -> None:
    """normalize: 小写 + 去标点 + 去空白。"""
    assert normalize_title("Hello, World!") == "helloworld"
    assert normalize_title("  Foo   BAR  ") == "foobar"
    assert normalize_title("a-b_c") == "abc"  # 下划线视为 word 字符保留


def test_normalize_chinese() -> None:
    """中文 + 英文混合也能 normalize。"""
    assert normalize_title("AI 周刊 #12") == "ai周刊12"
    assert normalize_title("【头条】GPT-5 来了") == "头条gpt5来了"


def test_normalize_empty() -> None:
    """空字符串 normalize 后还是空（hash 一样）。"""
    assert normalize_title("") == ""


def test_extract_domain_strips_www() -> None:
    """www. 前缀剥除（统一 example.com 与 www.example.com）。"""
    assert extract_domain("https://www.example.com/x") == "example.com"
    assert extract_domain("https://example.com/x") == "example.com"


def test_extract_domain_lowercase() -> None:
    """域名统一小写。"""
    assert extract_domain("https://Example.COM/X") == "example.com"


def test_extract_domain_port() -> None:
    """保留端口（区分端口差异不算同源）。"""
    assert extract_domain("http://example.com:8080/x") == "example.com:8080"


def test_extract_domain_none_or_empty() -> None:
    """url 为空时 domain 也为空（hash 仅靠 title 决定）。"""
    assert extract_domain(None) == ""
    assert extract_domain("") == ""
    assert extract_domain("not-a-url") == ""


def test_content_hash_deterministic_sha256_hex() -> None:
    """hash 是 64 位 hex（sha256 长度），可复现。"""
    h1 = content_hash("Hello", "https://example.com/x")
    h2 = content_hash("Hello", "https://example.com/x")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_content_hash_depends_on_normalized_title() -> None:
    """不同 normalize 后形式的同一标题 → 相同 hash。"""
    a = content_hash("Hello, World!", "https://example.com")
    b = content_hash("  hello world  ", "https://example.com")
    c = content_hash("HELLO WORLD", "https://example.com")
    assert a == b == c


def test_content_hash_differs_by_domain() -> None:
    """不同域名 → 不同 hash（保留镜像源）。"""
    a = content_hash("Same Title", "https://example.com")
    b = content_hash("Same Title", "https://other.com")
    assert a != b


def test_content_hash_differs_by_title() -> None:
    """不同标题 → 不同 hash。"""
    a = content_hash("Title A", "https://example.com")
    b = content_hash("Title B", "https://example.com")
    assert a != b


def test_content_hash_null_url_uses_empty_domain() -> None:
    """url=None 时 domain=""，两次 hash 一致（不抛错）。"""
    a = content_hash("Title", None)
    b = content_hash("Title", "")
    assert a == b