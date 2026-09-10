"""Account creative profiles and project bindings."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline.account_authorization import AuthorizationError, save_authorization
from pipeline.account_bindings import AccountBindingError, bind_project_account, load_bindings
from pipeline.account_onboarding import OnboardingError, confirm_onboarding, propose_onboarding
from pipeline.account_profiles import (
    AccountProfileError,
    DEFAULT_ACCOUNTS_ROOT,
    import_platform_accounts,
    list_profiles,
    load_profile,
    update_profile,
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


@router.post("/account-profiles/{account_id}/onboarding")
def post_onboarding(account_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        draft = propose_onboarding(
            account_id,
            interview=body.get("interview") or {},
            sources=body.get("sources") or (),
            now=_now(),
            accounts_root=DEFAULT_ACCOUNTS_ROOT,
        )
    except OnboardingError as error:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_onboarding", "message": str(error),
        }}) from error
    except AccountProfileError as error:
        raise _raise_profile(error) from error
    return asdict(draft)


@router.post("/account-profiles/{account_id}/onboarding/confirm")
def post_onboarding_confirm(account_id: str) -> dict[str, Any]:
    try:
        profile = confirm_onboarding(
            account_id, now=_now(), accounts_root=DEFAULT_ACCOUNTS_ROOT,
        )
    except OnboardingError as error:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "invalid_onboarding", "message": str(error),
        }}) from error
    except AccountProfileError as error:
        raise _raise_profile(error) from error
    return asdict(profile)


@router.patch("/account-profiles/{account_id}")
def patch_profile(account_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        current = load_profile(account_id, accounts_root=DEFAULT_ACCOUNTS_ROOT)
        allowed = {
            key: body[key] for key in (
                "frequency", "timezone", "budget_usd", "delivery_target", "operations_enabled",
                "positioning", "audience", "style",
            ) if key in body
        }
        updated = update_profile(current, now=_now(), accounts_root=DEFAULT_ACCOUNTS_ROOT, **allowed)
    except AccountProfileError as error:
        raise _raise_profile(error) from error
    return asdict(updated)


@router.post("/account-profiles/{account_id}/authorization")
def post_authorization(account_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        auth = save_authorization(
            account_id,
            actor=str(body.get("actor") or "local"),
            now=_now(),
            operations_enabled=bool(body.get("operations_enabled")),
            allow_draft=bool(body.get("allow_draft", True)),
            allow_direct=bool(body.get("allow_direct", False)),
            quality_floor=float(body.get("quality_floor") or 0),
            accounts_root=DEFAULT_ACCOUNTS_ROOT,
        )
    except (AuthorizationError, AccountProfileError) as error:
        raise HTTPException(status_code=400, detail={"error": {
            "code": getattr(error, "code", "invalid_authorization"), "message": str(error),
        }}) from error
    return asdict(auth)


@router.post("/ops/tick")
def post_ops_tick() -> dict[str, Any]:
    from pipeline.ops_runner import tick_operations
    with deps._db() as conn:
        result = tick_operations(
            conn, now=_now(), accounts_root=DEFAULT_ACCOUNTS_ROOT,
            projects_root=projects_api._PROJECTS_ROOT,
        )
    return {"scheduled": result.scheduled, "ran": result.ran, "failed": result.failed}
