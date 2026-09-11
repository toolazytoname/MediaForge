"""Turn a messy author dump plus URLs/files into sources and title options.

This module does not write Project or Idea sidecars. The HTTP layer persists
only after the author picks a title.
"""
from __future__ import annotations

import io
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from pipeline.creators import source_fetcher


class IntakeError(ValueError):
    """Author intake is empty, unsafe, or not a supported source."""


@dataclass(frozen=True)
class IntakeSource:
    kind: str
    title: str
    reference: str
    excerpt: str
    failure: str | None = None


_ALLOWED_FILE_KINDS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".pdf": "pdf",
}
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_EXCERPT = 8000
_MAX_SOURCES = 6
_WECHAT_HOST = "mp.weixin.qq.com"
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_FIRST_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")


def classify_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == _WECHAT_HOST or host.endswith(f".{_WECHAT_HOST}"):
        return "wechat"
    return "web"


def classify_filename(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_FILE_KINDS:
        raise IntakeError(f"unsupported file type: {name}")
    return _ALLOWED_FILE_KINDS[suffix]


def ensure_public_http_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise IntakeError("url must be a non-empty string")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IntakeError("url must be an absolute http(s) URL")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local") or host.endswith(".internal"):
        raise IntakeError("url is not a safe public address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise IntakeError("url is not a safe public address")
    return url.strip()


def split_pasted_url(idea: str) -> tuple[str, list[str]]:
    text = (idea or "").strip()
    if _URL_RE.match(text) and "\n" not in text:
        return "", [ensure_public_http_url(text)]
    return text, []


def material_from_file(filename: str, data: bytes) -> IntakeSource:
    if not filename or not filename.strip():
        raise IntakeError("file name is required")
    if not isinstance(data, (bytes, bytearray)):
        raise IntakeError("file bytes are required")
    if len(data) > _MAX_FILE_BYTES:
        raise IntakeError(f"file is larger than {_MAX_FILE_BYTES} bytes")
    kind = classify_filename(filename)
    if kind == "pdf":
        excerpt, title = _pdf_text_and_title(data, filename)
    else:
        excerpt = data.decode("utf-8", errors="replace").strip()
        title = _title_from_text(excerpt, fallback=Path(filename).stem)
    if not excerpt.strip():
        raise IntakeError("file is empty")
    return IntakeSource(
        kind=kind,
        title=title,
        reference=f"local:{Path(filename).name}",
        excerpt=_clip(excerpt),
    )


def fetch_url_material(url: str) -> IntakeSource:
    safe = ensure_public_http_url(url)
    kind = classify_url(safe)
    try:
        response = httpx.get(
            safe,
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
            },
        )
        response.raise_for_status()
    except Exception as error:
        return IntakeSource(
            kind=kind,
            title=_title_from_url(safe),
            reference=safe,
            excerpt="",
            failure=f"无法读取这个链接：{error}",
        )
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type or safe.lower().endswith(".pdf"):
        try:
            excerpt, title = _pdf_text_and_title(response.content, _title_from_url(safe))
        except IntakeError as error:
            return IntakeSource(kind="pdf", title=_title_from_url(safe), reference=safe, excerpt="", failure=str(error))
        return IntakeSource(kind="pdf", title=title, reference=safe, excerpt=_clip(excerpt))
    text = source_fetcher._html_to_text(response.text) if "html" in content_type or "xml" in content_type else (response.text or "").strip()
    if not text:
        text = source_fetcher.fetch_text(safe) or ""
    if not text:
        return IntakeSource(
            kind=kind,
            title=_title_from_url(safe),
            reference=safe,
            excerpt="",
            failure="这个链接没有可读正文。可以改成粘贴摘录，或换一份本地文件。",
        )
    return IntakeSource(
        kind=kind,
        title=_title_from_html(response.text, fallback=_title_from_url(safe)) if "html" in content_type else _title_from_text(text, fallback=_title_from_url(safe)),
        reference=safe,
        excerpt=_clip(text),
    )


def collect_sources(*, idea: str, urls: Iterable[str], files: Iterable[tuple[str, bytes]]) -> tuple[str, tuple[IntakeSource, ...]]:
    cleaned, discovered = split_pasted_url(idea)
    references: list[str] = []
    sources: list[IntakeSource] = []
    for raw in [*discovered, *urls]:
        if not str(raw).strip():
            continue
        source = fetch_url_material(str(raw))
        if source.reference in references:
            continue
        references.append(source.reference)
        sources.append(source)
    for filename, data in files:
        source = material_from_file(filename, data)
        if source.reference in references:
            continue
        references.append(source.reference)
        sources.append(source)
    if len(sources) > _MAX_SOURCES:
        raise IntakeError(f"at most {_MAX_SOURCES} sources")
    if not cleaned.strip() and not sources:
        raise IntakeError("write something, or add a link or file")
    return cleaned, tuple(sources)


def heuristic_titles(idea: str, source_titles: Iterable[str] | None = None) -> list[str]:
    extras = [item.strip() for item in (source_titles or []) if item and item.strip()]
    seed = _first_line(idea) or (extras[0] if extras else "未命名文章")
    seed = _clip(seed, 24)
    candidates = [
        seed,
        f"{seed}，后来我才看清" if "后来我才看清" not in seed else f"{seed}这件事",
        extras[0] if extras and extras[0] != seed else f"关于{seed}我想说的",
        "先把这件事写清楚",
    ]
    unique: list[str] = []
    for item in candidates:
        cleaned = _clip(item.strip(" 。"), 28)
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    while len(unique) < 3:
        unique.append(f"未命名文章 {len(unique) + 1}")
    return unique[:4]


def parse_titles(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or set(payload) != {"titles"}:
        raise ValueError("titles payload must contain only titles")
    raw = payload["titles"]
    if not isinstance(raw, list) or not 3 <= len(raw) <= 6:
        raise ValueError("need 3 to 6 titles")
    titles = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if len(titles) < 3:
        raise ValueError("need 3 to 6 titles")
    unique: list[str] = []
    for item in titles:
        if item not in unique:
            unique.append(_clip(item, 36))
    if len(unique) < 3:
        raise ValueError("titles must be distinct")
    return unique[:4]


def working_title(content: str, input_type: str = "thought") -> str:
    text = (content or "").strip()
    if input_type == "url":
        return _title_from_url(text)
    return _first_line(text)[:36] or "未命名灵感"


def title_prompt(idea: str, sources: Iterable[IntakeSource]) -> str:
    """Legacy prompt kept for intake tests; titles should come from a finished article."""
    return article_title_prompt(body="", idea=idea)


def article_title_prompt(*, body: str, idea: str = "") -> str:
    excerpt = (body or "").strip()
    if len(excerpt) > 4000:
        excerpt = excerpt[:4000].rstrip() + "…"
    return f"""你是微信公众号资深标题编辑。作者已经写完正文。标题必须从这篇成稿里长出来，不能只复述作者开写前的一团想法。

作者开写前的想法（只作背景，不是标题来源）：
{idea.strip() or "（无）"}

成稿正文：
{excerpt or "（正文为空，禁止硬编）"}

任务：给出 4 个会让目标读者停下来点开的中文标题。四个角度必须不同：
1. 反差/冲突：正文里真实存在的张力（例如更快了却更喘不过气）
2. 具体钩子：抽正文里的一句人话、一个细节或一个判断，不要空概念
3. 疑问钩：正文里作者真正没想完的问题，不要假悬疑
4. 克制主张：作者真正的判断，短、硬、不鸡汤

硬钩子优先顺序（只许用正文里有的）：具体细节 → 冲突/反转 → 真实后果 → 疑问。
标点可以谨慎用「」，：？！，不要堆。

禁止：
- 编造正文没有的数字、人名、公司、榜单、论文、「刚刚」「全球第一」「首个」
- 鸡汤、口号、贩卖焦虑、故弄玄虚（「看完沉默了」「细思极恐」）
- emoji、编号、引号包裹整句

每个标题 14—28 个字。
只返回严格 JSON：{{"titles":["...","...","...","..."]}}。"""


def _pdf_text_and_title(data: bytes, fallback: str) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise IntakeError("PDF support requires pypdf") from error
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as error:
        raise IntakeError(f"cannot read PDF: {error}") from error
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    excerpt = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    meta_title = ""
    if reader.metadata is not None:
        raw = reader.metadata.get("/Title") if hasattr(reader.metadata, "get") else None
        if isinstance(raw, str):
            meta_title = raw.strip()
    title = meta_title or _title_from_text(excerpt, fallback=Path(fallback).stem)
    if not excerpt:
        excerpt = f"（已附上 PDF：{Path(fallback).name}，未能抽出正文。写作时只会知道有这份文件。）"
    return excerpt, title


def _title_from_html(html: str, *, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return fallback
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title[:36] or fallback


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    slug = (parsed.path or "").strip("/").split("/")[-1]
    host = parsed.hostname or "链接"
    label = slug or host
    return _clip(label, 36) or "未命名链接"


def _title_from_text(text: str, *, fallback: str) -> str:
    return _first_line(text)[:36] or fallback[:36] or "未命名资料"


def _first_line(text: str) -> str:
    if not text:
        return ""
    match = _FIRST_SENTENCE.search(text.strip())
    if match:
        return match.group(0).strip()
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _clip(text: str, limit: int = _MAX_EXCERPT) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
