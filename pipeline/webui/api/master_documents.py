"""Project-scoped MasterDocument and explicit AI proposal API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import master_documents as master_store
from pipeline import research as research_store
from pipeline.creators import llm
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["master-documents"])


def _root():
    return projects_api._PROJECTS_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _master_dict(master: master_store.MasterDocument | None) -> dict[str, Any] | None:
    if master is None:
        return None
    return {"project_id": master.project_id, "title": master.title, "body": master.body,
            "version": master.version, "created_at": master.created_at, "updated_at": master.updated_at,
            "history": [_snapshot_dict(item) for item in master.history]}


def _snapshot_dict(snapshot: master_store.MasterSnapshot) -> dict[str, Any]:
    return {"version": snapshot.version, "title": snapshot.title, "body": snapshot.body,
            "saved_at": snapshot.saved_at, "reason": snapshot.reason}


def _suggestion_dict(suggestion: master_store.MasterSuggestion) -> dict[str, Any]:
    return {"id": suggestion.id, "project_id": suggestion.project_id, "action": suggestion.action,
            "selection": suggestion.selection, "base_version": suggestion.base_version,
            "proposed_title": suggestion.proposed_title, "proposed_body": suggestion.proposed_body,
            "status": suggestion.status, "created_at": suggestion.created_at, "decided_at": suggestion.decided_at}


def _master_error(project_id: str, error: master_store.MasterDocumentError) -> HTTPException:
    message = str(error)
    if message == f"project not found: {project_id}":
        return _error(404, "project_not_found", error)
    if message == f"master not found: {project_id}":
        return _error(404, "master_not_found", error)
    if message.startswith("suggestion not found:") or message.startswith("master version not found:"):
        return _error(404, "master_record_not_found", error)
    if message.startswith("cannot read project:") or "manifest" in message or "markdown does not match" in message:
        return _error(500, "master_manifest_invalid", error)
    return _error(400, "invalid_master_request", error)


def _manual_input(body: dict[str, Any]) -> tuple[str, str]:
    if set(body) != {"title", "body"}:
        raise master_store.MasterDocumentError("master body must contain only title and body")
    return body["title"], body["body"]


def _suggestion_input(body: dict[str, Any]) -> tuple[str, str | None]:
    if set(body) not in ({"action"}, {"action", "selection"}):
        raise master_store.MasterDocumentError("suggestion body must contain action and optional selection")
    return body["action"], body.get("selection")


@router.get("/projects/{project_id}/master")
def get_master(project_id: str) -> dict[str, Any]:
    try:
        return {"master": _master_dict(master_store.load_master(project_id, projects_root=_root()))}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.put("/projects/{project_id}/master")
def save_master(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        title, text = _manual_input(body)
        master = master_store.save_manual(project_id, title=title, body=text, now=_now(), projects_root=_root())
        return _master_dict(master) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.get("/projects/{project_id}/master/versions")
def get_versions(project_id: str) -> dict[str, Any]:
    try:
        return {"items": [_snapshot_dict(item) for item in master_store.list_versions(project_id, projects_root=_root())]}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/versions/{version}/restore")
def restore_master(project_id: str, version: int) -> dict[str, Any]:
    try:
        return _master_dict(master_store.restore_version(project_id, version, now=_now(), projects_root=_root())) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.get("/projects/{project_id}/master/suggestions")
def get_suggestions(project_id: str) -> dict[str, Any]:
    try:
        return {"items": [_suggestion_dict(item) for item in master_store.load_suggestions(project_id, projects_root=_root())]}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions", status_code=201)
def request_suggestion(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Call the existing LLM wrapper only after an explicit user request.

    The response becomes a pending proposal; it can never mutate the master in
    this handler.
    """
    try:
        action, selection = _suggestion_input(body)
        if action not in {"clarify", "shorten", "change_voice", "add_counterpoint"}:
            raise master_store.MasterDocumentError(
                "suggestion action must be clarify, shorten, change_voice or add_counterpoint"
            )
        master = master_store.load_master(project_id, projects_root=_root())
        if master is None:
            raise master_store.MasterDocumentError(f"master not found: {project_id}")
        if selection is not None and master.body.count(selection) != 1:
            raise master_store.MasterDocumentError(
                "selection must occur exactly once in the current master body"
            )
        prompt = _suggestion_prompt(project_id, master, action, selection)
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error
    if not _llm_is_configured():
        raise _error(503, "llm_provider_unavailable", "AI provider is not configured; save or write manually instead")
    try:
        conn = deps.get_conn()
        try:
            proposed = llm.complete(prompt, stage="coauthor_suggestion", ref_id=project_id,
                                    model_tier="creative", max_tokens=4096, conn=conn)
        finally:
            conn.close()
    except Exception as error:
        raise _error(502, "llm_suggestion_failed", f"AI suggestion failed: {error}") from error
    try:
        proposed_title = master.title
        proposed_body = _replace_selection(master.body, selection, proposed) if selection is not None else proposed
        suggestion = master_store.create_suggestion(project_id, action=action, selection=selection,
            proposed_title=proposed_title, proposed_body=proposed_body, now=_now(), projects_root=_root())
        return _suggestion_dict(suggestion)
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions/{suggestion_id}/accept")
def accept_suggestion(project_id: str, suggestion_id: str) -> dict[str, Any]:
    try:
        return _master_dict(master_store.accept_suggestion(project_id, suggestion_id, now=_now(), projects_root=_root())) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions/{suggestion_id}/reject")
def reject_suggestion(project_id: str, suggestion_id: str) -> dict[str, Any]:
    try:
        return _suggestion_dict(master_store.reject_suggestion(project_id, suggestion_id, now=_now(), projects_root=_root()))
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


def _llm_is_configured() -> bool:
    return not isinstance(llm._PROVIDER, llm.MockProvider)


def _replace_selection(body: str, selection: str, replacement: str) -> str:
    if body.count(selection) != 1:
        raise master_store.MasterDocumentError("selection must occur exactly once in the current master body")
    return body.replace(selection, replacement, 1)


def _suggestion_prompt(project_id: str, master: master_store.MasterDocument, action: str, selection: str | None) -> str:
    project = projects_api.project_store.load_project(project_id, projects_root=_root())
    try:
        board = research_store.load_research(project_id, projects_root=_root())
    except research_store.ResearchManifestError as error:
        raise master_store.MasterDocumentError(f"cannot read research: {error}") from error
    claims = "\n".join(f"- {item.kind}/{item.status}: {item.text}" for item in board.claims) or "- none"
    sources = "\n".join(f"- {item.title}: {item.reference}" for item in board.sources) or "- none"
    target = selection if selection is not None else master.body
    instruction = {"clarify": "rewrite it to be clearer without adding unsupported claims", "shorten": "compress it while preserving the author's point", "change_voice": f"rewrite it in this voice: {project.voice}", "add_counterpoint": "add a fair, explicit counterpoint while preserving the author's claim"}[action]
    return ("You are proposing an edit, not publishing a final answer. Return ONLY the replacement text, no preface.\n"
            f"Action: {instruction}\nAudience: {project.audience}\nGoal: {project.goal}\nVoice: {project.voice}\n"
            f"Research sources:\n{sources}\nResearch claims:\n{claims}\n\nText to revise:\n{target}")
