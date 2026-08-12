"""Whole-article feedback proposals kept beside a Project master document.

This is deliberately separate from ``master_documents`` suggestions.  It
records what the author asked for (including audience/platform/value context),
and it never has an operation that writes a master document.  RV-03 decides
how a ready proposal is compared or accepted.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import master_documents, projects as project_store
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


_NAME = "article_feedback.json"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class ArticleFeedbackError(ValueError):
    """A feedback proposal sidecar is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class ArticleFeedbackProposal:
    id: str
    project_id: str
    scope: str
    feedback: str
    target: str | None
    readership: str | None
    platform: str | None
    values: str | None
    base_version: int
    base_hash: str
    status: str
    proposed_title: str | None
    proposed_body: str | None
    error: str | None
    created_at: str
    updated_at: str


def create_proposal(project_id: str, *, feedback: str, target: str | None, readership: str | None,
                    platform: str | None, values: str | None, proposed_title: str, proposed_body: str,
                    now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                    proposal_id: str | None = None, base_version: int | None = None,
                    base_hash: str | None = None) -> ArticleFeedbackProposal:
    master = _required_master(project_id, projects_root)
    _check_base(master, base_version, base_hash)
    proposal = ArticleFeedbackProposal(
        _id(proposal_id or new_id("afp")), _project_id(project_id), "whole_article", _text("feedback", feedback),
        _optional("target", target), _optional("readership", readership), _optional("platform", platform), _optional("values", values),
        master.version, master_hash(master), "ready", _text("proposed_title", proposed_title), _text("proposed_body", proposed_body),
        None, _timestamp("created_at", now), _timestamp("updated_at", now),
    )
    return _append(project_id, proposal, projects_root)


def create_failed_proposal(project_id: str, *, feedback: str, target: str | None, readership: str | None,
                           platform: str | None, values: str | None, error: str, now: str,
                           projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                           proposal_id: str | None = None, base_version: int | None = None,
                           base_hash: str | None = None) -> ArticleFeedbackProposal:
    master = _required_master(project_id, projects_root)
    _check_base(master, base_version, base_hash)
    proposal = ArticleFeedbackProposal(
        _id(proposal_id or new_id("afp")), _project_id(project_id), "whole_article", _text("feedback", feedback),
        _optional("target", target), _optional("readership", readership), _optional("platform", platform), _optional("values", values),
        master.version, master_hash(master), "failed", None, None, _text("error", error),
        _timestamp("created_at", now), _timestamp("updated_at", now),
    )
    return _append(project_id, proposal, projects_root)


def complete_failed_proposal(project_id: str, proposal_id: str, *, proposed_title: str, proposed_body: str,
                             now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ArticleFeedbackProposal:
    project_id = _project_id(project_id); proposal_id = _id(proposal_id)
    with _lock(project_id):
        items = load_proposals(project_id, projects_root=projects_root)
        found = _find(items, proposal_id)
        if found.status != "failed":
            raise ArticleFeedbackError("only a failed feedback proposal can be retried")
        updated = replace(found, status="ready", proposed_title=_text("proposed_title", proposed_title),
                          proposed_body=_text("proposed_body", proposed_body), error=None, updated_at=_timestamp("updated_at", now))
        _write(project_id, tuple(updated if item.id == proposal_id else item for item in items), projects_root)
        return updated


def load_proposals(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[ArticleFeedbackProposal, ...]:
    project_id = _project_id(project_id)
    _ensure_project(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArticleFeedbackError("invalid feedback JSON") from exc
    if not isinstance(raw, list):
        raise ArticleFeedbackError("feedback manifest must be an array")
    result = tuple(_from_payload(item, project_id) for item in raw)
    if len({item.id for item in result}) != len(result):
        raise ArticleFeedbackError("duplicate feedback proposal id")
    return result


def proposal_state(proposal: ArticleFeedbackProposal, master: master_documents.MasterDocument) -> str:
    """A deterministic comparison, intentionally computed rather than mutable."""
    return "current" if proposal.base_version == master.version and proposal.base_hash == master_hash(master) else "obsolete"


def master_hash(master: master_documents.MasterDocument) -> str:
    return hashlib.sha256(f"{master.title}\0{master.body}".encode("utf-8")).hexdigest()


def _append(project_id: str, proposal: ArticleFeedbackProposal, projects_root: str | Path) -> ArticleFeedbackProposal:
    with _lock(project_id):
        items = load_proposals(project_id, projects_root=projects_root)
        if any(item.id == proposal.id for item in items):
            raise ArticleFeedbackError(f"feedback proposal already exists: {proposal.id}")
        _write(project_id, (*items, proposal), projects_root)
    return proposal


def _write(project_id: str, items: tuple[ArticleFeedbackProposal, ...], projects_root: str | Path) -> None:
    path = _path(projects_root, project_id); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _required_master(project_id: str, projects_root: str | Path) -> master_documents.MasterDocument:
    try:
        master = master_documents.load_master(_project_id(project_id), projects_root=projects_root)
    except master_documents.MasterDocumentError as exc:
        raise ArticleFeedbackError(str(exc)) from exc
    if master is None:
        raise ArticleFeedbackError(f"master not found: {project_id}")
    return master


def _ensure_project(project_id: str, projects_root: str | Path) -> None:
    try:
        project_store.load_project(project_id, projects_root=projects_root)
    except project_store.ProjectManifestError as exc:
        raise ArticleFeedbackError(str(exc)) from exc


def _from_payload(payload: Any, expected_project_id: str) -> ArticleFeedbackProposal:
    if not isinstance(payload, dict) or set(payload) != set(ArticleFeedbackProposal.__dataclass_fields__):
        raise ArticleFeedbackError("feedback proposal has missing or unknown fields")
    item = ArticleFeedbackProposal(
        _id(payload["id"]), _project_id(payload["project_id"]), payload["scope"], _text("feedback", payload["feedback"]),
        _optional("target", payload["target"]), _optional("readership", payload["readership"]), _optional("platform", payload["platform"]), _optional("values", payload["values"]),
        _version(payload["base_version"]), _hash(payload["base_hash"]), payload["status"], _nullable_text("proposed_title", payload["proposed_title"]),
        _nullable_text("proposed_body", payload["proposed_body"]), _nullable_text("error", payload["error"]),
        _timestamp("created_at", payload["created_at"]), _timestamp("updated_at", payload["updated_at"]),
    )
    if item.project_id != expected_project_id or item.scope != "whole_article":
        raise ArticleFeedbackError("feedback proposal project or scope is invalid")
    if item.status == "ready" and (item.proposed_title is None or item.proposed_body is None or item.error is not None):
        raise ArticleFeedbackError("ready feedback proposal is invalid")
    if item.status == "failed" and (item.proposed_title is not None or item.proposed_body is not None or item.error is None):
        raise ArticleFeedbackError("failed feedback proposal is invalid")
    if item.status not in {"ready", "failed"}:
        raise ArticleFeedbackError("feedback proposal status is invalid")
    return item


def _lock(project_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(project_id, threading.Lock())


def _find(items: tuple[ArticleFeedbackProposal, ...], proposal_id: str) -> ArticleFeedbackProposal:
    for item in items:
        if item.id == proposal_id:
            return item
    raise ArticleFeedbackError(f"feedback proposal not found: {proposal_id}")


def _check_base(master: master_documents.MasterDocument, version: int | None, digest: str | None) -> None:
    if version is not None and version != master.version:
        raise ArticleFeedbackError("master changed before feedback proposal was saved")
    if digest is not None and digest != master_hash(master):
        raise ArticleFeedbackError("master changed before feedback proposal was saved")


def _path(root: str | Path, project_id: str) -> Path: return Path(root) / _project_id(project_id) / _NAME
def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"): raise ArticleFeedbackError(f"invalid project id: {value!r}")
    return value
def _id(value: Any) -> str:
    if not valid_sidecar_id(value, "afp_"): raise ArticleFeedbackError(f"invalid feedback proposal id: {value!r}")
    return value
def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip(): raise ArticleFeedbackError(f"{name} must be a non-empty string")
    return value.strip()
def _nullable_text(name: str, value: Any) -> str | None: return None if value is None else _text(name, value)
def _optional(name: str, value: Any) -> str | None: return None if value is None else _text(name, value)
def _version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1: raise ArticleFeedbackError("invalid base version")
    return value
def _hash(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value): raise ArticleFeedbackError("invalid base hash")
    return value
def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str): raise ArticleFeedbackError(f"{name} must be an ISO8601 string")
    try: parsed = datetime.fromisoformat(value)
    except ValueError as exc: raise ArticleFeedbackError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None: raise ArticleFeedbackError(f"{name} must include a timezone")
    return value
