"""Learning suggestions and scheduled-prepare status (LAZY-88)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline.automation import load_automation
from pipeline.insights import InsightError, decide_suggestion, generate_suggestions, load_insights
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["insights"])


def _root():
    return projects_api._PROJECTS_ROOT


def _raise(error: InsightError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": "insight_error", "message": str(error)}},
    )


@router.get("/projects/{project_id}/automation")
def get_automation(project_id: str) -> dict[str, Any]:
    state = load_automation(project_id, projects_root=_root())
    return {"project_id": project_id, "automation": state}


@router.get("/projects/{project_id}/insights")
def get_insights(project_id: str) -> dict[str, Any]:
    try:
        return load_insights(project_id, projects_root=_root()).to_dict()
    except InsightError as error:
        raise _raise(error) from error


@router.post("/projects/{project_id}/insights/generate", status_code=201)
def generate_insight_cards(project_id: str) -> dict[str, Any]:
    try:
        board = generate_suggestions(
            project_id,
            now=datetime.now(timezone.utc).isoformat(),
            projects_root=_root(),
            actor="web",
        )
    except InsightError as error:
        raise _raise(error) from error
    return board.to_dict()


@router.post("/projects/{project_id}/insights/{suggestion_id}/decide")
def decide_insight_card(
    project_id: str,
    suggestion_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    accepted = body.get("accepted")
    actor = body.get("actor")
    if not isinstance(accepted, bool):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "invalid_insight_request", "message": "accepted must be boolean"}},
        )
    if not isinstance(actor, str) or not actor.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "invalid_insight_request", "message": "actor is required"}},
        )
    try:
        board = decide_suggestion(
            project_id,
            suggestion_id,
            accepted=accepted,
            actor=actor.strip(),
            now=datetime.now(timezone.utc).isoformat(),
            projects_root=_root(),
        )
    except InsightError as error:
        raise _raise(error) from error
    return board.to_dict()
