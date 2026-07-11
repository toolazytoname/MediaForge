"""原文正文抓取（M2-1 step 2）。

fetch_text(url) -> str | None
  - HTTP 200 → 简单 HTML→text 后返回
  - 网络/HTTP 错误 / url 为空 → 返回 None（不抛，编排层 fallback 到 title+summary）

依赖仅 httpx（requirements.txt 已有）。未引入 trafilatura 是为了避免新增依赖；
M2-1 验收阶段如发现正文质量不达标，再升级。
"""
from __future__ import annotations

import re

import httpx

_TIMEOUT_S = 20.0

# 去 <script> / <style> 整块（含换行）
_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL
)
_STYLE_RE = re.compile(
    r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL
)
# 去 HTML 注释
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# 去所有 tag
_TAG_RE = re.compile(r"<[^>]+>")
# 合并连续空白
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    """最小化 HTML→纯文本：去 script/style/comment/tag，合并空白。"""
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    # 反转义常见实体（仅最小集）
    text = (
        text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
    )
    text = _WS_RE.sub(" ", text).strip()
    return text


# content-type 允许列表：只有这些才可能是可读文本素材。
# 白名单而非黑名单——宁可漏抓（fallback 到 title+summary），
# 不可把 PDF/图片等二进制当纯文本喂给创作 LLM（会诱发幻觉，见回归测试）。
_TEXTLIKE_CONTENT_TYPE_RE = re.compile(
    r"\b(?:html|xml|text/plain|application/json)\b", re.IGNORECASE
)


def fetch_text(url: str | None) -> str | None:
    """抓取 url 页面正文，纯文本。任何错误 / 非文本内容 → None。"""
    if not url:
        return None
    try:
        resp = httpx.get(
            url, timeout=_TIMEOUT_S, follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return None

    content_type = resp.headers.get("content-type", "").lower()
    if not _TEXTLIKE_CONTENT_TYPE_RE.search(content_type):
        # PDF / 图片 / octet-stream 等二进制 → 不是可读素材，返回 None
        return None

    html = resp.text
    if "html" not in content_type and "xml" not in content_type:
        # text/plain / json → 原样返回（本来就不是标签文本）
        return html.strip() or None

    return _html_to_text(html) or None