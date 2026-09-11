"""Project short-video API. Generates a downloadable cut. Never publishes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import project_video
from pipeline import projects as project_store
from pipeline.autonomy import AutonomyError, require_llm
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["project-video"])


def _root():
    return projects_api._PROJECTS_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _video_error(project_id: str, error: Exception) -> HTTPException:
    message = str(error)
    if message == f"project not found: {project_id}":
        return _err(404, "project_not_found", error)
    if isinstance(error, project_video.ProjectVideoError):
        return _err(400, "project_video_failed", error)
    return _err(502, "project_video_failed", error)


@router.get("/projects/{project_id}/video")
def get_project_video(project_id: str) -> dict[str, Any]:
    try:
        project_store.load_project(project_id, projects_root=_root())
        pack = project_video.load_video(project_id, projects_root=_root())
    except (project_store.ProjectManifestError, project_video.ProjectVideoError) as error:
        raise _video_error(project_id, error) from error
    return {"video": project_video.to_payload(pack) if pack else None}


@router.post("/projects/{project_id}/video")
def post_project_video(project_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = body or {}
    extra = set(payload) - set()
    if extra:
        raise _err(400, "invalid_project_video", "request body must be empty")
    try:
        try:
            require_llm(project_id, projects_root=_root())
            allow_llm = True
        except AutonomyError:
            # Autonomy policy forbids the model: still cut the video, but from the
            # heuristic script instead of an AI-written one.
            allow_llm = False
        pack = project_video.generate_project_video(
            project_id, now=_now(), projects_root=_root(), allow_llm=allow_llm,
        )
    except (project_store.ProjectManifestError, project_video.ProjectVideoError) as error:
        raise _video_error(project_id, error) from error
    return project_video.to_payload(pack)
