"""Human content-package approval endpoints. They never publish anything."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import approvals
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["approvals"])
def _root(): return projects_api._PROJECTS_ROOT
def _now(): return datetime.now(timezone.utc).isoformat()
def _err(status: int, code: str, message: str): return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})
def _status(value: approvals.ApprovalStatus) -> dict[str, Any]: return asdict(value)
def _approval_error(project_id: str, error: approvals.ApprovalError) -> HTTPException:
    if str(error) == f"project not found: {project_id}": return _err(404, "project_not_found", str(error))
    return _err(400, "invalid_approval_request", str(error))

@router.get("/projects/{project_id}/approval")
def get_approval(project_id: str) -> dict[str, Any]:
    try: return _status(approvals.status(project_id, projects_root=_root()))
    except approvals.ApprovalError as error: raise _approval_error(project_id, error) from error

@router.post("/projects/{project_id}/approval/recheck")
def recheck(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) != {"actor"}: raise _err(400, "invalid_approval_request", "recheck requires only actor")
    try: return _status(approvals.recheck(project_id, actor=body["actor"], now=_now(), projects_root=_root()))
    except approvals.ApprovalError as error: raise _approval_error(project_id, error) from error

@router.post("/projects/{project_id}/approval/checks/{check_id}")
def decide(project_id: str, check_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) - {"approved", "note", "actor"} or {"approved", "actor"} - set(body):
        raise _err(400, "invalid_approval_request", "decision requires approved, actor and optional note")
    try: return _status(approvals.decide(project_id, check_id, approved=body["approved"], note=body.get("note"), actor=body["actor"], now=_now(), projects_root=_root()))
    except approvals.ApprovalError as error: raise _approval_error(project_id, error) from error
