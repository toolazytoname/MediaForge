"""Project-scoped MasterDocument and explicit AI proposal API."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import creator_article_generation
from pipeline import creator_material_parsing
from pipeline import master_documents as master_store
from pipeline import research as research_store
from pipeline.creators import image_gen, llm
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["master-documents"])


def _root():
    return projects_api._PROJECTS_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: int, code: str, error: Exception | str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(error)}})


def _master_dict(master: master_store.MasterDocument | None) -> dict[str, Any] | None:
    if master is None:
        return None
    return {"project_id": master.project_id, "title": master.title, "body": master.body,
            "version": master.version, "created_at": master.created_at, "updated_at": master.updated_at,
            "history": [_snapshot_dict(item) for item in master.history]}


def _generation_dict(outcome: creator_article_generation.GenerationOutcome) -> dict[str, Any]:
    return {"status": outcome.status, "title": outcome.title, "body": outcome.body,
            "completed_images": outcome.completed_images, "failed_images": outcome.failed_images, "error": outcome.error}


def _snapshot_dict(snapshot: master_store.MasterSnapshot) -> dict[str, Any]:
    return {"version": snapshot.version, "title": snapshot.title, "body": snapshot.body,
            "saved_at": snapshot.saved_at, "reason": snapshot.reason}


def _suggestion_dict(suggestion: master_store.MasterSuggestion) -> dict[str, Any]:
    return {"id": suggestion.id, "project_id": suggestion.project_id, "action": suggestion.action,
            "selection": suggestion.selection, "base_version": suggestion.base_version,
            "proposed_title": suggestion.proposed_title, "proposed_body": suggestion.proposed_body,
            "status": suggestion.status, "created_at": suggestion.created_at, "decided_at": suggestion.decided_at}


def _master_error(project_id: str, error: master_store.MasterDocumentError) -> HTTPException:
    message = str(error)
    if message == f"project not found: {project_id}":
        return _error(404, "project_not_found", error)
    if message == f"master not found: {project_id}":
        return _error(404, "master_not_found", error)
    if message.startswith("suggestion not found:") or message.startswith("master version not found:"):
        return _error(404, "master_record_not_found", error)
    if message.startswith("cannot read project:") or "manifest" in message or "markdown does not match" in message:
        return _error(500, "master_manifest_invalid", error)
    return _error(400, "invalid_master_request", error)


def _manual_input(body: dict[str, Any]) -> tuple[str, str]:
    if set(body) != {"title", "body"}:
        raise master_store.MasterDocumentError("master body must contain only title and body")
    return body["title"], body["body"]


def _suggestion_input(body: dict[str, Any]) -> tuple[str, str | None]:
    if set(body) not in ({"action"}, {"action", "selection"}):
        raise master_store.MasterDocumentError("suggestion body must contain action and optional selection")
    return body["action"], body.get("selection")


def _parse_article(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or set(payload) != {"title", "body"}:
        raise ValueError("draft must contain only title and body")
    if any(not isinstance(payload[key], str) or not payload[key].strip() for key in payload):
        raise ValueError("draft title and body must be non-empty text")
    return {key: payload[key].strip() for key in ("title", "body")}


def _draft_prompt(project_id: str) -> str:
    project = projects_api.project_store.load_project(project_id, projects_root=_root())
    board = research_store.load_research(project_id, projects_root=_root())
    sources = "\n".join(
        f"[{item.id}] {item.title} | {item.reference} | {item.summary}"
        for item in board.sources
    ) or "（无来源）"
    claims = "\n".join(
        f"- {item.kind}/{item.status} sources={','.join(item.source_ids) or 'none'}: {item.text}"
        f" | limitation={item.limitation or 'none'} | counterpoint={item.counterpoint or 'none'}"
        for item in board.claims
    ) or "（无声明）"
    return f"""你是中文资深编辑。为下面的真实创作项目提出一份可审阅初稿，不要发布，也不要声称已替作者确认。
项目想法：{project.idea}
目标读者：{project.audience}
发布目的：{project.goal}
声音：{project.voice}
自主程度：{project.autonomy}

来源：
{sources}

声明：
{claims}

规则：
1. 只把 status=verified 且有来源的 fact 当作确定事实；judgment 必须写成作者判断；open_question 不得伪装成结论。
2. 不虚构数字、引语、案例或个人经历；保留限制与有力反方观点。
3. 外部事实首次出现时使用来源区提供的 Markdown 链接 `[来源标题](URL)`，文末附精简参考资料；不得虚构 URL，也不要为 local: 引用创建链接。
4. 写成 1500—2500 字、结构清楚、有真实问题和明确主张的中文长文，使用 Markdown 二级标题。
5. 只返回严格 JSON：{{"title":"...","body":"..."}}，不要代码围栏或额外文字。"""


@router.post("/projects/{project_id}/master/draft")
def propose_draft(project_id: str) -> dict[str, str]:
    """Generate a review-only first draft; never persist or overwrite the master."""
    try:
        prompt = _draft_prompt(project_id)
    except (master_store.MasterDocumentError, research_store.ResearchManifestError,
            projects_api.project_store.ProjectManifestError) as error:
        if str(error) == f"project not found: {project_id}":
            raise _error(404, "project_not_found", error) from error
        raise _error(400, "invalid_draft_context", error) from error
    if not _llm_is_configured():
        raise _error(503, "llm_provider_unavailable", "AI provider is not configured; write the first draft manually or configure a text provider")
    try:
        conn = deps.get_conn()
        try:
            return llm.complete_json(
                prompt, stage="project_master_draft", ref_id=project_id,
                model_tier="creative", max_tokens=6000, conn=conn,
                parse=_parse_article,
            )
        finally:
            conn.close()
    except Exception as error:
        raise _error(502, "llm_draft_failed", f"AI draft failed: {error}") from error


@router.get("/projects/{project_id}/article/generation")
def get_article_generation(project_id: str) -> dict[str, Any]:
    """Return a recoverable, creator-facing progress record."""
    try:
        outcome = creator_article_generation.load_generation(project_id, projects_root=_root())
        return {"generation": _generation_dict(outcome) if outcome is not None else None}
    except (creator_article_generation.ArticleGenerationError, master_store.MasterDocumentError) as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/article/generate")
def generate_complete_article(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Explicitly generate a first editable draft and its in-context images."""
    if body:
        raise _error(400, "invalid_generation_request", "generation request must be empty")
    if not _llm_is_configured():
        raise _error(503, "llm_provider_unavailable", "AI provider is not configured; your idea is kept and you can retry or write manually")
    provider = image_gen._PROVIDER
    if not hasattr(provider, "call"):
        image_model = "unavailable"
        image_cost = lambda _ratio: 0.0
        def create_image(_prompt: str, _ratio: str) -> bytes:
            raise image_gen.ImageProviderError("GPT Image 2 is unavailable. Configure it in Settings and retry this image.")
    else:
        image_model = getattr(provider, "_model", "image-provider")
        image_cost = lambda ratio: provider.estimated_cost_usd(aspect_ratio=ratio) if hasattr(provider, "estimated_cost_usd") else 0.0
        def create_image(prompt: str, ratio: str) -> bytes:
            return image_gen._call_with_retry(provider, prompt, aspect_ratio=ratio, n=1)[0]
    try:
        def write_article(prompt: str) -> dict[str, str]:
            conn = deps.get_conn()
            try:
                return llm.complete_json(prompt, stage="creator_article_draft", ref_id=project_id,
                    model_tier="creative", max_tokens=6000, conn=conn, parse=_parse_article)
            finally:
                conn.close()
        outcome = creator_article_generation.generate_article(project_id, projects_root=_root(), now=_now(),
            write_article=write_article, make_image=create_image, image_model=image_model, image_cost=image_cost,
            source_context=creator_material_parsing.project_material_context(project_id, projects_root=_root()))
        return _generation_dict(outcome)
    except (creator_article_generation.ArticleGenerationError, master_store.MasterDocumentError,
            projects_api.project_store.ProjectManifestError) as error:
        raise _master_error(project_id, error) from error
    except Exception as error:
        raise _error(502, "article_generation_failed", f"article generation failed: {error}") from error


@router.post("/projects/{project_id}/article/images/retry")
def retry_article_images(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """A separate, auditable retry for failed image locations only."""
    if body:
        raise _error(400, "invalid_generation_request", "image retry request must be empty")
    provider = image_gen._PROVIDER
    if not hasattr(provider, "call"):
        raise _error(503, "image_provider_unavailable", "GPT Image 2 is unavailable; configure it in Settings before retrying this image")
    try:
        outcome = creator_article_generation.retry_failed_images(project_id, projects_root=_root(), now=_now(),
            make_image=lambda prompt, ratio: image_gen._call_with_retry(provider, prompt, aspect_ratio=ratio, n=1)[0],
            image_model=getattr(provider, "_model", "image-provider"),
            image_cost=lambda ratio: provider.estimated_cost_usd(aspect_ratio=ratio) if hasattr(provider, "estimated_cost_usd") else 0.0)
        return _generation_dict(outcome)
    except (creator_article_generation.ArticleGenerationError, master_store.MasterDocumentError) as error:
        raise _master_error(project_id, error) from error
    except Exception as error:
        raise _error(502, "image_generation_failed", f"image retry failed: {error}") from error


@router.get("/projects/{project_id}/master")
def get_master(project_id: str) -> dict[str, Any]:
    try:
        return {"master": _master_dict(master_store.load_master(project_id, projects_root=_root()))}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.put("/projects/{project_id}/master")
def save_master(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        title, text = _manual_input(body)
        master = master_store.save_manual(project_id, title=title, body=text, now=_now(), projects_root=_root())
        return _master_dict(master) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.get("/projects/{project_id}/master/versions")
def get_versions(project_id: str) -> dict[str, Any]:
    try:
        return {"items": [_snapshot_dict(item) for item in master_store.list_versions(project_id, projects_root=_root())]}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/versions/{version}/restore")
def restore_master(project_id: str, version: int) -> dict[str, Any]:
    try:
        return _master_dict(master_store.restore_version(project_id, version, now=_now(), projects_root=_root())) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.get("/projects/{project_id}/master/suggestions")
def get_suggestions(project_id: str) -> dict[str, Any]:
    try:
        return {"items": [_suggestion_dict(item) for item in master_store.load_suggestions(project_id, projects_root=_root())]}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions", status_code=201)
def request_suggestion(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Call the existing LLM wrapper only after an explicit user request.

    The response becomes a pending proposal; it can never mutate the master in
    this handler.
    """
    try:
        action, selection = _suggestion_input(body)
        if action not in {"clarify", "shorten", "change_voice", "add_counterpoint"}:
            raise master_store.MasterDocumentError(
                "suggestion action must be clarify, shorten, change_voice or add_counterpoint"
            )
        master = master_store.load_master(project_id, projects_root=_root())
        if master is None:
            raise master_store.MasterDocumentError(f"master not found: {project_id}")
        if selection is not None and master.body.count(selection) != 1:
            raise master_store.MasterDocumentError(
                "selection must occur exactly once in the current master body"
            )
        prompt = _suggestion_prompt(project_id, master, action, selection)
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error
    if not _llm_is_configured():
        raise _error(503, "llm_provider_unavailable", "AI provider is not configured; save or write manually instead")
    try:
        conn = deps.get_conn()
        try:
            proposed = llm.complete(prompt, stage="coauthor_suggestion", ref_id=project_id,
                                    model_tier="creative", max_tokens=4096, conn=conn)
        finally:
            conn.close()
    except Exception as error:
        raise _error(502, "llm_suggestion_failed", f"AI suggestion failed: {error}") from error
    try:
        proposed_title = master.title
        proposed_body = _replace_selection(master.body, selection, proposed) if selection is not None else proposed
        suggestion = master_store.create_suggestion(project_id, action=action, selection=selection,
            proposed_title=proposed_title, proposed_body=proposed_body, now=_now(), projects_root=_root())
        return _suggestion_dict(suggestion)
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions/{suggestion_id}/accept")
def accept_suggestion(project_id: str, suggestion_id: str) -> dict[str, Any]:
    try:
        return _master_dict(master_store.accept_suggestion(project_id, suggestion_id, now=_now(), projects_root=_root())) or {}
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


@router.post("/projects/{project_id}/master/suggestions/{suggestion_id}/reject")
def reject_suggestion(project_id: str, suggestion_id: str) -> dict[str, Any]:
    try:
        return _suggestion_dict(master_store.reject_suggestion(project_id, suggestion_id, now=_now(), projects_root=_root()))
    except master_store.MasterDocumentError as error:
        raise _master_error(project_id, error) from error


def _llm_is_configured() -> bool:
    return not isinstance(llm._PROVIDER, llm.MockProvider)


def _replace_selection(body: str, selection: str, replacement: str) -> str:
    if body.count(selection) != 1:
        raise master_store.MasterDocumentError("selection must occur exactly once in the current master body")
    return body.replace(selection, replacement, 1)


def _suggestion_prompt(project_id: str, master: master_store.MasterDocument, action: str, selection: str | None) -> str:
    project = projects_api.project_store.load_project(project_id, projects_root=_root())
    try:
        board = research_store.load_research(project_id, projects_root=_root())
    except research_store.ResearchManifestError as error:
        raise master_store.MasterDocumentError(f"cannot read research: {error}") from error
    claims = "\n".join(f"- {item.kind}/{item.status}: {item.text}" for item in board.claims) or "- none"
    sources = "\n".join(f"- {item.title}: {item.reference}" for item in board.sources) or "- none"
    target = selection if selection is not None else master.body
    instruction = {"clarify": "rewrite it to be clearer without adding unsupported claims", "shorten": "compress it while preserving the author's point", "change_voice": f"rewrite it in this voice: {project.voice}", "add_counterpoint": "add a fair, explicit counterpoint while preserving the author's claim"}[action]
    return ("You are proposing an edit, not publishing a final answer. Return ONLY the replacement text, no preface.\n"
            f"Action: {instruction}\nAudience: {project.audience}\nGoal: {project.goal}\nVoice: {project.voice}\n"
            f"Research sources:\n{sources}\nResearch claims:\n{claims}\n\nText to revise:\n{target}")
