"""Strict, project-sidecar annotations for a precise piece of an article.

Annotations are author instructions, never AI changes.  Text annotations retain
enough evidence to safely survive a manual article edit: the selected excerpt,
its neighbouring text, a semantic structural anchor and a version fingerprint.
When that evidence cannot identify one target in a later master version, the
annotation is explicitly orphaned instead of being silently redirected.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import master_documents, projects as project_store, visuals
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


_NAME = "local_annotations.json"
_KINDS = frozenset({"text", "image"})
_STATUSES = frozenset({"active", "orphaned"})
_CATEGORIES = frozenset({"composition", "style", "subject", "text", "fact"})
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class LocalAnnotationError(ValueError):
    """A local annotation request or its persisted sidecar is unsafe."""


@dataclass(frozen=True)
class LocalAnnotation:
    id: str
    project_id: str
    kind: str
    feedback: str
    categories: tuple[str, ...]
    excerpt: str | None
    context_before: str | None
    context_after: str | None
    structural_anchor: str | None
    paragraph_anchor: str | None
    asset_id: str | None
    source_version: int
    source_hash: str
    resolved_version: int
    resolved_hash: str
    status: str
    orphan_reason: str | None
    created_at: str
    updated_at: str


def master_hash(master: master_documents.MasterDocument | None) -> str:
    if master is None:
        raise LocalAnnotationError("master not found")
    return hashlib.sha256(f"{master.title}\0{master.body}".encode("utf-8")).hexdigest()


def create_text_annotation(project_id: str, *, excerpt: str, feedback: str, categories: tuple[str, ...], now: str,
                           projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                           annotation_id: str | None = None) -> LocalAnnotation:
    master = _required_master(project_id, projects_root)
    selected = _text("excerpt", excerpt)
    kind, source, offset, structural, paragraph = _find_exact_target(master, selected)
    before, after = _context(source, offset, len(selected))
    item = LocalAnnotation(
        _id(annotation_id or new_id("lan")), _project_id(project_id), "text", _text("feedback", feedback), _categories(categories),
        selected, before, after, structural, paragraph, None, master.version, master_hash(master), master.version,
        master_hash(master), "active", None, _timestamp("created_at", now), _timestamp("updated_at", now),
    )
    return _append(project_id, item, projects_root)


def create_image_annotation(project_id: str, *, asset_id: str, feedback: str, categories: tuple[str, ...], now: str,
                            projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                            annotation_id: str | None = None) -> LocalAnnotation:
    master = _required_master(project_id, projects_root)
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    asset = next((item for item in plan.assets if item.id == _asset_id(asset_id)), None)
    if asset is None:
        raise LocalAnnotationError(f"visual asset not found: {asset_id}")
    if asset.status != "selected" or asset.file_path is None:
        raise LocalAnnotationError("only a selected visible visual asset can be annotated")
    slot = next(item for item in plan.slots if item.id == asset.slot_id)
    if slot.paragraph_anchor is None:
        raise LocalAnnotationError("visual asset has no contextual paragraph")
    item = LocalAnnotation(
        _id(annotation_id or new_id("lan")), _project_id(project_id), "image", _text("feedback", feedback), _categories(categories),
        None, None, None, None, _text("paragraph_anchor", slot.paragraph_anchor), asset.id, master.version, master_hash(master),
        master.version, master_hash(master), "active", None, _timestamp("created_at", now), _timestamp("updated_at", now),
    )
    return _append(project_id, item, projects_root)


def load_annotations(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[LocalAnnotation, ...]:
    project_id = _project_id(project_id)
    _ensure_project(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalAnnotationError("invalid local annotations JSON") from exc
    if not isinstance(payload, list):
        raise LocalAnnotationError("local annotations manifest must be an array")
    items = tuple(_from_payload(item, project_id) for item in payload)
    if len({item.id for item in items}) != len(items):
        raise LocalAnnotationError("duplicate local annotation id")
    return items


def resolve_annotation(project_id: str, annotation_id: str, *, now: str,
                       projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> LocalAnnotation:
    project_id = _project_id(project_id); annotation_id = _id(annotation_id)
    with _lock(project_id):
        master = _required_master(project_id, projects_root)
        items = load_annotations(project_id, projects_root=projects_root)
        found = _find(items, annotation_id)
        updated = _resolve(found, master, now, projects_root)
        if updated != found:
            _write(project_id, tuple(updated if item.id == annotation_id else item for item in items), projects_root)
        return updated


def resolve_all_annotations(project_id: str, *, now: str,
                            projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[LocalAnnotation, ...]:
    project_id = _project_id(project_id)
    with _lock(project_id):
        master = _required_master(project_id, projects_root)
        items = load_annotations(project_id, projects_root=projects_root)
        resolved = tuple(_resolve(item, master, now, projects_root) for item in items)
        if resolved != items:
            _write(project_id, resolved, projects_root)
        return resolved


def remove_annotation(project_id: str, annotation_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> None:
    project_id = _project_id(project_id); annotation_id = _id(annotation_id)
    with _lock(project_id):
        items = load_annotations(project_id, projects_root=projects_root)
        _find(items, annotation_id)
        _write(project_id, tuple(item for item in items if item.id != annotation_id), projects_root)


def _resolve(item: LocalAnnotation, master: master_documents.MasterDocument, now: str, projects_root: str | Path) -> LocalAnnotation:
    digest = master_hash(master)
    if item.resolved_version == master.version and item.resolved_hash == digest:
        return item
    if item.kind == "image":
        # The asset id is immutable; an image replacement is intentionally not
        # reattached to a different image by a best-effort heuristic.
        try:
            known = {asset.id for asset in visuals.load_visuals(item.project_id, projects_root=projects_root).assets}
        except visuals.VisualsError:
            known = set()
        valid = item.asset_id in known
    else:
        valid = _unique_reanchor(item, master)
    if valid:
        return replace(item, resolved_version=master.version, resolved_hash=digest, status="active", orphan_reason=None,
                       updated_at=_timestamp("updated_at", now))
    return replace(item, status="orphaned", orphan_reason="文章已变化，无法唯一定位原批注；请确认后重试或移除。",
                   updated_at=_timestamp("updated_at", now))


def _unique_reanchor(item: LocalAnnotation, master: master_documents.MasterDocument) -> bool:
    assert item.excerpt is not None
    candidates: list[tuple[str, int, str]] = []
    for source_name, source in (("title", master.title), ("body", master.body)):
        start = 0
        while True:
            at = source.find(item.excerpt, start)
            if at < 0: break
            candidates.append((source_name, at, source)); start = at + 1
    if len(candidates) == 1:
        return True
    if not candidates:
        return False
    scores: list[tuple[int, int]] = []
    for source_name, at, source in candidates:
        context_score = _suffix_match(item.context_before or "", source[max(0, at - 96):at]) + _prefix_match(item.context_after or "", source[at + len(item.excerpt):at + len(item.excerpt) + 96])
        # A paragraph ordinal is supporting evidence, never proof on its own:
        # insertions/deletions can shift it to a semantically different target.
        scores.append((context_score, context_score + (16 if context_score and item.structural_anchor == _structural_anchor(source_name, master.body, at) else 0)))
    best_context = max(score[0] for score in scores)
    best = max(score[1] for score in scores)
    # One shared punctuation character is not sufficient evidence to migrate a
    # human instruction.  Require a meaningful context run as well as a unique
    # best candidate.
    return best_context >= 4 and sum(score[1] == best for score in scores) == 1


def _find_exact_target(master: master_documents.MasterDocument, excerpt: str) -> tuple[str, str, int, str, str | None]:
    matches: list[tuple[str, str, int]] = []
    for kind, source in (("title", master.title), ("body", master.body)):
        start = 0
        while True:
            at = source.find(excerpt, start)
            if at < 0: break
            matches.append((kind, source, at)); start = at + 1
    if len(matches) != 1:
        raise LocalAnnotationError("selection must occur exactly once in the current article")
    kind, source, at = matches[0]
    paragraph = _paragraph(master.body, at) if kind == "body" else None
    return kind, source, at, _structural_anchor(kind, master.body, at), paragraph


def _structural_anchor(kind: str, body: str, offset: int) -> str:
    if kind == "title": return "title"
    return f"body:{body[:offset].count(chr(10) + chr(10))}"


def _paragraph(body: str, offset: int) -> str:
    start = body.rfind("\n\n", 0, offset) + 2
    end = body.find("\n\n", offset)
    return body[start:] if end < 0 else body[start:end]


def _context(source: str, offset: int, length: int) -> tuple[str, str]:
    return source[max(0, offset - 96):offset], source[offset + length:offset + length + 96]


def _suffix_match(expected: str, actual: str) -> int:
    amount = 0
    for left, right in zip(reversed(expected), reversed(actual)):
        if left != right: break
        amount += 1
    return amount


def _prefix_match(expected: str, actual: str) -> int:
    amount = 0
    for left, right in zip(expected, actual):
        if left != right: break
        amount += 1
    return amount


def _append(project_id: str, item: LocalAnnotation, projects_root: str | Path) -> LocalAnnotation:
    with _lock(project_id):
        items = load_annotations(project_id, projects_root=projects_root)
        if any(existing.id == item.id for existing in items):
            raise LocalAnnotationError(f"local annotation already exists: {item.id}")
        _write(project_id, (*items, item), projects_root)
    return item


def _write(project_id: str, items: tuple[LocalAnnotation, ...], projects_root: str | Path) -> None:
    path = _path(projects_root, project_id); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _from_payload(payload: Any, project_id: str) -> LocalAnnotation:
    if not isinstance(payload, dict) or set(payload) != set(LocalAnnotation.__dataclass_fields__):
        raise LocalAnnotationError("local annotation has missing or unknown fields")
    item = LocalAnnotation(_id(payload["id"]), _project_id(payload["project_id"]), payload["kind"], _text("feedback", payload["feedback"]),
                           _categories(payload["categories"]), _optional("excerpt", payload["excerpt"]), _optional("context_before", payload["context_before"]),
                           _optional("context_after", payload["context_after"]), _optional("structural_anchor", payload["structural_anchor"]),
                           _optional("paragraph_anchor", payload["paragraph_anchor"]), _optional_asset(payload["asset_id"]), _version(payload["source_version"]),
                           _hash(payload["source_hash"]), _version(payload["resolved_version"]), _hash(payload["resolved_hash"]), payload["status"],
                           _optional("orphan_reason", payload["orphan_reason"]), _timestamp("created_at", payload["created_at"]), _timestamp("updated_at", payload["updated_at"]))
    if item.project_id != project_id or item.kind not in _KINDS or item.status not in _STATUSES:
        raise LocalAnnotationError("local annotation project, kind or status is invalid")
    if item.kind == "text" and (not item.excerpt or item.asset_id is not None or not item.structural_anchor):
        raise LocalAnnotationError("text annotation evidence is invalid")
    if item.kind == "image" and (item.asset_id is None or item.excerpt is not None or not item.paragraph_anchor):
        raise LocalAnnotationError("image annotation evidence is invalid")
    if item.status == "active" and item.orphan_reason is not None: raise LocalAnnotationError("active annotation cannot have orphan reason")
    if item.status == "orphaned" and item.orphan_reason is None: raise LocalAnnotationError("orphaned annotation requires a reason")
    return item


def _required_master(project_id: str, root: str | Path) -> master_documents.MasterDocument:
    try: master = master_documents.load_master(_project_id(project_id), projects_root=root)
    except master_documents.MasterDocumentError as exc: raise LocalAnnotationError(str(exc)) from exc
    if master is None: raise LocalAnnotationError(f"master not found: {project_id}")
    return master
def _ensure_project(project_id: str, root: str | Path) -> None:
    try: project_store.load_project(project_id, projects_root=root)
    except project_store.ProjectManifestError as exc: raise LocalAnnotationError(str(exc)) from exc
def _path(root: str | Path, project_id: str) -> Path: return Path(root) / _project_id(project_id) / _NAME
def _lock(project_id: str) -> threading.Lock:
    with _LOCKS_GUARD: return _LOCKS.setdefault(project_id, threading.Lock())
def _find(items: tuple[LocalAnnotation, ...], annotation_id: str) -> LocalAnnotation:
    for item in items:
        if item.id == annotation_id: return item
    raise LocalAnnotationError(f"local annotation not found: {annotation_id}")
def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"): raise LocalAnnotationError(f"invalid project id: {value!r}")
    return value
def _id(value: Any) -> str:
    if not valid_sidecar_id(value, "lan_"): raise LocalAnnotationError(f"invalid local annotation id: {value!r}")
    return value
def _asset_id(value: Any) -> str:
    if not valid_sidecar_id(value, "vas_"): raise LocalAnnotationError(f"invalid visual asset id: {value!r}")
    return value
def _optional_asset(value: Any) -> str | None: return None if value is None else _asset_id(value)
def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 8_000: raise LocalAnnotationError(f"{name} must be non-empty text")
    return value.strip()
def _optional(name: str, value: Any) -> str | None: return None if value is None else _text(name, value)
def _categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or any(not isinstance(item, str) or item not in _CATEGORIES for item in value): raise LocalAnnotationError("invalid annotation categories")
    result = tuple(value)
    if len(result) != len(set(result)): raise LocalAnnotationError("duplicate annotation category")
    return result
def _version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1: raise LocalAnnotationError("invalid master version")
    return value
def _hash(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value): raise LocalAnnotationError("invalid master hash")
    return value
def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str): raise LocalAnnotationError(f"{name} must be an ISO8601 string")
    try: parsed = datetime.fromisoformat(value)
    except ValueError as exc: raise LocalAnnotationError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None: raise LocalAnnotationError(f"{name} must include a timezone")
    return value
