"""R3 read-only Project sidecar API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pipeline import projects as project_store


router = APIRouter(tags=["projects"])
_PROJECTS_ROOT = project_store.DEFAULT_PROJECTS_ROOT


def _project_dict(project: project_store.Project) -> dict[str, Any]:
    """Serialize an immutable sidecar record without exposing storage details."""
    return {
        "id": project.id,
        "title": project.title,
        "idea": project.idea,
        "audience": project.audience,
        "goal": project.goal,
        "voice": project.voice,
        "autonomy": project.autonomy,
        "content_ids": list(project.content_ids),
        "asset_paths": list(project.asset_paths),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _manifest_error(project_id: str | None, error: Exception) -> HTTPException:
    message = str(error)
    if project_id is not None and message == f"project not found: {project_id}":
        return HTTPException(status_code=404, detail={"error": {
            "code": "project_not_found",
            "message": message,
        }})
    subject = project_id or "project collection"
    return HTTPException(status_code=500, detail={"error": {
        "code": "project_manifest_invalid",
        "message": f"cannot read {subject}: {message}",
    }})


@router.get("/projects")
def list_projects() -> dict[str, Any]:
    """List valid manifests newest first; malformed data is deliberately visible."""
    try:
        items = project_store.list_projects(projects_root=_PROJECTS_ROOT)
    except project_store.ProjectManifestError as error:
        raise _manifest_error(None, error) from error
    return {"items": [_project_dict(item) for item in items], "total": len(items)}


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Read one manifest. This endpoint never creates or changes a project."""
    try:
        project = project_store.load_project(project_id, projects_root=_PROJECTS_ROOT)
    except project_store.ProjectManifestError as error:
        raise _manifest_error(project_id, error) from error
    return _project_dict(project)
