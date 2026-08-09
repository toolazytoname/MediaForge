"""R3 read-only Project sidecar API."""
from __future__ import annotations

from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException

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


def _invalid_input(code: str, error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": {
        "code": code,
        "message": str(error),
    }})


def create_project_from_input(body: dict[str, Any]) -> project_store.Project:
    """严格挑选公开字段，避免把请求体扩散进冻结的 Project v0。"""
    allowed = {"title", "idea", "audience", "goal", "voice", "autonomy"}
    if set(body) != allowed:
        raise project_store.ProjectManifestError(
            "project body must contain only title, idea, audience, goal, voice and autonomy"
        )
    return project_store.create_project(
        title=body["title"], idea=body["idea"], audience=body["audience"],
        goal=body["goal"], voice=body["voice"], autonomy=body["autonomy"],
        now=datetime.now(timezone.utc).isoformat(), projects_root=_PROJECTS_ROOT,
    )


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


@router.post("/projects", status_code=201)
def create_project(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """显式创建 sidecar Project；不写 SQLite、不调用 AI。"""
    try:
        project = create_project_from_input(body)
    except project_store.ProjectManifestError as error:
        raise _invalid_input("invalid_project_input", error) from error
    return _project_dict(project)
