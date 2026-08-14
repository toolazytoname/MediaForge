"""Approved local content-package export; never publishes externally."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from pipeline import project_exports
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["project-exports"])


def _payload(result: project_exports.ProjectExport) -> dict[str, str]:
    payload = asdict(result)
    payload["url"] = f"/output/projects/{result.project_id}/{result.path}"
    return payload


def _export_error(project_id: str, error: project_exports.ProjectExportError) -> HTTPException:
    status = 404 if str(error) == f"project not found: {project_id}" else 400
    code = "project_not_found" if status == 404 else "project_export_not_ready"
    return HTTPException(status_code=status, detail={"error": {
        "code": code, "message": str(error),
    }})


@router.post("/projects/{project_id}/export/markdown", status_code=201)
def export_project_markdown(project_id: str) -> dict[str, str]:
    """Primary formal export: versioned Markdown package with relative image paths."""
    try:
        result = project_exports.create_markdown_export(
            project_id, projects_root=projects_api._PROJECTS_ROOT,
        )
    except project_exports.ProjectExportError as error:
        raise _export_error(project_id, error) from error
    return _payload(result)


@router.post("/projects/{project_id}/export", status_code=201)
def export_project(project_id: str) -> dict[str, str]:
    """Secondary ZIP backup of the approved dual-platform content package."""
    try:
        result = project_exports.create_export(
            project_id, projects_root=projects_api._PROJECTS_ROOT,
        )
    except project_exports.ProjectExportError as error:
        raise _export_error(project_id, error) from error
    return _payload(result)
