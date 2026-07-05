"""选题去重工具（TECH_SPEC §3 content_hash + HARD_PARTS §5 幂等）。

content_hash = sha256(normalize_title(title) + '|' + extract_domain(url))

设计动机：
  - 同一条新闻被多源转载（title 略有出入）→ normalize 后判重
  - 同一内容镜像到不同域名 → 保留转载源各自的可见度（不同 hash）

normalize 策略：基于 Unicode category 保留 Letter (L*) / Number (N*) /
Connector_Punctuation (Pc, 即下划线)，其余（whitespace Z*、所有 Punctuation
P*、Symbol S*、Mark M*）一律剥除——避免 ASCII-only 正则误伤中文，同时把
【】- 等 Unicode 标点也去干净。
"""
from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import urlparse

_WWW_PREFIX = "www."


def _is_kept(ch: str) -> bool:
    """normalize 保留规则：字母（L*）+ 数字（N*）。下划线/标点/空白一律剥除。"""
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N")


def normalize_title(title: str) -> str:
    """小写 + 去空白/标点/符号。仅保留 Unicode 字母与数字。"""
    return "".join(
        ch for ch in title.lower() if _is_kept(ch)
    )


def extract_domain(url: str | None) -> str:
    """url netloc，小写，剥 www. 前缀。

    无 url / 非合法 url → 返回空串（hash 仅靠 title 决定）。
    保留端口（example.com:8080 ≠ example.com）。
    """
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if not netloc:
        return ""
    if netloc.startswith(_WWW_PREFIX):
        netloc = netloc[len(_WWW_PREFIX):]
    return netloc


def content_hash(title: str, url: str | None) -> str:
    """sha256(normalize(title) + '|' + domain)，返回 64 位 hex。"""
    h = hashlib.sha256()
    h.update(normalize_title(title).encode("utf-8"))
    h.update(b"|")
    h.update(extract_domain(url).encode("utf-8"))
    return h.hexdigest()