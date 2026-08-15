"""Send a project WeChat variant to the official draft box. Never mass-sends."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from pipeline import projects as project_store
from pipeline.wechat_project_draft import WechatDraftError, load_receipt, send_project_wechat_draft
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["wechat-draft"])


def _root():
    return projects_api._PROJECTS_ROOT


def _err(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _receipt_error(project_id: str, error: Exception) -> HTTPException:
    message = str(error)
    if message == f"project not found: {project_id}":
        return _err(404, "project_not_found", error)
    if isinstance(error, WechatDraftError):
        return _err(400, "wechat_draft_failed", error)
    return _err(502, "wechat_draft_failed", error)


@router.get("/projects/{project_id}/wechat-draft")
def get_wechat_draft(project_id: str) -> dict[str, Any]:
    try:
        project_store.load_project(project_id, projects_root=_root())
    except project_store.ProjectManifestError as error:
        raise _receipt_error(project_id, error) from error
    receipt = load_receipt(project_id, projects_root=_root())
    return {"receipt": asdict(receipt) if receipt else None}


@router.post("/projects/{project_id}/wechat-draft")
def post_wechat_draft(project_id: str) -> dict[str, Any]:
    try:
        receipt = send_project_wechat_draft(project_id, projects_root=_root())
    except (project_store.ProjectManifestError, WechatDraftError) as error:
        raise _receipt_error(project_id, error) from error
    return asdict(receipt)
