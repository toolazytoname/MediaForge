"""Project platform variants and safe, local-only preview endpoints."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from pipeline import master_documents, project_exports, research
from pipeline import variants as variant_store
from pipeline import visuals
from pipeline.autonomy import AutonomyError, require_llm
from pipeline.creators import llm
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api
from pipeline.webui.mdrender import md_to_html

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

def _llm_is_configured() -> bool:
    return not isinstance(llm._PROVIDER, llm.MockProvider)


def _parse_adaptation(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines(); cleaned = "\n".join(lines[1:-1]).strip()
    payload = json.loads(cleaned)
    required = {"title", "summary", "body"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("platform adaptation requires only title, summary and body")
    if any(not isinstance(payload[key], str) or not payload[key].strip() for key in required):
        raise ValueError("platform adaptation fields must be non-empty text")
    return {key: payload[key].strip() for key in required}


def _adapt_prompt(project_id: str, platform: str) -> str:
    project = projects_api.project_store.load_project(project_id, projects_root=_root())
    master = master_documents.load_master(project_id, projects_root=_root())
    if master is None: raise variant_store.VariantsError(f"master not found: {project_id}")
    board = research.load_research(project_id, projects_root=_root())
    sources = "\n".join(
        f"[{item.id}] {item.title} | {item.reference} | {item.summary}"
        for item in board.sources
    ) or "- none"
    facts = "\n".join(
        f"- {item.kind}/{item.status} sources={','.join(item.source_ids) or 'none'}: {item.text}"
        for item in board.claims
    ) or "- none"
    if platform == "wechat_mp":
        platform_rule = "微信公众号：保留完整论证，优化长文阅读节奏、摘要、二级标题与段落留白。"
    else:
        platform_rule = "今日头条：标题和开场更直接，提高信息密度，但不得夸大、标题党或删除关键限制。"
    return f"""把主稿重组为一个平台原生版本。这是显式创建动作，不得改写主稿。
平台要求：{platform_rule}
读者：{project.audience}
目标：{project.goal}
声音：{project.voice}
可引用来源（只允许使用这里给出的 URL）：
{sources}
事实边界：
{facts}

主稿标题：{master.title}
主稿正文：
{master.body}

保留主张、证据边界、反方观点和作者声音。保留主稿已有的 Markdown 引用；外部事实首次出现时使用 `[来源标题](URL)`，文末附精简参考资料，不得虚构 URL，也不要为 local: 引用创建链接。只返回严格 JSON：
{{"title":"...","summary":"...","body":"Markdown 正文"}}"""


@router.post("/projects/{project_id}/variants/{platform}", response_model=None)
def create_variant(project_id: str, platform: str, body: dict[str, Any] | None = Body(None)):
    try:
        existing = next((item for item in variant_store.load_variants(project_id, projects_root=_root()).variants if item.platform == platform), None)
        if existing is not None and (body is None or body.get("adapt_with_ai") is True):
            return JSONResponse(status_code=200, content=asdict(existing))
        if body is None:
            result = variant_store.create_from_master(project_id, platform, now=_now(), projects_root=_root())
            return JSONResponse(status_code=201, content=asdict(result))
        if set(body) == {"title", "summary", "body"}:
            result = variant_store.create_adapted(
                project_id, platform, title=body["title"], summary=body["summary"],
                body=body["body"], now=_now(), projects_root=_root(),
            )
            payload = asdict(result)
            payload["persisted"] = True
            return JSONResponse(status_code=201, content=payload)
        if set(body) != {"adapt_with_ai"} or body["adapt_with_ai"] is not True:
            raise variant_store.VariantsError("variant creation accepts adapt_with_ai=true or an explicit title/summary/body accept")
        _project, policy = require_llm(project_id, projects_root=_root())
        if not _llm_is_configured():
            raise _err(503, "llm_provider_unavailable", "AI provider is not configured; create a copy and edit manually instead")
        prompt = _adapt_prompt(project_id, platform)
        conn = deps.get_conn()
        try:
            adapted = llm.complete_json(
                prompt, stage=f"variant_{platform}", ref_id=project_id,
                model_tier="creative", max_tokens=6000, conn=conn,
                parse=_parse_adaptation,
            )
        finally:
            conn.close()
        if not policy.persist_ai_adapt:
            return JSONResponse(status_code=200, content={
                "persisted": False,
                "platform": platform,
                "title": adapted["title"],
                "summary": adapted["summary"],
                "body": adapted["body"],
            })
        result = variant_store.create_adapted(
            project_id, platform, **adapted, now=_now(), projects_root=_root(),
        )
        payload = asdict(result)
        payload["persisted"] = True
        return JSONResponse(status_code=201, content=payload)
    except HTTPException:
        raise
    except AutonomyError as error:
        raise _err(error.http_status, error.code, error) from error
    except variant_store.VariantsError as error:
        raise _variant_error(project_id, error) from error
    except Exception as error:
        raise _err(502, "llm_variant_failed", f"AI platform adaptation failed: {error}") from error

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

@router.post("/projects/{project_id}/variants/{platform}/acknowledge-master")
def acknowledge_master_update(project_id: str, platform: str) -> dict[str, Any]:
    try:
        return asdict(variant_store.acknowledge_master_update(
            project_id, platform, now=_now(), projects_root=_root(),
        ))
    except variant_store.VariantsError as error:
        raise _variant_error(project_id, error) from error

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
    except visuals.VisualsError as error:
        raise _err(400, "invalid_variant_request", f"cannot read visuals: {error}") from error
    markdown = project_exports.render_variant_markdown(
        variant, plan, image_prefix=f"/output/projects/{project_id}/assets/",
    )
    article = md_to_html(markdown)
    html = f"<!doctype html><html lang=zh-CN><meta charset=utf-8><title>{escape(variant.title)}</title><style>body{{margin:0;background:#f4f0e8;color:#2c2926}}main{{max-width:720px;margin:0 auto;padding:32px 28px 72px;background:#fffdf8;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;line-height:1.85}}h1{{font-size:34px;line-height:1.3}}h2{{margin-top:2em}}img{{display:block;width:100%;height:auto;margin:26px 0;border-radius:6px}}aside{{margin-top:32px;padding:12px 16px;background:#f5f2ed;color:#6f675f}}.preview-label{{color:#8a7358;font-size:13px}}</style><main><p class=preview-label>只读预览 · {label}</p><article>{article}</article><aside>平台适配：{guidance}</aside></main></html>"
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
