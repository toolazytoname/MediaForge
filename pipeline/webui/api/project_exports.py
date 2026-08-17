"""Approved local content-package export; never publishes externally."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from pipeline import project_exports
from pipeline.delivery.service import DeliveryError, bridge_enabled, create_export_delivery
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["project-exports"])


@router.post("/projects/{project_id}/export", status_code=201)
def export_project(project_id: str) -> dict[str, str]:
    cfg, _err = deps.get_config()
    if bridge_enabled(cfg):
        try:
            with deps._db() as conn:
                delivered = create_export_delivery(
                    conn, project_id=project_id, deliverable_id=None,
                    actor="local", cfg=cfg, projects_root=projects_api._PROJECTS_ROOT,
                )
        except DeliveryError as error:
            raise HTTPException(status_code=error.http_status, detail={"error": {
                "code": error.code, "message": str(error),
            }}) from error
        assert delivered.export is not None
        payload = asdict(delivered.export)
        payload["url"] = f"/output/projects/{project_id}/{delivered.export.path}"
        return payload
    try:
        result = project_exports.create_export(
            project_id, projects_root=projects_api._PROJECTS_ROOT,
        )
    except project_exports.ProjectExportError as error:
        message = str(error)
        if message == f"project not found: {project_id}":
            status, code = 404, "project_not_found"
        elif "completed approval" in message:
            status, code = 409, "not_approved"
        else:
            status, code = 400, "project_export_not_ready"
        raise HTTPException(status_code=status, detail={"error": {
            "code": code, "message": message,
        }}) from error
    payload = asdict(result)
    payload["url"] = f"/output/projects/{project_id}/{result.path}"
    return payload
