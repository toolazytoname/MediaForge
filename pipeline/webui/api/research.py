"""Explicit Project-scoped research board API.

All mutations are creator initiated.  The API deliberately has no fetch, LLM,
or automatic verification path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import research as research_store
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["research"])


def _root():
    """Resolve at request time so project API test isolation remains shared."""
    return projects_api._PROJECTS_ROOT


def _error(status_code: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": str(error)}})


def _research_error(error: research_store.ResearchManifestError) -> HTTPException:
    message = str(error)
    if message.startswith("project not found:"):
        return _error(404, "project_not_found", error)
    return _error(500, "research_manifest_invalid", error)


def _source_dict(source: research_store.ResearchSource) -> dict[str, Any]:
    return {"id": source.id, "title": source.title, "reference": source.reference, "summary": source.summary,
            "entered_at": source.entered_at, "updated_at": source.updated_at}


def _claim_dict(claim: research_store.ResearchClaim) -> dict[str, Any]:
    return {"id": claim.id, "text": claim.text, "kind": claim.kind, "source_ids": list(claim.source_ids),
            "status": claim.status, "limitation": claim.limitation, "counterpoint": claim.counterpoint,
            "entered_at": claim.entered_at, "updated_at": claim.updated_at}


def _board_dict(board: research_store.ResearchBoard) -> dict[str, Any]:
    return {"project_id": board.project_id, "sources": [_source_dict(item) for item in board.sources],
            "claims": [_claim_dict(item) for item in board.claims]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_readable_project(project_id: str) -> None:
    """Make a missing Project visible before reporting malformed mutation input."""
    try:
        research_store.load_research(project_id, projects_root=_root())
    except research_store.ResearchManifestError as error:
        raise _research_error(error) from error


def _only(body: dict[str, Any], fields: set[str], subject: str) -> None:
    if set(body) != fields:
        raise research_store.ResearchManifestError(
            f"{subject} body must contain only {', '.join(sorted(fields))}"
        )


def _source_input(body: dict[str, Any]) -> dict[str, Any]:
    _only(body, {"title", "reference", "summary"}, "source")
    return body


def _claim_input(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {"text", "kind", "source_ids", "status", "limitation", "counterpoint"}
    missing = {"text", "kind", "source_ids", "status"} - set(body)
    if missing or set(body) - allowed:
        raise research_store.ResearchManifestError("claim body has missing or unknown fields")
    return {key: body.get(key) for key in allowed}


@router.get("/projects/{project_id}/research")
def get_research(project_id: str) -> dict[str, Any]:
    try:
        return _board_dict(research_store.load_research(project_id, projects_root=_root()))
    except research_store.ResearchManifestError as error:
        raise _research_error(error) from error


@router.post("/projects/{project_id}/research/sources", status_code=201)
def create_source(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _require_readable_project(project_id)
    try:
        source = research_store.add_source(project_id, **_source_input(body), now=_now(), projects_root=_root())
    except research_store.ResearchManifestError as error:
        if str(error).startswith("project not found:"):
            raise _research_error(error) from error
        raise _error(400, "invalid_research_source", error) from error
    return _source_dict(source)


@router.put("/projects/{project_id}/research/sources/{source_id}")
def replace_source(project_id: str, source_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _require_readable_project(project_id)
    try:
        source = research_store.update_source(project_id, source_id, **_source_input(body), now=_now(), projects_root=_root())
    except research_store.ResearchManifestError as error:
        if str(error).startswith("project not found:"):
            raise _research_error(error) from error
        if str(error).startswith("source not found:"):
            raise _error(404, "research_source_not_found", error) from error
        raise _error(400, "invalid_research_source", error) from error
    return _source_dict(source)


@router.post("/projects/{project_id}/research/claims", status_code=201)
def create_claim(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _require_readable_project(project_id)
    try:
        claim = research_store.add_claim(project_id, **_claim_input(body), now=_now(), projects_root=_root())
    except research_store.ResearchManifestError as error:
        if str(error).startswith("project not found:"):
            raise _research_error(error) from error
        raise _error(400, "invalid_research_claim", error) from error
    return _claim_dict(claim)


@router.put("/projects/{project_id}/research/claims/{claim_id}")
def replace_claim(project_id: str, claim_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _require_readable_project(project_id)
    try:
        claim = research_store.update_claim(project_id, claim_id, **_claim_input(body), now=_now(), projects_root=_root())
    except research_store.ResearchManifestError as error:
        if str(error).startswith("project not found:"):
            raise _research_error(error) from error
        if str(error).startswith("claim not found:"):
            raise _error(404, "research_claim_not_found", error) from error
        raise _error(400, "invalid_research_claim", error) from error
    return _claim_dict(claim)
