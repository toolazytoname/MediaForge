"""Project article delivery: preview, export, wechat draft. Never exposes direct."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline.deliverables import DeliverablesError, get_deliverable, load_deliverables, seed_id_for
from pipeline.delivery.service import (
    DeliveryError,
    attempt_to_dict,
    create_draft,
    create_export_delivery,
    preview_deliverable,
)
from pipeline.delivery.store import latest_attempts
from pipeline.publishers import get_adapter
from pipeline.publishers.base import AccountConfig
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["delivery"])


def _root():
    return projects_api._PROJECTS_ROOT


def _raise(error: DeliveryError) -> HTTPException:
    return HTTPException(status_code=error.http_status, detail={"error": {"code": error.code, "message": str(error)}})


def _actor(body: dict[str, Any]) -> str:
    actor = body.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        raise HTTPException(status_code=400, detail={"error": {"code": "invalid_delivery_request", "message": "actor is required"}})
    return actor.strip()


@router.get("/projects/{project_id}/deliverables")
def list_deliverables(project_id: str) -> dict[str, Any]:
    try:
        result = load_deliverables(project_id, projects_root=_root())
    except DeliverablesError as error:
        status = 404 if "not found" in str(error) else 400
        raise HTTPException(status_code=status, detail={"error": {"code": "deliverable_error", "message": str(error)}}) from error
    from dataclasses import asdict
    return asdict(result)


@router.get("/projects/{project_id}/delivery-attempts")
def list_attempts(project_id: str) -> dict[str, Any]:
    with deps._db() as conn:
        items = latest_attempts(conn, project_id)
    from dataclasses import asdict
    return {"items": [asdict(item) for item in items]}


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/preview")
def preview(project_id: str, deliverable_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg, _err = deps.get_config()
    try:
        with deps._db() as conn:
            result = preview_deliverable(
                conn, project_id=project_id, deliverable_id=deliverable_id,
                actor=_actor(body) if body else "local",
                adapter=None, account=None, cfg=cfg, projects_root=_root(),
            )
    except (DeliveryError, DeliverablesError) as error:
        if isinstance(error, DeliveryError):
            raise _raise(error) from error
        raise HTTPException(status_code=400, detail={"error": {"code": "deliverable_error", "message": str(error)}}) from error
    return attempt_to_dict(result)


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/export", status_code=201)
def export_one(project_id: str, deliverable_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    cfg, _err = deps.get_config()
    try:
        with deps._db() as conn:
            result = create_export_delivery(
                conn, project_id=project_id, deliverable_id=deliverable_id,
                actor=_actor(body) if body else "local", cfg=cfg, projects_root=_root(),
            )
    except DeliveryError as error:
        raise _raise(error) from error
    return attempt_to_dict(result)


@router.post("/projects/{project_id}/deliverables/{deliverable_id}/draft", status_code=201)
def draft(project_id: str, deliverable_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    if body.get("mode") == "direct" or "confirm_token" in body:
        raise HTTPException(status_code=403, detail={"error": {"code": "direct_hidden", "message": "Project UI does not expose direct publish"}})
    cfg, err = deps.get_config()
    if err or cfg is None:
        raise HTTPException(status_code=400, detail={"error": {"code": "config_missing", "message": err or "config missing"}})
    actor = _actor(body)
    retry_of_id = body.get("retry_of_id")
    try:
        deliverable = get_deliverable(project_id, deliverable_id, projects_root=_root())
        platform = deliverable.targets[0]
        adapter, account = _adapter_for(cfg, platform)
        with deps._db() as conn:
            result = create_draft(
                conn, project_id=project_id, deliverable_id=deliverable_id, actor=actor,
                adapter=adapter, account=account, publish_config=cfg.publish, cfg=cfg,
                projects_root=_root(), retry_of_id=retry_of_id,
            )
    except DeliveryError as error:
        raise _raise(error) from error
    except (DeliverablesError, ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=400, detail={"error": {"code": "draft_unavailable", "message": str(error)}}) from error
    return attempt_to_dict(result)


def _adapter_for(cfg: Any, platform: str) -> tuple[Any, AccountConfig]:
    plat = getattr(cfg.platforms, platform, None)
    if plat is None or not plat.accounts:
        raise DeliveryError(f"no {platform} account configured", code="account_missing")
    acc = plat.accounts[0]
    creds = acc.credentials if hasattr(acc, "credentials") else acc.cookies
    account = AccountConfig(id=acc.id, credentials_path=Path(creds))
    return get_adapter(platform, account=account, config=cfg), account


# seed id helper kept for UI convenience
__all__ = ["router", "seed_id_for"]
