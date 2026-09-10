"""Account creative profiles and project bindings."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline.account_bindings import AccountBindingError, bind_project_account, load_bindings
from pipeline.account_profiles import (
    AccountProfileError,
    DEFAULT_ACCOUNTS_ROOT,
    import_platform_accounts,
    list_profiles,
)
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["account-profiles"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raise_profile(error: AccountProfileError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": "invalid_account_profile", "message": str(error)}},
    )


@router.get("/account-profiles")
def get_profiles() -> dict[str, Any]:
    items = list_profiles(accounts_root=DEFAULT_ACCOUNTS_ROOT)
    return {"items": [asdict(item) for item in items]}


@router.post("/account-profiles/import", status_code=201)
def import_profiles() -> dict[str, Any]:
    cfg, err = deps.get_config()
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "config_missing", "message": err or "config missing"}},
        )
    items = import_platform_accounts(
        cfg.platforms, now=_now(), accounts_root=DEFAULT_ACCOUNTS_ROOT,
    )
    return {"items": [asdict(item) for item in items]}


@router.get("/projects/{project_id}/account-binding")
def get_binding(project_id: str) -> dict[str, Any]:
    try:
        bindings = load_bindings(project_id, projects_root=projects_api._PROJECTS_ROOT)
    except AccountBindingError as error:
        if "not bound" in str(error):
            raise HTTPException(status_code=404, detail={"error": {
                "code": "account_not_bound", "message": str(error),
            }}) from error
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_account_binding", "message": str(error),
        }}) from error
    return asdict(bindings)


@router.put("/projects/{project_id}/account-binding")
def put_binding(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    platform = body.get("platform")
    account_id = body.get("account_id")
    if not isinstance(platform, str) or not isinstance(account_id, str):
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_account_binding",
            "message": "platform and account_id are required",
        }})
    try:
        bindings = bind_project_account(
            project_id, platform=platform.strip(), account_id=account_id.strip(),
            now=_now(), projects_root=projects_api._PROJECTS_ROOT,
            accounts_root=DEFAULT_ACCOUNTS_ROOT,
        )
    except AccountBindingError as error:
        status = 404 if "not found" in str(error) else 400
        raise HTTPException(status_code=status, detail={"error": {
            "code": "invalid_account_binding", "message": str(error),
        }}) from error
    return asdict(bindings)
