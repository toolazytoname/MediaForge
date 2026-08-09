"""Explicit project visual-plan and GPT Image candidate endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import visuals
from pipeline.creators import image_gen
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["visuals"])

def _root(): return projects_api._PROJECTS_ROOT
def _now(): return datetime.now(timezone.utc).isoformat()
def _err(status: int, code: str, message: str): return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})
def _plan(plan: visuals.VisualPlan) -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(plan)
def _visual_error(project_id: str, error: visuals.VisualsError) -> HTTPException:
    if str(error) == f"project not found: {project_id}": return _err(404, "project_not_found", str(error))
    return _err(400, "invalid_visual_request", str(error))

@router.get("/projects/{project_id}/visuals")
def get_visuals(project_id: str) -> dict[str, Any]:
    try: return _plan(visuals.load_visuals(project_id, projects_root=_root()))
    except visuals.VisualsError as error: raise _visual_error(project_id, error) from error

@router.put("/projects/{project_id}/visuals")
def save_visuals(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) != {"bible", "slots"}: raise _err(400, "invalid_visual_request", "visual plan requires only bible and slots")
    try: return _plan(visuals.save_plan(project_id, bible=body["bible"], slots=body["slots"], projects_root=_root()))
    except visuals.VisualsError as error: raise _visual_error(project_id, error) from error

def _provider() -> image_gen.OpenAIImageProvider:
    provider = image_gen._PROVIDER
    if not isinstance(provider, image_gen.OpenAIImageProvider):
        raise _err(503, "image_provider_unavailable", "GPT Image 2 is unavailable. Configure OPENAI_API_KEY in Settings.")
    return provider


def _record_unavailable(project_id: str, *, slot: visuals.VisualSlot, prompt: str, reference_asset_id: str | None) -> None:
    """Persist an attempted, but unconfigured, user-requested generation."""
    try:
        visuals.record_asset(
            project_id, slot_id=slot.id, prompt=prompt, model=image_gen.OpenAIImageProvider.DEFAULT_MODEL,
            size=slot.aspect_ratio, cost_usd=0.0, now=_now(), file_path=None, status="failed",
            failure="GPT Image 2 is unavailable. Configure OPENAI_API_KEY in Settings.",
            reference_asset_id=reference_asset_id, projects_root=_root(),
        )
    except visuals.VisualsError:
        # The original request error remains the actionable response. This
        # defensive branch must not turn a missing provider into a server error.
        pass


def _edit_with_retry(provider: image_gen.OpenAIImageProvider, prompt: str, *, image_path: Path, aspect_ratio: str) -> list[bytes]:
    """Use the same bounded retry policy as ImageProvider.call for edits."""
    last_error: image_gen.RetryableError | None = None
    for attempt in range(1, image_gen._RETRY_MAX_ATTEMPTS + 1):
        try:
            return provider.edit(prompt, image_path=image_path, aspect_ratio=aspect_ratio)
        except image_gen.RetryableError as error:
            last_error = error
            if attempt < image_gen._RETRY_MAX_ATTEMPTS:
                time.sleep(image_gen._RETRY_BASE_SLEEP_S * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error

def _create_asset(project_id: str, body: dict[str, Any], *, edit: bool) -> dict[str, Any]:
    allowed = {"slot_id", "prompt"} | ({"reference_asset_id"} if edit else set())
    if set(body) != allowed: raise _err(400, "invalid_visual_request", "invalid image request fields")
    if not isinstance(body["slot_id"], str) or not body["slot_id"].strip():
        raise _err(400, "invalid_visual_request", "slot_id must be non-empty text")
    if not isinstance(body["prompt"], str) or not body["prompt"].strip():
        raise _err(400, "invalid_visual_request", "prompt must be non-empty text")
    if edit and (not isinstance(body["reference_asset_id"], str) or not body["reference_asset_id"].strip()):
        raise _err(400, "invalid_visual_request", "reference_asset_id must be non-empty text")
    try:
        plan = visuals.load_visuals(project_id, projects_root=_root()); slot = next(item for item in plan.slots if item.id == body["slot_id"])
    except StopIteration: raise _err(400, "invalid_visual_request", "visual slot not found")
    except visuals.VisualsError as error: raise _visual_error(project_id, error) from error
    parent = None
    if edit:
        parent = next((item for item in plan.assets if item.id == body["reference_asset_id"]), None)
        if parent is None:
            raise _err(400, "invalid_visual_request", "reference visual asset not found")
        if not parent.file_path:
            raise _err(400, "invalid_visual_request", "reference asset has no file")
    try:
        provider = _provider()
    except HTTPException as error:
        _record_unavailable(project_id, slot=slot, prompt=body["prompt"], reference_asset_id=body.get("reference_asset_id"))
        raise error
    asset_id = None
    try:
        from pipeline.utils.ids import new_id
        asset_id = new_id("vas"); path = visuals.asset_path(project_id, asset_id, projects_root=_root())
        images = _edit_with_retry(provider, body["prompt"], image_path=Path(_root()) / project_id / parent.file_path, aspect_ratio=slot.aspect_ratio) if edit else image_gen._call_with_retry(provider, body["prompt"], aspect_ratio=slot.aspect_ratio, n=1)
        image_gen._write_atomic(path, images[0])
        asset = visuals.record_asset(project_id, slot_id=slot.id, prompt=body["prompt"], model=provider._model, size=slot.aspect_ratio, cost_usd=provider.estimated_cost_usd(aspect_ratio=slot.aspect_ratio), now=_now(), file_path=f"assets/{asset_id}.png", status="candidate", reference_asset_id=body.get("reference_asset_id"), projects_root=_root(), asset_id=asset_id)
        from dataclasses import asdict
        return asdict(asset)
    except (visuals.VisualsError, ValueError, image_gen.RetryableError, IndexError) as error:
        try:
            visuals.record_asset(project_id, slot_id=body["slot_id"], prompt=body.get("prompt", "invalid"), model=provider._model, size=slot.aspect_ratio, cost_usd=provider.estimated_cost_usd(aspect_ratio=slot.aspect_ratio), now=_now(), file_path=None, status="failed", failure=str(error), reference_asset_id=body.get("reference_asset_id"), projects_root=_root(), asset_id=asset_id)
        except visuals.VisualsError: pass
        raise _err(502, "image_generation_failed", str(error)) from error

@router.post("/projects/{project_id}/visuals/assets", status_code=201)
def generate(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]: return _create_asset(project_id, body, edit=False)
@router.post("/projects/{project_id}/visuals/assets/edit", status_code=201)
def edit(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]: return _create_asset(project_id, body, edit=True)

@router.post("/projects/{project_id}/visuals/assets/{asset_id}/select")
def select(project_id: str, asset_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) - {"reason", "rating"} or "reason" not in body: raise _err(400, "invalid_visual_request", "selection requires reason and optional rating")
    try:
        from dataclasses import asdict
        return asdict(visuals.select_asset(project_id, asset_id, reason=body["reason"], rating=body.get("rating"), projects_root=_root()))
    except visuals.VisualsError as error: raise _visual_error(project_id, error) from error
