"""R3 read-only Project sidecar API."""
from __future__ import annotations

from typing import Any
from pathlib import Path
import shutil

from datetime import datetime, timezone

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from pipeline import creator_materials
from pipeline import creator_material_parsing
from pipeline import projects as project_store


router = APIRouter(tags=["projects"])
_PROJECTS_ROOT = project_store.DEFAULT_PROJECTS_ROOT


def _project_dict(project: project_store.Project) -> dict[str, Any]:
    """Serialize an immutable sidecar record without exposing storage details."""
    return {
        "id": project.id,
        "title": project.title,
        "idea": project.idea,
        "audience": project.audience,
        "goal": project.goal,
        "voice": project.voice,
        "autonomy": project.autonomy,
        "content_ids": list(project.content_ids),
        "asset_paths": list(project.asset_paths),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _manifest_error(project_id: str | None, error: Exception) -> HTTPException:
    message = str(error)
    if project_id is not None and message == f"project not found: {project_id}":
        return HTTPException(status_code=404, detail={"error": {
            "code": "project_not_found",
            "message": message,
        }})
    subject = project_id or "project collection"
    return HTTPException(status_code=500, detail={"error": {
        "code": "project_manifest_invalid",
        "message": f"cannot read {subject}: {message}",
    }})


def _invalid_input(code: str, error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": {
        "code": code,
        "message": str(error),
    }})


def create_project_from_input(body: dict[str, Any]) -> project_store.Project:
    """严格挑选公开字段，避免把请求体扩散进冻结的 Project v0。"""
    allowed = {"title", "idea", "audience", "goal", "voice", "autonomy"}
    if set(body) != allowed:
        raise project_store.ProjectManifestError(
            "project body must contain only title, idea, audience, goal, voice and autonomy"
        )
    return project_store.create_project(
        title=body["title"], idea=body["idea"], audience=body["audience"],
        goal=body["goal"], voice=body["voice"], autonomy=body["autonomy"],
        now=datetime.now(timezone.utc).isoformat(), projects_root=_PROJECTS_ROOT,
    )


def create_project_from_creator_prompt(body: dict[str, Any]) -> project_store.Project:
    """Create a v0 sidecar from the single creator-facing home input.

    The remaining manifest fields deliberately receive product defaults here,
    rather than making a new creator understand project configuration first.
    """
    allowed = {"prompt", "draft_id", "material_ids"}
    if not set(body).issubset(allowed) or set(body) == set():
        raise project_store.ProjectManifestError("creator start body must contain prompt and optional draft_id/material_ids")
    prompt = body["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise project_store.ProjectManifestError("prompt must be a non-empty string")
    normalized = prompt.strip()
    title = normalized.splitlines()[0].strip()[:100]
    return project_store.create_project(
        title=title,
        idea=normalized,
        audience="希望把 AI 用进真实生活的普通人",
        goal="完成一篇可继续编辑的图文文章",
        voice="真实、清楚、有个人判断",
        autonomy="collaborate",
        now=datetime.now(timezone.utc).isoformat(), projects_root=_PROJECTS_ROOT,
    )


def _material_dict(material: creator_materials.CreatorMaterial, *, project_id: str | None = None) -> dict[str, Any]:
    data = {
        "id": material.id, "kind": material.kind, "source": material.source,
        "original_name": material.original_name, "sha256": material.sha256,
        "created_at": material.created_at, "status": material.status,
        "error": material.error, "stored_path": material.stored_path,
    }
    if project_id is not None:
        data["analysis"] = creator_material_parsing.get_project_material_analysis(
            project_id, material.id, projects_root=_PROJECTS_ROOT
        )
    return data


def _material_error(error: creator_materials.CreatorMaterialError) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": {
        "code": "invalid_creator_material", "message": str(error),
    }})


@router.get("/projects")
def list_projects() -> dict[str, Any]:
    """List valid manifests newest first; malformed data is deliberately visible."""
    try:
        items = project_store.list_projects(projects_root=_PROJECTS_ROOT)
    except project_store.ProjectManifestError as error:
        raise _manifest_error(None, error) from error
    return {"items": [_project_dict(item) for item in items], "total": len(items)}


@router.post("/projects", status_code=201)
def create_project(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """显式创建 sidecar Project；不写 SQLite、不调用 AI。"""
    try:
        project = create_project_from_input(body)
    except project_store.ProjectManifestError as error:
        raise _invalid_input("invalid_project_input", error) from error
    return _project_dict(project)


@router.post("/projects/creator-start", status_code=201)
def creator_start_project(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """One-click creator entrypoint; it stores intent but does not invoke AI yet."""
    try:
        draft_id = body.get("draft_id")
        material_ids = body.get("material_ids", [])
        if draft_id is not None:
            if not isinstance(draft_id, str) or not isinstance(material_ids, list) or any(not isinstance(item, str) for item in material_ids):
                raise project_store.ProjectManifestError("draft_id and material_ids must be strings")
            creator_materials.selected_draft_materials(draft_id, material_ids, projects_root=_PROJECTS_ROOT)
        elif material_ids:
            raise project_store.ProjectManifestError("material_ids require a draft_id")
        project = create_project_from_creator_prompt(body)
        if draft_id is not None:
            creator_materials.attach_draft_materials(draft_id, project.id, material_ids, projects_root=_PROJECTS_ROOT)
    except project_store.ProjectManifestError as error:
        raise _invalid_input("invalid_creator_start", error) from error
    except creator_materials.CreatorMaterialError as error:
        if "project" in locals():
            _remove_new_project(project.id)
        raise _invalid_input("invalid_creator_start", error) from error
    return _project_dict(project)


def _remove_new_project(project_id: str) -> None:
    """Remove only a directory created by this request after an attach failure."""
    candidate = Path(_PROJECTS_ROOT) / project_id
    if candidate.parent == Path(_PROJECTS_ROOT) and candidate.name == project_id:
        shutil.rmtree(candidate, ignore_errors=True)


@router.get("/creator-materials/drafts/{draft_id}")
def get_draft_materials(draft_id: str) -> dict[str, Any]:
    try:
        return {"items": [_material_dict(item) for item in creator_materials.list_draft_materials(draft_id, projects_root=_PROJECTS_ROOT)]}
    except creator_materials.CreatorMaterialError as error:
        raise _material_error(error) from error


@router.post("/creator-materials", status_code=201)
def create_creator_material(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        if set(body) != {"draft_id", "kind", "value"}:
            raise creator_materials.CreatorMaterialError("material body must contain draft_id, kind and value")
        if body["kind"] == "text":
            item = creator_materials.add_text_material(body["draft_id"], body["value"], projects_root=_PROJECTS_ROOT)
        elif body["kind"] == "url":
            item = creator_materials.add_url_material(body["draft_id"], body["value"], projects_root=_PROJECTS_ROOT)
        else:
            raise creator_materials.CreatorMaterialError("kind must be text or url")
        return _material_dict(item)
    except creator_materials.CreatorMaterialError as error:
        raise _material_error(error) from error


@router.post("/creator-materials/upload", status_code=201)
async def upload_creator_material(
    draft_id: str = Form(...), file: UploadFile = File(...)
) -> dict[str, Any]:
    try:
        payload = await file.read(creator_materials.MAX_FILE_BYTES + 1)
        item = creator_materials.add_file_material(draft_id, file.filename or "", payload, file.content_type, projects_root=_PROJECTS_ROOT)
        return _material_dict(item)
    except creator_materials.CreatorMaterialError as error:
        raise _material_error(error) from error
    finally:
        await file.close()


@router.delete("/creator-materials/drafts/{draft_id}/{material_id}", status_code=204)
def delete_creator_material(draft_id: str, material_id: str) -> None:
    try:
        creator_materials.remove_draft_material(draft_id, material_id, projects_root=_PROJECTS_ROOT)
    except creator_materials.CreatorMaterialError as error:
        raise _material_error(error) from error


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Read one manifest. This endpoint never creates or changes a project."""
    try:
        project = project_store.load_project(project_id, projects_root=_PROJECTS_ROOT)
    except project_store.ProjectManifestError as error:
        raise _manifest_error(project_id, error) from error
    return _project_dict(project)


@router.get("/projects/{project_id}/materials")
def get_project_materials(project_id: str) -> dict[str, Any]:
    try:
        project_store.load_project(project_id, projects_root=_PROJECTS_ROOT)
        return {"items": [_material_dict(item, project_id=project_id) for item in creator_materials.list_project_materials(project_id, projects_root=_PROJECTS_ROOT)]}
    except project_store.ProjectManifestError as error:
        raise _manifest_error(project_id, error) from error
    except creator_materials.CreatorMaterialError as error:
        raise _material_error(error) from error


@router.post("/projects/{project_id}/materials/{material_id}/parse")
def parse_project_material(project_id: str, material_id: str) -> dict[str, Any]:
    """Explicitly parse one project-local source; never calls an LLM."""
    try:
        project_store.load_project(project_id, projects_root=_PROJECTS_ROOT)
        return creator_material_parsing.parse_project_material(
            project_id, material_id, projects_root=_PROJECTS_ROOT
        )
    except project_store.ProjectManifestError as error:
        raise _manifest_error(project_id, error) from error
    except (creator_materials.CreatorMaterialError, creator_material_parsing.MaterialParseError) as error:
        raise _material_error(error) from error
