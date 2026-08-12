"""Safe, project-local parsing for creator materials.

This module deliberately stores derived facts separately from the immutable
``materials.json`` records.  A parser failure is a visible ``not_used`` result,
never an invented citation and never a blocker for the rest of a project.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from pipeline import creator_materials
from pipeline.projects import DEFAULT_PROJECTS_ROOT


MAX_REMOTE_BYTES = 2 * 1024 * 1024
MAX_EXTRACT_CHARS = 100_000
URL_TIMEOUT_SECONDS = 8.0
MAX_REDIRECTS = 4
_ALLOWED_REMOTE_MIME_PREFIXES = ("text/",)
_ALLOWED_REMOTE_MIMES = {"application/pdf", "application/xhtml+xml"}
_ANALYSIS_FILE = "analysis.json"


class MaterialParseError(ValueError):
    """A source could not be parsed safely; callers persist a not-used result."""


@dataclass(frozen=True)
class _RemoteResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def parse_project_material(
    project_id: str, material_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> dict[str, Any]:
    """Parse one material and atomically persist a traceable derived result."""
    item = _material(project_id, material_id, projects_root)
    try:
        result = _parse(item, project_id, projects_root)
    except MaterialParseError as exc:
        result = _not_used(item, str(exc))
    _write_analysis(project_id, material_id, result, projects_root)
    return result


def get_project_material_analysis(
    project_id: str, material_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> dict[str, Any] | None:
    return _read_analysis(project_id, projects_root).get(material_id)


def project_material_context(
    project_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> tuple[dict[str, str], ...]:
    """Return only verified source segments for a future generation prompt."""
    analyses = _read_analysis(project_id, projects_root)
    context: list[dict[str, str]] = []
    for item in creator_materials.list_project_materials(project_id, projects_root=projects_root):
        result = analyses.get(item.id)
        if not result or result.get("status") != "used":
            continue
        for segment in result.get("segments", []):
            if segment.get("kind") == "source_fact":
                context.append({"citation": segment["citation"], "text": segment["text"]})
    return tuple(context)


def _parse(item: creator_materials.CreatorMaterial, project_id: str, root: str | Path) -> dict[str, Any]:
    if item.kind == "url":
        text, title, final_url = _fetch_url(item.source)
        return _used(item, _segments(item.id, text), title=title, resolved_url=final_url)
    if item.kind == "image":
        return _used(item, [], image={"original_name": item.original_name, "sha256": item.sha256,
                                      "description_status": "not_generated"})
    path = _source_path(item, project_id, root)
    payload = path.read_bytes()
    if len(payload) > MAX_REMOTE_BYTES:
        raise MaterialParseError("source is too large to parse")
    if item.kind == "pdf":
        text = _pdf_text(payload)
    else:
        text = _decode(payload)
    if not text.strip():
        raise MaterialParseError("no extractable text; this source was not used")
    return _used(item, _segments(item.id, text))


def _used(item: creator_materials.CreatorMaterial, segments: list[dict[str, str]], **extra: Any) -> dict[str, Any]:
    return {"material_id": item.id, "status": "used", "error": None,
            "source": item.source, "source_sha256": item.sha256,
            "parsed_at": datetime.now(timezone.utc).isoformat(), "segments": segments,
            **extra}


def _not_used(item: creator_materials.CreatorMaterial, reason: str) -> dict[str, Any]:
    return {"material_id": item.id, "status": "not_used", "error": reason,
            "source": item.source, "source_sha256": item.sha256,
            "parsed_at": datetime.now(timezone.utc).isoformat(), "segments": []}


def _segments(material_id: str, text: str) -> list[dict[str, str]]:
    cleaned = re.sub(r"\r\n?", "\n", text).strip()[:MAX_EXTRACT_CHARS]
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    return [{"citation": f"{material_id}:{index}", "text": _strip_marker(paragraph), "kind": _segment_kind(paragraph)}
            for index, paragraph in enumerate(paragraphs, start=1)]


def _segment_kind(paragraph: str) -> str:
    if paragraph.startswith("[观点]") or paragraph.startswith("[author view]"):
        return "author_view"
    if paragraph.startswith("[待核查]") or paragraph.startswith("[needs verification]"):
        return "needs_verification"
    # AI inference is never fabricated in source parsing.  Future generation
    # proposals may add explicitly-labelled ``ai_inference`` segments.
    return "source_fact"


def _strip_marker(paragraph: str) -> str:
    return re.sub(r"^\[(?:观点|author view|待核查|needs verification)\]\s*", "", paragraph, flags=re.I)


def _source_path(item: creator_materials.CreatorMaterial, project_id: str, root: str | Path) -> Path:
    if not item.stored_path:
        raise MaterialParseError("source file is missing; this source was not used")
    base = Path(root) / project_id / "materials"
    try:
        path = creator_materials._safe_relative(base, item.stored_path)  # type: ignore[attr-defined]
    except creator_materials.CreatorMaterialError as exc:
        raise MaterialParseError("unsafe source path; this source was not used") from exc
    if not path.is_file():
        raise MaterialParseError("source file is missing; this source was not used")
    return path


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise MaterialParseError("source encoding is unreadable; this source was not used")


def _pdf_text(payload: bytes) -> str:
    # A dependency-free conservative extractor.  PDF streams are opaque without
    # a parser; scanning images produces no literal text and is reported honestly.
    literals = re.findall(rb"\(([^()]*)\)", payload)
    text = "\n".join(_decode(part) for part in literals if part.strip())
    if not text.strip():
        raise MaterialParseError("PDF has no extractable text (possibly a scanned PDF); this source was not used")
    return text


def _fetch_url(url: str) -> tuple[str, str | None, str]:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _validate_public_url(current)
        response = _request(current)
        location = response.headers.get("location")
        if 300 <= response.status_code < 400 and location:
            current = urljoin(current, location)
            continue
        if response.status_code != 200:
            raise MaterialParseError(f"URL returned HTTP {response.status_code}; this source was not used")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type and not (content_type.startswith(_ALLOWED_REMOTE_MIME_PREFIXES) or content_type in _ALLOWED_REMOTE_MIMES):
            raise MaterialParseError("URL MIME type is not readable text; this source was not used")
        declared_size = response.headers.get("content-length")
        if declared_size is not None:
            try:
                size_hint = int(declared_size)
            except ValueError:
                size_hint = 0
            if size_hint > MAX_REMOTE_BYTES:
                raise MaterialParseError("URL response is too large; this source was not used")
        payload = response.content
        if len(payload) > MAX_REMOTE_BYTES:
            raise MaterialParseError("URL response is too large; this source was not used")
        text = _decode(payload)
        if "html" in content_type or "<html" in text[:500].lower():
            title, text = _html_text(text)
        else:
            title = None
        if not text.strip():
            raise MaterialParseError("URL has no readable text; this source was not used")
        return text, title, current
    raise MaterialParseError("URL has too many redirects; this source was not used")


def _request(url: str) -> _RemoteResponse:
    try:
        with httpx.Client(follow_redirects=False, timeout=URL_TIMEOUT_SECONDS,
                          headers={"User-Agent": "MediaForge/creator-material-parser"}) as client:
            with client.stream("GET", url) as response:
                declared_size = response.headers.get("content-length")
                if declared_size is not None:
                    try:
                        size_hint = int(declared_size)
                    except ValueError:
                        size_hint = 0
                    if size_hint > MAX_REMOTE_BYTES:
                        raise MaterialParseError("URL response is too large; this source was not used")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_REMOTE_BYTES:
                        raise MaterialParseError("URL response is too large; this source was not used")
                    chunks.append(chunk)
                return _RemoteResponse(response.status_code, dict(response.headers), b"".join(chunks), str(response.url))
    except httpx.HTTPError as exc:
        raise MaterialParseError("URL could not be read; this source was not used") from exc


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise MaterialParseError("URL is invalid; this source was not used")
    host = parsed.hostname.rstrip(".")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise MaterialParseError("URL host could not be resolved; this source was not used") from exc
    if not addresses or any(_unsafe_address(address) for address in addresses):
        raise MaterialParseError("URL points to a private or reserved network; this source was not used")


def _unsafe_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not address.is_global


def _html_text(source: str) -> tuple[str | None, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", source, flags=re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else None
    parser = _VisibleText()
    try:
        parser.feed(source)
    except Exception as exc:
        raise MaterialParseError("URL HTML is unreadable; this source was not used") from exc
    return title, "\n".join(parser.parts)


def _material(project_id: str, material_id: str, root: str | Path) -> creator_materials.CreatorMaterial:
    items = creator_materials.list_project_materials(project_id, projects_root=root)
    item = next((candidate for candidate in items if candidate.id == material_id), None)
    if item is None:
        raise MaterialParseError("unknown project material")
    return item


def _analysis_path(project_id: str, root: str | Path) -> Path:
    return Path(root) / project_id / "materials" / _ANALYSIS_FILE


def _read_analysis(project_id: str, root: str | Path) -> dict[str, dict[str, Any]]:
    path = _analysis_path(project_id, root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterialParseError("material analysis is unreadable") from exc
    if not isinstance(data, dict) or any(not isinstance(key, str) or not _valid_analysis(key, value) for key, value in data.items()):
        raise MaterialParseError("material analysis is invalid")
    return data


def _valid_analysis(material_id: str, value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"material_id", "status", "error", "source", "source_sha256", "parsed_at", "segments"}
    allowed = required | {"title", "resolved_url", "image"}
    if set(value) - allowed or not required.issubset(value) or value.get("material_id") != material_id:
        return False
    if value.get("status") not in {"used", "not_used"} or not isinstance(value.get("source"), str):
        return False
    if not isinstance(value.get("source_sha256"), str) or len(value["source_sha256"]) != 64:
        return False
    if value["status"] == "used" and value.get("error") is not None:
        return False
    if value["status"] == "not_used" and (not isinstance(value.get("error"), str) or not value["error"]):
        return False
    try:
        datetime.fromisoformat(value["parsed_at"])
    except (TypeError, ValueError):
        return False
    if not isinstance(value["segments"], list) or any(not _valid_segment(material_id, segment) for segment in value["segments"]):
        return False
    if "image" in value and (not isinstance(value["image"], dict) or value["image"].get("description_status") not in {"not_generated", "generated"}):
        return False
    return True


def _valid_segment(material_id: str, value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"citation", "text", "kind"}:
        return False
    citation, text, kind = value["citation"], value["text"], value["kind"]
    return (isinstance(citation, str) and citation.startswith(f"{material_id}:") and citation.rsplit(":", 1)[-1].isdigit()
            and isinstance(text, str) and bool(text.strip()) and kind in {"source_fact", "author_view", "ai_inference", "needs_verification"})


def _write_analysis(project_id: str, material_id: str, result: dict[str, Any], root: str | Path) -> None:
    path = _analysis_path(project_id, root)
    data = _read_analysis(project_id, root)
    data[material_id] = result
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
