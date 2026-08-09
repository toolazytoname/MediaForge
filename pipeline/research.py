"""Project-scoped research sidecar storage.

Research is deliberately separate from the frozen Project v0 manifest and the
SQLite pipeline.  It records only material a creator explicitly enters; this
module never fetches a URL, invokes an LLM, or infers verification status.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pipeline import projects as project_store
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


_MANIFEST_NAME = "research.json"
_KINDS = frozenset({"fact", "judgment", "open_question"})
_STATUSES_BY_KIND = {
    "fact": frozenset({"unverified", "verified"}),
    "judgment": frozenset({"unverified", "verified"}),
    "open_question": frozenset({"open", "resolved"}),
}


class ResearchManifestError(ValueError):
    """Research sidecar is missing, malformed, or violates its strict schema."""


@dataclass(frozen=True)
class ResearchSource:
    id: str
    title: str
    reference: str
    summary: str
    entered_at: str
    updated_at: str


@dataclass(frozen=True)
class ResearchClaim:
    id: str
    text: str
    kind: str
    source_ids: tuple[str, ...]
    status: str
    limitation: str | None
    counterpoint: str | None
    entered_at: str
    updated_at: str


@dataclass(frozen=True)
class ResearchBoard:
    project_id: str
    sources: tuple[ResearchSource, ...]
    claims: tuple[ResearchClaim, ...]


def load_research(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ResearchBoard:
    """Load a validated board, or a non-persisted empty board for an existing Project."""
    _ensure_project(project_id, projects_root)
    path = _research_path(projects_root, project_id)
    if not path.exists():
        return ResearchBoard(project_id=project_id, sources=(), claims=())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResearchManifestError(f"invalid research JSON: {project_id}") from exc
    return _board_from_payload(payload, expected_project_id=project_id)


def add_source(
    project_id: str, *, title: str, reference: str, summary: str, now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT, source_id: str | None = None,
) -> ResearchSource:
    board = load_research(project_id, projects_root=projects_root)
    source = ResearchSource(
        id=_id("source id", source_id or new_id("src"), "src_"), title=_text("title", title),
        reference=_text("reference", reference), summary=_text("summary", summary),
        entered_at=_timestamp("entered_at", now), updated_at=_timestamp("updated_at", now),
    )
    if any(item.id == source.id for item in board.sources):
        raise ResearchManifestError(f"source already exists: {source.id}")
    _write_research(_research_path(projects_root, project_id), replace(board, sources=(*board.sources, source)))
    return source


def update_source(
    project_id: str, source_id: str, *, title: str, reference: str, summary: str, now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> ResearchSource:
    board = load_research(project_id, projects_root=projects_root)
    source_id = _id("source id", source_id, "src_")
    source = _find_source(board, source_id)
    updated = replace(
        source, title=_text("title", title), reference=_text("reference", reference),
        summary=_text("summary", summary), updated_at=_timestamp("updated_at", now),
    )
    _write_research(_research_path(projects_root, project_id), replace(
        board, sources=tuple(updated if item.id == source_id else item for item in board.sources),
    ))
    return updated


def add_claim(
    project_id: str, *, text: str, kind: str, source_ids: Iterable[str], status: str, now: str,
    limitation: str | None = None, counterpoint: str | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT, claim_id: str | None = None,
) -> ResearchClaim:
    board = load_research(project_id, projects_root=projects_root)
    claim = _claim(
        claim_id or new_id("clm"), text, kind, source_ids, status, limitation, counterpoint, now, now,
        board.sources,
    )
    if any(item.id == claim.id for item in board.claims):
        raise ResearchManifestError(f"claim already exists: {claim.id}")
    _write_research(_research_path(projects_root, project_id), replace(board, claims=(*board.claims, claim)))
    return claim


def update_claim(
    project_id: str, claim_id: str, *, text: str, kind: str, source_ids: Iterable[str], status: str, now: str,
    limitation: str | None = None, counterpoint: str | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> ResearchClaim:
    board = load_research(project_id, projects_root=projects_root)
    current = _find_claim(board, _id("claim id", claim_id, "clm_"))
    updated = _claim(current.id, text, kind, source_ids, status, limitation, counterpoint,
                     current.entered_at, now, board.sources)
    _write_research(_research_path(projects_root, project_id), replace(
        board, claims=tuple(updated if item.id == current.id else item for item in board.claims),
    ))
    return updated


def _ensure_project(project_id: str, projects_root: str | Path) -> None:
    try:
        project_store.load_project(project_id, projects_root=projects_root)
    except project_store.ProjectManifestError as exc:
        if str(exc) == f"project not found: {project_id}":
            raise ResearchManifestError(str(exc)) from exc
        raise ResearchManifestError(f"cannot read project: {exc}") from exc


def _research_path(projects_root: str | Path, project_id: str) -> Path:
    return Path(projects_root) / _id("project id", project_id, "prj_") / _MANIFEST_NAME


def _write_research(path: Path, board: ResearchBoard) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(board), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _board_from_payload(payload: Any, *, expected_project_id: str) -> ResearchBoard:
    if not isinstance(payload, dict) or set(payload) != set(ResearchBoard.__dataclass_fields__):
        raise ResearchManifestError("research manifest has missing or unknown fields")
    project_id = _id("project id", payload["project_id"], "prj_")
    if project_id != expected_project_id:
        raise ResearchManifestError("research manifest project id does not match its directory")
    sources_raw, claims_raw = payload["sources"], payload["claims"]
    if not isinstance(sources_raw, list) or not isinstance(claims_raw, list):
        raise ResearchManifestError("research sources and claims must be arrays")
    sources = tuple(_source_from_payload(item) for item in sources_raw)
    if len({item.id for item in sources}) != len(sources):
        raise ResearchManifestError("duplicate source id")
    claims = tuple(_claim_from_payload(item, sources) for item in claims_raw)
    if len({item.id for item in claims}) != len(claims):
        raise ResearchManifestError("duplicate claim id")
    return ResearchBoard(project_id=project_id, sources=sources, claims=claims)


def _source_from_payload(payload: Any) -> ResearchSource:
    if not isinstance(payload, dict) or set(payload) != set(ResearchSource.__dataclass_fields__):
        raise ResearchManifestError("research source has missing or unknown fields")
    source = ResearchSource(
        id=_id("source id", payload["id"], "src_"), title=_text("title", payload["title"]),
        reference=_text("reference", payload["reference"]), summary=_text("summary", payload["summary"]),
        entered_at=_timestamp("entered_at", payload["entered_at"]), updated_at=_timestamp("updated_at", payload["updated_at"]),
    )
    if datetime.fromisoformat(source.updated_at) < datetime.fromisoformat(source.entered_at):
        raise ResearchManifestError("source updated_at cannot be earlier than entered_at")
    return source


def _claim_from_payload(payload: Any, sources: tuple[ResearchSource, ...]) -> ResearchClaim:
    if not isinstance(payload, dict) or set(payload) != set(ResearchClaim.__dataclass_fields__):
        raise ResearchManifestError("research claim has missing or unknown fields")
    return _claim(payload["id"], payload["text"], payload["kind"], payload["source_ids"], payload["status"],
                  payload["limitation"], payload["counterpoint"], payload["entered_at"], payload["updated_at"], sources)


def _claim(claim_id: Any, text: Any, kind: Any, source_ids: Any, status: Any, limitation: Any,
           counterpoint: Any, entered_at: Any, updated_at: Any, sources: tuple[ResearchSource, ...]) -> ResearchClaim:
    kind = _kind(kind)
    claim = ResearchClaim(
        id=_id("claim id", claim_id, "clm_"), text=_text("text", text), kind=kind,
        source_ids=_source_ids(source_ids, sources), status=_status(kind, status),
        limitation=_optional_text("limitation", limitation), counterpoint=_optional_text("counterpoint", counterpoint),
        entered_at=_timestamp("entered_at", entered_at), updated_at=_timestamp("updated_at", updated_at),
    )
    if claim.kind == "fact" and claim.status == "verified" and not claim.source_ids:
        raise ResearchManifestError("a verified fact must reference at least one source")
    if datetime.fromisoformat(claim.updated_at) < datetime.fromisoformat(claim.entered_at):
        raise ResearchManifestError("claim updated_at cannot be earlier than entered_at")
    return claim


def _find_source(board: ResearchBoard, source_id: str) -> ResearchSource:
    for source in board.sources:
        if source.id == source_id:
            return source
    raise ResearchManifestError(f"source not found: {source_id}")


def _find_claim(board: ResearchBoard, claim_id: str) -> ResearchClaim:
    for claim in board.claims:
        if claim.id == claim_id:
            return claim
    raise ResearchManifestError(f"claim not found: {claim_id}")


def _source_ids(value: Any, sources: tuple[ResearchSource, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ResearchManifestError("source_ids must be an array of source ids")
    result = tuple(_id("source id", item, "src_") for item in value)
    if len(set(result)) != len(result):
        raise ResearchManifestError("duplicate source id reference")
    known = {source.id for source in sources}
    unknown = next((item for item in result if item not in known), None)
    if unknown:
        raise ResearchManifestError(f"unknown source reference: {unknown}")
    return result


def _kind(value: Any) -> str:
    if value not in _KINDS:
        raise ResearchManifestError("claim kind must be fact, judgment or open_question")
    return value


def _status(kind: str, value: Any) -> str:
    if value not in _STATUSES_BY_KIND[kind]:
        raise ResearchManifestError(f"{kind} status is invalid: {value!r}")
    return value


def _id(name: str, value: Any, prefix: str) -> str:
    if not valid_sidecar_id(value, prefix):
        raise ResearchManifestError(f"invalid {name}: {value!r}")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchManifestError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    return _text(name, value)


def _timestamp(name: str, value: Any) -> str:
    value = _text(name, value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResearchManifestError(f"{name} must be an ISO8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ResearchManifestError(f"{name} must include timezone")
    return value
