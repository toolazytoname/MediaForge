"""Immutable, project-scoped MasterDocument sidecars.

The Project v0 manifest remains deliberately unchanged.  A master document,
its bounded history, and pending AI proposals live next to it under
``output/projects/<project_id>/``.  AI output is never written as the current
document: a proposal has to be explicitly accepted first.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import projects as project_store
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


_MASTER_NAME = "master.json"
_MARKDOWN_NAME = "master.md"
_SUGGESTIONS_NAME = "suggestions.json"
HISTORY_LIMIT = 20
_ACTIONS = frozenset({"clarify", "shorten", "change_voice", "add_counterpoint"})


class MasterDocumentError(ValueError):
    """A MasterDocument sidecar is missing, malformed, or unsafe to change."""


@dataclass(frozen=True)
class MasterSnapshot:
    version: int
    title: str
    body: str
    saved_at: str
    reason: str


@dataclass(frozen=True)
class MasterDocument:
    project_id: str
    title: str
    body: str
    version: int
    created_at: str
    updated_at: str
    history: tuple[MasterSnapshot, ...]


@dataclass(frozen=True)
class MasterSuggestion:
    id: str
    project_id: str
    action: str
    selection: str | None
    base_version: int
    proposed_title: str
    proposed_body: str
    status: str
    created_at: str
    decided_at: str | None


def load_master(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument | None:
    """Read a strict master sidecar; an existing project may still be blank."""
    _ensure_project(project_id, projects_root)
    path = _path(projects_root, project_id, _MASTER_NAME)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MasterDocumentError(f"invalid master JSON: {project_id}") from exc
    master = _master_from_payload(payload, expected_project_id=project_id)
    markdown = _path(projects_root, project_id, _MARKDOWN_NAME)
    if not markdown.exists() or markdown.read_text(encoding="utf-8") != _markdown(master):
        raise MasterDocumentError("master markdown does not match its manifest")
    return master


def save_manual(project_id: str, *, title: str, body: str, now: str,
                projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument:
    """Save a human edit as the next immutable version."""
    current = load_master(project_id, projects_root=projects_root)
    return _write_next(project_id, title=title, body=body, now=now, reason="manual", current=current,
                       projects_root=projects_root)


def save_feedback_acceptance(project_id: str, *, proposal_id: str, title: str, body: str, now: str,
                             projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument:
    """Create a new master version after an explicitly accepted article proposal.

    This deliberately shares the same immutable writer as a manual save.  The
    reason is retained in history so the proposal remains traceable without
    changing the Project or SQLite contracts.
    """
    if not valid_sidecar_id(proposal_id, "afp_"):
        raise MasterDocumentError(f"invalid feedback proposal id: {proposal_id!r}")
    current = _required_master(project_id, projects_root)
    return _write_next(project_id, title=title, body=body, now=now, reason=f"feedback:{proposal_id}", current=current,
                       projects_root=projects_root)


def save_image_replacement(project_id: str, *, current_asset_id: str, candidate_asset_id: str,
                           title: str, body: str, now: str,
                           projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument:
    """Append an explicitly accepted article-image replacement to history.

    Asset IDs are retained in the immutable reason only for auditability; the
    visual files and the prior Markdown snapshot are never overwritten.
    """
    if not valid_sidecar_id(current_asset_id, "vas_") or not valid_sidecar_id(candidate_asset_id, "vas_"):
        raise MasterDocumentError("image replacement requires valid visual asset ids")
    current = _required_master(project_id, projects_root)
    return _write_next(project_id, title=title, body=body, now=now,
                       reason=f"image:{current_asset_id}->{candidate_asset_id}", current=current,
                       projects_root=projects_root)


def restore_version(project_id: str, version: int, *, now: str,
                    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument:
    """Restore a historical version by creating a new version, never rewinding."""
    current = _required_master(project_id, projects_root)
    snapshot = _find_version(current, version)
    return _write_next(project_id, title=snapshot.title, body=snapshot.body, now=now,
                       reason=f"restore:{version}", current=current, projects_root=projects_root)


def list_versions(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[MasterSnapshot, ...]:
    master = _required_master(project_id, projects_root)
    return (*master.history, MasterSnapshot(master.version, master.title, master.body, master.updated_at, "current"))


def create_suggestion(project_id: str, *, action: str, selection: str | None,
                      proposed_title: str, proposed_body: str, now: str,
                      projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                      suggestion_id: str | None = None) -> MasterSuggestion:
    """Persist a pending proposal without changing the MasterDocument."""
    master = _required_master(project_id, projects_root)
    suggestion = MasterSuggestion(
        id=_id("suggestion id", suggestion_id or new_id("sug"), "sug_"),
        project_id=_project_id(project_id), action=_action(action), selection=_optional_text("selection", selection),
        base_version=master.version, proposed_title=_text("proposed_title", proposed_title),
        proposed_body=_text("proposed_body", proposed_body), status="pending",
        created_at=_timestamp("created_at", now), decided_at=None,
    )
    if suggestion.selection is not None and suggestion.selection not in master.body:
        raise MasterDocumentError("selection is not present in the current master body")
    suggestions = load_suggestions(project_id, projects_root=projects_root)
    if any(item.id == suggestion.id for item in suggestions):
        raise MasterDocumentError(f"suggestion already exists: {suggestion.id}")
    _write_json(_path(projects_root, project_id, _SUGGESTIONS_NAME), [asdict(item) for item in (*suggestions, suggestion)])
    return suggestion


def load_suggestions(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[MasterSuggestion, ...]:
    _ensure_project(project_id, projects_root)
    path = _path(projects_root, project_id, _SUGGESTIONS_NAME)
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MasterDocumentError(f"invalid suggestions JSON: {project_id}") from exc
    if not isinstance(payload, list):
        raise MasterDocumentError("suggestions manifest must be an array")
    suggestions = tuple(_suggestion_from_payload(item, expected_project_id=project_id) for item in payload)
    if len({item.id for item in suggestions}) != len(suggestions):
        raise MasterDocumentError("duplicate suggestion id")
    return suggestions


def accept_suggestion(project_id: str, suggestion_id: str, *, now: str,
                      projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterDocument:
    current = _required_master(project_id, projects_root)
    suggestions = load_suggestions(project_id, projects_root=projects_root)
    suggestion = _find_suggestion(suggestions, suggestion_id)
    if suggestion.status != "pending":
        raise MasterDocumentError(f"suggestion is already {suggestion.status}: {suggestion.id}")
    if suggestion.base_version != current.version:
        raise MasterDocumentError("suggestion is stale; request a new proposal from the current version")
    updated = _write_next(project_id, title=suggestion.proposed_title, body=suggestion.proposed_body, now=now,
                          reason=f"suggestion:{suggestion.id}", current=current, projects_root=projects_root)
    _write_suggestions(project_id, _decide(suggestions, suggestion.id, "accepted", now), projects_root)
    return updated


def reject_suggestion(project_id: str, suggestion_id: str, *, now: str,
                      projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> MasterSuggestion:
    """Reject only changes proposal metadata: master files are deliberately untouched."""
    suggestions = load_suggestions(project_id, projects_root=projects_root)
    suggestion = _find_suggestion(suggestions, suggestion_id)
    if suggestion.status != "pending":
        raise MasterDocumentError(f"suggestion is already {suggestion.status}: {suggestion.id}")
    updated = _decide(suggestions, suggestion.id, "rejected", now)
    _write_suggestions(project_id, updated, projects_root)
    return _find_suggestion(updated, suggestion.id)


def _write_next(project_id: str, *, title: str, body: str, now: str, reason: str,
                current: MasterDocument | None, projects_root: str | Path) -> MasterDocument:
    timestamp = _timestamp("updated_at", now)
    if current is None:
        result = MasterDocument(_project_id(project_id), _text("title", title), _text("body", body), 1,
                                timestamp, timestamp, ())
    else:
        snapshot = MasterSnapshot(current.version, current.title, current.body, current.updated_at, reason)
        history = (*current.history, snapshot)[-HISTORY_LIMIT:]
        result = MasterDocument(current.project_id, _text("title", title), _text("body", body), current.version + 1,
                                current.created_at, timestamp, history)
    _write_master(projects_root, result)
    return result


def _write_master(projects_root: str | Path, master: MasterDocument) -> None:
    # Both mirrors use atomic replace. JSON is the authoritative record and is
    # replaced last, so a reader never observes a manifest pointing at old text.
    _write_text(_path(projects_root, master.project_id, _MARKDOWN_NAME), _markdown(master))
    _write_json(_path(projects_root, master.project_id, _MASTER_NAME), asdict(master))


def _write_suggestions(project_id: str, suggestions: tuple[MasterSuggestion, ...], projects_root: str | Path) -> None:
    _write_json(_path(projects_root, project_id, _SUGGESTIONS_NAME), [asdict(item) for item in suggestions])


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _markdown(master: MasterDocument) -> str:
    return f"# {master.title}\n\n{master.body}\n"


def _required_master(project_id: str, projects_root: str | Path) -> MasterDocument:
    master = load_master(project_id, projects_root=projects_root)
    if master is None:
        raise MasterDocumentError(f"master not found: {project_id}")
    return master


def _ensure_project(project_id: str, projects_root: str | Path) -> None:
    try:
        project_store.load_project(_project_id(project_id), projects_root=projects_root)
    except project_store.ProjectManifestError as exc:
        if str(exc) == f"project not found: {project_id}":
            raise MasterDocumentError(str(exc)) from exc
        raise MasterDocumentError(f"cannot read project: {exc}") from exc


def _path(projects_root: str | Path, project_id: str, name: str) -> Path:
    return Path(projects_root) / _project_id(project_id) / name


def _master_from_payload(payload: Any, *, expected_project_id: str) -> MasterDocument:
    if not isinstance(payload, dict) or set(payload) != set(MasterDocument.__dataclass_fields__):
        raise MasterDocumentError("master manifest has missing or unknown fields")
    project_id = _project_id(payload["project_id"])
    if project_id != expected_project_id:
        raise MasterDocumentError("master project id does not match its directory")
    history_raw = payload["history"]
    if not isinstance(history_raw, list):
        raise MasterDocumentError("master history must be an array")
    history = tuple(_snapshot_from_payload(item) for item in history_raw)
    master = MasterDocument(project_id, _text("title", payload["title"]), _text("body", payload["body"]),
                            _version(payload["version"]), _timestamp("created_at", payload["created_at"]),
                            _timestamp("updated_at", payload["updated_at"]), history)
    if datetime.fromisoformat(master.updated_at) < datetime.fromisoformat(master.created_at):
        raise MasterDocumentError("master updated_at cannot be earlier than created_at")
    if len(history) > HISTORY_LIMIT:
        raise MasterDocumentError("master history exceeds its snapshot limit")
    versions = tuple(item.version for item in history)
    if versions != tuple(sorted(versions)) or len(set(versions)) != len(versions) or any(version >= master.version for version in versions):
        raise MasterDocumentError("master history versions are invalid")
    return master


def _snapshot_from_payload(payload: Any) -> MasterSnapshot:
    if not isinstance(payload, dict) or set(payload) != set(MasterSnapshot.__dataclass_fields__):
        raise MasterDocumentError("master snapshot has missing or unknown fields")
    return MasterSnapshot(_version(payload["version"]), _text("title", payload["title"]), _text("body", payload["body"]),
                          _timestamp("saved_at", payload["saved_at"]), _text("reason", payload["reason"]))


def _suggestion_from_payload(payload: Any, *, expected_project_id: str) -> MasterSuggestion:
    if not isinstance(payload, dict) or set(payload) != set(MasterSuggestion.__dataclass_fields__):
        raise MasterDocumentError("suggestion has missing or unknown fields")
    suggestion = MasterSuggestion(_id("suggestion id", payload["id"], "sug_"), _project_id(payload["project_id"]),
        _action(payload["action"]), _optional_text("selection", payload["selection"]), _version(payload["base_version"]),
        _text("proposed_title", payload["proposed_title"]), _text("proposed_body", payload["proposed_body"]),
        payload["status"], _timestamp("created_at", payload["created_at"]), _optional_timestamp("decided_at", payload["decided_at"]))
    if suggestion.project_id != expected_project_id:
        raise MasterDocumentError("suggestion project id does not match its directory")
    if suggestion.status not in {"pending", "accepted", "rejected"}:
        raise MasterDocumentError("suggestion status is invalid")
    if (suggestion.status == "pending") != (suggestion.decided_at is None):
        raise MasterDocumentError("suggestion decision timestamp is invalid")
    return suggestion


def _decide(items: tuple[MasterSuggestion, ...], suggestion_id: str, status: str, now: str) -> tuple[MasterSuggestion, ...]:
    timestamp = _timestamp("decided_at", now)
    return tuple(replace(item, status=status, decided_at=timestamp) if item.id == suggestion_id else item for item in items)


def _find_version(master: MasterDocument, version: int) -> MasterSnapshot:
    version = _version(version)
    for item in (*master.history, MasterSnapshot(master.version, master.title, master.body, master.updated_at, "current")):
        if item.version == version:
            return item
    raise MasterDocumentError(f"master version not found: {version}")


def _find_suggestion(items: tuple[MasterSuggestion, ...], suggestion_id: str) -> MasterSuggestion:
    suggestion_id = _id("suggestion id", suggestion_id, "sug_")
    for item in items:
        if item.id == suggestion_id:
            return item
    raise MasterDocumentError(f"suggestion not found: {suggestion_id}")


def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"):
        raise MasterDocumentError(f"invalid project id: {value!r}")
    return value


def _id(name: str, value: Any, prefix: str) -> str:
    if not valid_sidecar_id(value, prefix):
        raise MasterDocumentError(f"invalid {name}: {value!r}")
    return value


def _version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MasterDocumentError(f"invalid master version: {value!r}")
    return value


def _action(value: Any) -> str:
    if value not in _ACTIONS:
        raise MasterDocumentError("suggestion action must be clarify, shorten, change_voice or add_counterpoint")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MasterDocumentError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    return _text(name, value)


def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise MasterDocumentError(f"{name} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MasterDocumentError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None:
        raise MasterDocumentError(f"{name} must include a timezone")
    return value


def _optional_timestamp(name: str, value: Any) -> str | None:
    return None if value is None else _timestamp(name, value)
