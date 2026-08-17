"""Read-only autonomy policies, next-action CTA, and pack prepare."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from pipeline.autonomy import AutonomyError, all_policies, next_action
from pipeline.pack import prepare_pack
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["autonomy"])


def _root():
    return projects_api._PROJECTS_ROOT


def raise_autonomy(error: AutonomyError) -> HTTPException:
    return HTTPException(
        status_code=error.http_status,
        detail={"error": {"code": error.code, "message": str(error)}},
    )


@router.get("/autonomy-policies")
def list_policies() -> dict[str, Any]:
    return {"items": [item.to_dict() for item in all_policies()]}


@router.get("/projects/{project_id}/next-action")
def get_next_action(project_id: str) -> dict[str, Any]:
    try:
        return next_action(project_id, projects_root=_root())
    except AutonomyError as error:
        raise raise_autonomy(error) from error


@router.post("/projects/{project_id}/pack/prepare", status_code=201)
def pack_prepare(project_id: str) -> dict[str, Any]:
    try:
        result = prepare_pack(
            project_id,
            now=datetime.now(timezone.utc).isoformat(),
            projects_root=_root(),
        )
    except AutonomyError as error:
        raise raise_autonomy(error) from error
    return result.to_dict()
