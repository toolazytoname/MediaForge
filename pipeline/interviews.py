"""Project interview sidecar. Confirmed author viewpoint/experience is required
before an AI master draft. Book titles without excerpts are unread books.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pipeline.projects import DEFAULT_PROJECTS_ROOT, ProjectManifestError, load_project
from pipeline.utils.sidecar_ids import valid_sidecar_id


_MANIFEST_NAME = "interview.json"
_SOURCE_KINDS = frozenset({"url", "excerpt", "book", "note"})


class InterviewError(ValueError):
    """Interview missing, incomplete, or not confirmed."""


@dataclass(frozen=True)
class InterviewSource:
    kind: str
    title: str
    reference: str
    excerpt: str


@dataclass(frozen=True)
class ProjectInterview:
    project_id: str
    viewpoint: str
    motive: str
    experience: str
    sources: tuple[InterviewSource, ...]
    unread_books: tuple[str, ...]
    confirmed: bool
    created_at: str
    updated_at: str


def save_interview(
    project_id: str,
    *,
    viewpoint: str,
    motive: str,
    experience: str,
    sources: Iterable[dict[str, str] | InterviewSource],
    now: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> ProjectInterview:
    _ensure_project(project_id, projects_root)
    parsed_sources = tuple(_source(item) for item in sources)
    unread = tuple(
        item.title for item in parsed_sources
        if item.kind == "book" and not item.excerpt.strip()
    )
    try:
        existing = load_interview(project_id, projects_root=projects_root)
        created_at = existing.created_at
    except InterviewError:
        created_at = now
    interview = ProjectInterview(
        project_id=project_id,
        viewpoint=_required("viewpoint", viewpoint),
        motive=_required("motive", motive),
        experience=_required("experience", experience),
        sources=parsed_sources,
        unread_books=unread,
        confirmed=False,
        created_at=_timestamp(created_at),
        updated_at=_timestamp(now),
    )
    _write(_path(projects_root, project_id), interview)
    return interview


def load_interview(
    project_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> ProjectInterview:
    path = _path(projects_root, project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InterviewError(f"interview not confirmed for {project_id}") from exc
    except json.JSONDecodeError as exc:
        raise InterviewError(f"invalid interview JSON: {project_id}") from exc
    return _from_payload(payload, expected_id=project_id)


def confirm_interview(
    project_id: str, *, now: str, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> ProjectInterview:
    current = load_interview(project_id, projects_root=projects_root)
    _required("viewpoint", current.viewpoint)
    _required("motive", current.motive)
    _required("experience", current.experience)
    confirmed = ProjectInterview(
        project_id=current.project_id,
        viewpoint=current.viewpoint,
        motive=current.motive,
        experience=current.experience,
        sources=current.sources,
        unread_books=current.unread_books,
        confirmed=True,
        created_at=current.created_at,
        updated_at=_timestamp(now),
    )
    _write(_path(projects_root, project_id), confirmed)
    return confirmed


def require_confirmed_interview(
    project_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> ProjectInterview:
    try:
        interview = load_interview(project_id, projects_root=projects_root)
    except InterviewError as exc:
        raise InterviewError(f"interview not confirmed for {project_id}") from exc
    if not interview.confirmed:
        raise InterviewError(f"interview not confirmed for {project_id}")
    return interview


def _ensure_project(project_id: str, projects_root: str | Path) -> None:
    if not valid_sidecar_id(project_id, "prj_"):
        raise InterviewError(f"invalid project id: {project_id!r}")
    try:
        load_project(project_id, projects_root=projects_root)
    except ProjectManifestError as exc:
        raise InterviewError(f"project not found: {project_id}") from exc


def _source(value: Any) -> InterviewSource:
    if isinstance(value, InterviewSource):
        return value
    if not isinstance(value, dict):
        raise InterviewError("interview source must be an object")
    kind = value.get("kind") or "note"
    if kind not in _SOURCE_KINDS:
        raise InterviewError(f"invalid interview source kind: {kind!r}")
    title = _required("source title", value.get("title") or "")
    reference = str(value.get("reference") or title).strip()
    excerpt = str(value.get("excerpt") or "").strip()
    return InterviewSource(kind=str(kind), title=title, reference=reference, excerpt=excerpt)


def _path(root: str | Path, project_id: str) -> Path:
    return Path(root) / project_id / _MANIFEST_NAME


def _write(path: Path, interview: ProjectInterview) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = asdict(interview)
    payload["sources"] = [asdict(item) for item in interview.sources]
    payload["unread_books"] = list(interview.unread_books)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _from_payload(payload: Any, *, expected_id: str) -> ProjectInterview:
    if not isinstance(payload, dict) or payload.get("project_id") != expected_id:
        raise InterviewError("interview project_id does not match directory")
    sources = tuple(_source(item) for item in payload.get("sources") or ())
    unread = tuple(payload.get("unread_books") or (
        item.title for item in sources if item.kind == "book" and not item.excerpt
    ))
    return ProjectInterview(
        project_id=expected_id,
        viewpoint=_required("viewpoint", payload.get("viewpoint") or ""),
        motive=_required("motive", payload.get("motive") or ""),
        experience=_required("experience", payload.get("experience") or ""),
        sources=sources,
        unread_books=unread,
        confirmed=bool(payload.get("confirmed")),
        created_at=_timestamp(payload.get("created_at")),
        updated_at=_timestamp(payload.get("updated_at")),
    )


def _required(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InterviewError(f"{name} is required")
    return value.strip()


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise InterviewError("timestamp must be an ISO8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise InterviewError("timestamp must include a timezone")
    return value


__all__ = [
    "InterviewError",
    "InterviewSource",
    "ProjectInterview",
    "confirm_interview",
    "load_interview",
    "require_confirmed_interview",
    "save_interview",
]
