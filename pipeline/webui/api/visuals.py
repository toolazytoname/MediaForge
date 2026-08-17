"""Explicit project visual-plan and GPT Image candidate endpoints."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from PIL import Image, UnidentifiedImageError

from pipeline import projects as project_store
from pipeline import visuals
from pipeline.autonomy import AutonomyError, require_image_gen
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


@router.get("/projects/{project_id}/visuals/provider")
def provider_status(project_id: str) -> dict[str, Any]:
    """Expose capability state without leaking credentials or making a request."""
    try:
        visuals.load_visuals(project_id, projects_root=_root())
    except visuals.VisualsError as error:
        raise _visual_error(project_id, error) from error
    provider = image_gen._PROVIDER
    available = isinstance(provider, image_gen.OpenAIImageProvider)
    return {
        "available": available,
        "provider": "openai" if available else None,
        "model": provider._model if available else image_gen.OpenAIImageProvider.DEFAULT_MODEL,
        "reason": None if available else "未配置 OPENAI_API_KEY；可到设置配置，或导入本地 PNG 继续完成内容包。",
    }


@router.post("/projects/{project_id}/visuals/assets/import", status_code=201)
def import_asset(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Import a creator-owned PNG as an auditable zero-cost candidate."""
    required = {"slot_id", "prompt", "file_name", "data_base64"}
    if set(body) != required:
        raise _err(400, "invalid_visual_request", "local import requires slot_id, prompt, file_name and data_base64")
    if not all(isinstance(body[key], str) and body[key].strip() for key in required):
        raise _err(400, "invalid_visual_request", "local import fields must be non-empty text")
    file_name = body["file_name"].strip()
    if Path(file_name).name != file_name or not file_name.lower().endswith(".png"):
        raise _err(400, "invalid_visual_request", "local import must be a plain .png file name")
    try:
        raw = base64.b64decode(body["data_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise _err(400, "invalid_visual_request", "data_base64 is not valid base64") from error
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise _err(400, "invalid_visual_request", "PNG must be between 1 byte and 15 MB")
    try:
        image = Image.open(BytesIO(raw))
        if image.format != "PNG":
            raise ValueError("not PNG")
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _err(400, "invalid_visual_request", "uploaded content is not a valid PNG") from error
    try:
        plan = visuals.load_visuals(project_id, projects_root=_root())
        slot = next(item for item in plan.slots if item.id == body["slot_id"])
    except StopIteration:
        raise _err(400, "invalid_visual_request", "visual slot not found")
    except visuals.VisualsError as error:
        raise _visual_error(project_id, error) from error
    from pipeline.utils.ids import new_id
    asset_id = new_id("vas")
    path = visuals.asset_path(project_id, asset_id, projects_root=_root())
    image_gen._write_atomic(path, raw)
    try:
        from dataclasses import asdict
        return asdict(visuals.record_asset(
            project_id, slot_id=slot.id, prompt=body["prompt"], model="local-import",
            size=slot.aspect_ratio, cost_usd=0.0, now=_now(),
            file_path=f"assets/{asset_id}.png", status="candidate",
            projects_root=_root(), asset_id=asset_id,
        ))
    except visuals.VisualsError as error:
        path.unlink(missing_ok=True)
        raise _visual_error(project_id, error) from error


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

@router.get("/visual-library")
def list_visual_library() -> dict[str, Any]:
    """List project-owned vas_ assets so the library page is not a placeholder."""
    from dataclasses import asdict
    items: list[dict[str, Any]] = []
    try:
        projects = project_store.list_projects(projects_root=_root())
    except project_store.ProjectManifestError as error:
        raise _err(500, "project_manifest_invalid", str(error))
    for project in projects:
        try:
            plan = visuals.load_visuals(project.id, projects_root=_root())
        except visuals.VisualsError:
            continue
        for asset in plan.assets:
            payload = asdict(asset)
            payload["project_id"] = project.id
            payload["project_title"] = project.title
            payload["url"] = (
                f"/output/projects/{project.id}/{asset.file_path}" if asset.file_path else None
            )
            items.append(payload)
    return {"items": items, "total": len(items)}


def _create_asset(project_id: str, body: dict[str, Any], *, edit: bool) -> dict[str, Any]:
    try:
        require_image_gen(project_id, projects_root=_root())
    except AutonomyError as error:
        raise _err(error.http_status, error.code, str(error)) from error
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
