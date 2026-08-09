"""Project platform variants and safe, local-only preview endpoints."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse

from pipeline import variants as variant_store
from pipeline import visuals
from pipeline.webui.api import projects as projects_api

router = APIRouter(tags=["variants"])
def _root(): return projects_api._PROJECTS_ROOT
def _now(): return datetime.now(timezone.utc).isoformat()
def _err(status: int, code: str, error: Exception | str): return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})
def _variant_error(project_id: str, error: variant_store.VariantsError):
    text = str(error)
    if text == f"project not found: {project_id}": return _err(404, "project_not_found", error)
    if text.startswith("master not found") or text.startswith("variant not found") or text.startswith("variant version not found"): return _err(404, "variant_record_not_found", error)
    return _err(400, "invalid_variant_request", error)

@router.get("/projects/{project_id}/variants")
def get_variants(project_id: str) -> dict[str, Any]:
    try: return asdict(variant_store.load_variants(project_id, projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.post("/projects/{project_id}/variants/{platform}", status_code=201)
def create_variant(project_id: str, platform: str) -> dict[str, Any]:
    try: return asdict(variant_store.create_from_master(project_id, platform, now=_now(), projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.put("/projects/{project_id}/variants/{platform}")
def save_variant(project_id: str, platform: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) != {"title", "summary", "body", "asset_ids"}: raise _err(400, "invalid_variant_request", "variant body requires title, summary, body and asset_ids")
    try: return asdict(variant_store.save_manual(project_id, platform, title=body["title"], summary=body["summary"], body=body["body"], asset_ids=body["asset_ids"], now=_now(), projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.post("/projects/{project_id}/variants/{platform}/lock")
def lock_variant(project_id: str, platform: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if set(body) != {"locked"}: raise _err(400, "invalid_variant_request", "lock body requires only locked")
    try: return asdict(variant_store.set_locked(project_id, platform, locked=body["locked"], now=_now(), projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.post("/projects/{project_id}/variants/{platform}/check-upstream")
def check_upstream(project_id: str, platform: str) -> dict[str, Any]:
    try: return asdict(variant_store.check_upstream(project_id, platform, now=_now(), projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.post("/projects/{project_id}/variants/{platform}/versions/{version}/restore")
def restore_variant(project_id: str, platform: str, version: int) -> dict[str, Any]:
    try: return asdict(variant_store.restore_version(project_id, platform, version, now=_now(), projects_root=_root()))
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error

@router.get("/projects/{project_id}/variants/{platform}/preview", response_class=HTMLResponse)
def preview_variant(project_id: str, platform: str) -> HTMLResponse:
    try:
        if platform not in {"wechat_mp", "toutiao"}:
            raise variant_store.VariantsError("platform must be wechat_mp or toutiao")
        variant = next(item for item in variant_store.load_variants(project_id, projects_root=_root()).variants if item.platform == platform)
    except StopIteration: raise _err(404, "variant_record_not_found", f"variant not found: {platform}")
    except variant_store.VariantsError as error: raise _variant_error(project_id, error) from error
    label = "微信公众号" if variant.platform == "wechat_mp" else "今日头条"
    guidance = "长文阅读节奏、引用与图文排版" if variant.platform == "wechat_mp" else "更直接的标题、开场和信息密度"
    try:
        plan = visuals.load_visuals(project_id, projects_root=_root())
        paths = {asset.id: asset.file_path for asset in plan.assets if asset.status == "selected" and asset.file_path}
    except visuals.VisualsError as error:
        raise _err(400, "invalid_variant_request", f"cannot read visuals: {error}") from error
    images = "".join(f'<figure><img src="/output/projects/{escape(project_id)}/{escape(paths[asset_id])}" alt="已选择视觉资产 {escape(asset_id)}"><figcaption>{escape(asset_id)}</figcaption></figure>' for asset_id in variant.asset_ids if asset_id in paths) or "<p>尚未选择视觉资产</p>"
    html = f"<!doctype html><html lang=zh-CN><meta charset=utf-8><title>{escape(variant.title)}</title><style>main{{max-width:720px;margin:32px auto;font-family:system-ui}}img{{max-width:100%;height:auto}}figure{{margin:20px 0}}aside{{padding:12px;background:#f5f2ed}}</style><main><p>只读预览 · {label}</p><h1>{escape(variant.title)}</h1><p>{escape(variant.summary)}</p><article>{''.join(f'<p>{escape(p)}</p>' for p in variant.body.splitlines() if p.strip())}</article><aside>平台适配：{guidance}</aside><h2>视觉资产</h2>{images}</main></html>"
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
