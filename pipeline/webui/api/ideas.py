"""R4 平台无关 Idea Inbox API。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from pipeline import ideas as idea_store
from pipeline import projects as project_store
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["ideas"])
_IDEAS_ROOT = idea_store.DEFAULT_IDEAS_ROOT


def _idea_dict(idea: idea_store.Idea) -> dict[str, Any]:
    return {
        "id": idea.id, "input_type": idea.input_type, "content": idea.content,
        "title": idea.title, "project_id": idea.project_id,
        "created_at": idea.created_at, "updated_at": idea.updated_at,
    }


def _error(status_code: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {
        "code": code, "message": str(error),
    }})


def _idea_input(body: dict[str, Any]) -> idea_store.Idea:
    if set(body) != {"input_type", "content", "title"}:
        raise idea_store.IdeaManifestError("idea body must contain only input_type, content and title")
    return idea_store.create_idea(
        input_type=body["input_type"], content=body["content"], title=body["title"],
        now=datetime.now(timezone.utc).isoformat(), ideas_root=_IDEAS_ROOT,
    )


@router.get("/ideas")
def list_ideas() -> dict[str, Any]:
    try:
        items = idea_store.list_ideas(ideas_root=_IDEAS_ROOT)
    except idea_store.IdeaManifestError as error:
        raise _error(500, "idea_manifest_invalid", error) from error
    return {"items": [_idea_dict(item) for item in items], "total": len(items)}


@router.post("/ideas", status_code=201)
def create_idea(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        idea = _idea_input(body)
    except idea_store.IdeaManifestError as error:
        raise _error(400, "invalid_idea_input", error) from error
    return _idea_dict(idea)


@router.post("/ideas/{idea_id}/promote-to-project", response_model=None)
def promote_to_project(idea_id: str, body: dict[str, Any] = Body(...)):
    """明确提升一个 Idea；已有提升结果原样返回，确保重试幂等。"""
    try:
        idea = idea_store.load_idea(idea_id, ideas_root=_IDEAS_ROOT)
    except idea_store.IdeaManifestError as error:
        if str(error) == f"idea not found: {idea_id}":
            raise _error(404, "idea_not_found", error) from error
        raise _error(500, "idea_manifest_invalid", error) from error

    if idea.project_id is not None:
        try:
            project = project_store.load_project(idea.project_id, projects_root=projects_api._PROJECTS_ROOT)
        except project_store.ProjectManifestError as error:
            raise _error(500, "project_manifest_invalid", error) from error
        return {"idea": _idea_dict(idea), "project": projects_api._project_dict(project)}

    try:
        project = projects_api.create_project_from_input({**body, "idea": idea.content})
        promoted = idea_store.promote_idea(
            idea, project_id=project.id, now=datetime.now(timezone.utc).isoformat(), ideas_root=_IDEAS_ROOT,
        )
    except (idea_store.IdeaManifestError, project_store.ProjectManifestError) as error:
        raise _error(400, "invalid_project_input", error) from error
    return JSONResponse(
        status_code=201,
        content={"idea": _idea_dict(promoted), "project": projects_api._project_dict(project)},
    )
