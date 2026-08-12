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

from pipeline import local_annotations, master_documents, projects as project_store
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
    decision: str | None
    decided_at: str | None
    accepted_title: str | None
    accepted_body: str | None
    annotation_id: str | None
    annotation_kind: str | None
    annotation_excerpt: str | None
    annotation_asset_id: str | None
    annotation_categories: tuple[str, ...]
    annotation_resolved_version: int | None
    annotation_resolved_hash: str | None
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
        None, None, None, None, None, None, None, None, None, (), None, None, _timestamp("created_at", now), _timestamp("updated_at", now),
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
        master.version, master_hash(master), "failed", None, None, _text("error", error), None, None, None, None,
        None, None, None, None, (), None, None, _timestamp("created_at", now), _timestamp("updated_at", now),
    )
    return _append(project_id, proposal, projects_root)


def create_local_proposal(project_id: str, *, annotation_id: str, proposed_title: str, proposed_body: str,
                          now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                          proposal_id: str | None = None) -> ArticleFeedbackProposal:
    """Persist an AI suggestion for one still-active, exactly resolved annotation."""
    master = _required_master(project_id, projects_root)
    annotation = _current_annotation(project_id, annotation_id, master, now, projects_root)
    proposal = _local_record(project_id, annotation, master, status="ready", proposed_title=proposed_title,
                             proposed_body=proposed_body, error=None, now=now, proposal_id=proposal_id)
    return _append(project_id, proposal, projects_root)


def create_failed_local_proposal(project_id: str, *, annotation_id: str, error: str, now: str,
                                 projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
                                 proposal_id: str | None = None) -> ArticleFeedbackProposal:
    master = _required_master(project_id, projects_root)
    annotation = _current_annotation(project_id, annotation_id, master, now, projects_root)
    proposal = _local_record(project_id, annotation, master, status="failed", proposed_title=None,
                             proposed_body=None, error=error, now=now, proposal_id=proposal_id)
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


def accept_proposal(project_id: str, proposal_id: str, *, title: str, body: str, now: str,
                    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ArticleFeedbackProposal:
    """Accept a current proposal as a new immutable master version.

    The comparison checks *both* the version and content hash immediately
    before the shared master writer is called.  A stale proposal is never
    applied, even if its suggested text itself looks valid.
    """
    project_id = _project_id(project_id); proposal_id = _id(proposal_id)
    timestamp = _timestamp("decided_at", now)
    with _lock(project_id):
        items = load_proposals(project_id, projects_root=projects_root)
        proposal = _find(items, proposal_id)
        if proposal.status == "accepting":
            return _recover_one_acceptance(project_id, proposal, items, projects_root)
        if proposal.status != "ready":
            raise ArticleFeedbackError(f"feedback proposal is already {proposal.status}: {proposal.id}")
        master = _required_master(project_id, projects_root)
        if proposal_state(proposal, master) != "current":
            raise ArticleFeedbackError("feedback proposal is obsolete; compare it with the current article again")
        accepted_title = _text("title", title); accepted_body = _text("body", body)
        # Intent first: two independent sidecars cannot share an OS-level
        # transaction.  If the process dies after master.json is written, the
        # durable intent plus its history reason lets the next request finish
        # the decision without guessing or applying the text twice.
        intent = replace(proposal, status="accepting", decision="accepted", decided_at=timestamp,
                         accepted_title=accepted_title, accepted_body=accepted_body, updated_at=timestamp)
        _write(project_id, tuple(intent if item.id == proposal_id else item for item in items), projects_root)
        try:
            master_documents.save_feedback_acceptance(project_id, proposal_id=proposal.id, title=accepted_title,
                                                      body=accepted_body, now=timestamp, projects_root=projects_root)
        except Exception:
            # A normal write error can safely go back to ready.  A hard crash
            # leaves ``accepting`` and is handled by recover_acceptances().
            _write(project_id, items, projects_root)
            raise
        return _recover_one_acceptance(project_id, intent, (*items,), projects_root)


def reject_proposal(project_id: str, proposal_id: str, *, now: str,
                    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ArticleFeedbackProposal:
    """Record rejection only; the authoritative master sidecar is untouched."""
    project_id = _project_id(project_id); proposal_id = _id(proposal_id)
    timestamp = _timestamp("decided_at", now)
    with _lock(project_id):
        items = load_proposals(project_id, projects_root=projects_root)
        proposal = _find(items, proposal_id)
        if proposal.status != "ready":
            raise ArticleFeedbackError(f"feedback proposal is already {proposal.status}: {proposal.id}")
        updated = replace(proposal, status="rejected", decision="rejected", decided_at=timestamp, updated_at=timestamp)
        _write(project_id, tuple(updated if item.id == proposal_id else item for item in items), projects_root)
        return updated


def recover_acceptances(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> tuple[ArticleFeedbackProposal, ...]:
    """Finish an interrupted accepted-proposal audit record, if any.

    Only an ``accepting`` intent whose corresponding immutable master history
    entry exists can be finalized.  Otherwise it remains visibly interrupted;
    this function never writes a master document itself.
    """
    project_id = _project_id(project_id)
    with _lock(project_id):
        items = load_proposals(project_id, projects_root=projects_root)
        changed = False; resolved: list[ArticleFeedbackProposal] = []
        for proposal in items:
            if proposal.status != "accepting":
                resolved.append(proposal); continue
            try:
                repaired = _recover_one_acceptance(project_id, proposal, items, projects_root, write=False)
            except ArticleFeedbackError:
                repaired = proposal
            changed = changed or repaired is not proposal; resolved.append(repaired)
        result = tuple(resolved)
        if changed: _write(project_id, result, projects_root)
        return result


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


def _current_annotation(project_id: str, annotation_id: str, master: master_documents.MasterDocument, now: str,
                        projects_root: str | Path) -> local_annotations.LocalAnnotation:
    try:
        annotation = local_annotations.resolve_annotation(project_id, annotation_id, now=now, projects_root=projects_root)
    except local_annotations.LocalAnnotationError as exc:
        raise ArticleFeedbackError(str(exc)) from exc
    if annotation.status != "active":
        raise ArticleFeedbackError("annotation is orphaned; confirm its target before requesting a proposal")
    if annotation.resolved_version != master.version or annotation.resolved_hash != master_hash(master):
        raise ArticleFeedbackError("annotation changed while resolving; request a proposal from the current article")
    return annotation


def _local_record(project_id: str, annotation: local_annotations.LocalAnnotation, master: master_documents.MasterDocument,
                  *, status: str, proposed_title: str | None, proposed_body: str | None, error: str | None,
                  now: str, proposal_id: str | None) -> ArticleFeedbackProposal:
    scope = "local_text" if annotation.kind == "text" else "local_image"
    return ArticleFeedbackProposal(
        _id(proposal_id or new_id("afp")), _project_id(project_id), scope, annotation.feedback, None, None, None, None,
        master.version, master_hash(master), status,
        _nullable_text("proposed_title", proposed_title), _nullable_text("proposed_body", proposed_body), _nullable_text("error", error),
        None, None, None, None, annotation.id, annotation.kind, annotation.excerpt, annotation.asset_id,
        annotation.categories, annotation.resolved_version, annotation.resolved_hash,
        _timestamp("created_at", now), _timestamp("updated_at", now),
    )


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


def _recover_one_acceptance(project_id: str, proposal: ArticleFeedbackProposal,
                            items: tuple[ArticleFeedbackProposal, ...], projects_root: str | Path,
                            *, write: bool = True) -> ArticleFeedbackProposal:
    if proposal.status != "accepting" or proposal.accepted_title is None or proposal.accepted_body is None:
        raise ArticleFeedbackError("feedback acceptance recovery is invalid")
    master = _required_master(project_id, projects_root)
    reason = f"feedback:{proposal.id}"
    if not any(snapshot.reason == reason for snapshot in master.history):
        raise ArticleFeedbackError("feedback acceptance is interrupted; the master write was not completed")
    accepted = replace(proposal, status="accepted")
    if write: _write(project_id, tuple(accepted if item.id == proposal.id else item for item in items), projects_root)
    return accepted


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
    additions = {"decision", "decided_at", "accepted_title", "accepted_body", "annotation_id", "annotation_kind", "annotation_excerpt", "annotation_asset_id", "annotation_categories", "annotation_resolved_version", "annotation_resolved_hash"}
    legacy_fields = set(ArticleFeedbackProposal.__dataclass_fields__) - additions
    rv03_fields = legacy_fields | {"decision", "decided_at", "accepted_title", "accepted_body"}
    accepted_old_fields = (legacy_fields, rv03_fields, set(ArticleFeedbackProposal.__dataclass_fields__) - {"decision", "decided_at", "accepted_title", "accepted_body"})
    if not isinstance(payload, dict) or set(payload) not in (set(ArticleFeedbackProposal.__dataclass_fields__), *accepted_old_fields):
        raise ArticleFeedbackError("feedback proposal has missing or unknown fields")
    if set(payload) != set(ArticleFeedbackProposal.__dataclass_fields__):
        defaults = {"decision": None, "decided_at": None, "accepted_title": None, "accepted_body": None,
                    "annotation_id": None, "annotation_kind": None, "annotation_excerpt": None, "annotation_asset_id": None,
                    "annotation_categories": [], "annotation_resolved_version": None, "annotation_resolved_hash": None}
        payload = {**defaults, **payload}
    item = ArticleFeedbackProposal(
        _id(payload["id"]), _project_id(payload["project_id"]), payload["scope"], _text("feedback", payload["feedback"]),
        _optional("target", payload["target"]), _optional("readership", payload["readership"]), _optional("platform", payload["platform"]), _optional("values", payload["values"]),
        _version(payload["base_version"]), _hash(payload["base_hash"]), payload["status"], _nullable_text("proposed_title", payload["proposed_title"]),
        _nullable_text("proposed_body", payload["proposed_body"]), _nullable_text("error", payload["error"]),
        _decision(payload["decision"]), _nullable_timestamp("decided_at", payload["decided_at"]),
        _nullable_text("accepted_title", payload["accepted_title"]), _nullable_text("accepted_body", payload["accepted_body"]),
        _nullable_annotation_id(payload["annotation_id"]), _nullable_annotation_kind(payload["annotation_kind"]),
        _nullable_text("annotation_excerpt", payload["annotation_excerpt"]), _nullable_asset_id(payload["annotation_asset_id"]),
        _annotation_categories(payload["annotation_categories"]), _nullable_version(payload["annotation_resolved_version"]), _nullable_hash(payload["annotation_resolved_hash"]),
        _timestamp("created_at", payload["created_at"]), _timestamp("updated_at", payload["updated_at"]),
    )
    if item.project_id != expected_project_id or item.scope not in {"whole_article", "local_text", "local_image"}:
        raise ArticleFeedbackError("feedback proposal project or scope is invalid")
    is_local = item.scope.startswith("local_")
    if is_local != (item.annotation_id is not None): raise ArticleFeedbackError("feedback proposal annotation scope is invalid")
    if not is_local and any((item.annotation_kind, item.annotation_excerpt, item.annotation_asset_id, item.annotation_resolved_version, item.annotation_resolved_hash)):
        raise ArticleFeedbackError("whole article feedback cannot contain annotation evidence")
    if item.scope == "local_text" and (item.annotation_kind != "text" or not item.annotation_excerpt or item.annotation_asset_id is not None): raise ArticleFeedbackError("local text feedback annotation is invalid")
    if item.scope == "local_image" and (item.annotation_kind != "image" or item.annotation_asset_id is None or item.annotation_excerpt is not None): raise ArticleFeedbackError("local image feedback annotation is invalid")
    if is_local and (item.annotation_resolved_version is None or item.annotation_resolved_hash is None): raise ArticleFeedbackError("local feedback annotation snapshot is invalid")
    if item.status == "ready" and (item.proposed_title is None or item.proposed_body is None or item.error is not None or item.decision is not None or item.decided_at is not None):
        raise ArticleFeedbackError("ready feedback proposal is invalid")
    if item.status == "failed" and (item.proposed_title is not None or item.proposed_body is not None or item.error is None or item.decision is not None or item.decided_at is not None):
        raise ArticleFeedbackError("failed feedback proposal is invalid")
    if item.status in {"accepted", "accepting"} and (item.decision != "accepted" or item.decided_at is None or item.accepted_title is None or item.accepted_body is None or item.error is not None):
        raise ArticleFeedbackError("accepted feedback proposal is invalid")
    if item.status == "rejected" and (item.decision != "rejected" or item.decided_at is None or item.accepted_title is not None or item.accepted_body is not None or item.error is not None):
        raise ArticleFeedbackError("rejected feedback proposal is invalid")
    if item.status not in {"ready", "failed", "accepting", "accepted", "rejected"}:
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
def _nullable_timestamp(name: str, value: Any) -> str | None: return None if value is None else _timestamp(name, value)
def _decision(value: Any) -> str | None:
    if value is None: return None
    if value not in {"accepted", "rejected"}: raise ArticleFeedbackError("feedback decision is invalid")
    return value
def _optional(name: str, value: Any) -> str | None: return None if value is None else _text(name, value)
def _nullable_annotation_id(value: Any) -> str | None: return None if value is None else _annotation_id(value)
def _annotation_id(value: Any) -> str:
    if not valid_sidecar_id(value, "lan_"): raise ArticleFeedbackError(f"invalid annotation id: {value!r}")
    return value
def _nullable_annotation_kind(value: Any) -> str | None:
    if value is None: return None
    if value not in {"text", "image"}: raise ArticleFeedbackError("invalid annotation kind")
    return value
def _nullable_asset_id(value: Any) -> str | None:
    if value is None: return None
    if not valid_sidecar_id(value, "vas_"): raise ArticleFeedbackError(f"invalid visual asset id: {value!r}")
    return value
def _annotation_categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(value) != len(set(value)): raise ArticleFeedbackError("invalid annotation categories")
    return tuple(value)
def _nullable_version(value: Any) -> int | None: return None if value is None else _version(value)
def _nullable_hash(value: Any) -> str | None: return None if value is None else _hash(value)
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
