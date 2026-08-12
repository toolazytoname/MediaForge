"""Project-scoped MasterDocument and explicit AI proposal API."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pipeline import creator_article_generation
from pipeline import creator_material_parsing
from pipeline import article_feedback
from pipeline import local_annotations
from pipeline import article_image_revisions
from pipeline import visuals
from pipeline import master_documents as master_store
from pipeline import research as research_store
from pipeline.creators import image_gen, llm
from pipeline.webui import deps
from pipeline.webui.api import projects as projects_api


router = APIRouter(tags=["master-documents"])
_FEEDBACK_RUN_LOCKS: dict[str, threading.Lock] = {}
_FEEDBACK_RUN_LOCKS_GUARD = threading.Lock()


def _root():
    return projects_api._PROJECTS_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: int, code: str, error: Exception | str) -> HTTPException:
    if isinstance(error, dict):
        detail = {"code": code, **error}
        detail.setdefault("message", code)
        return HTTPException(status_code=status, detail={"error": detail})
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


def _feedback_dict(proposal: article_feedback.ArticleFeedbackProposal, master: master_store.MasterDocument) -> dict[str, Any]:
    return {"id": proposal.id, "project_id": proposal.project_id, "scope": proposal.scope,
            "feedback": proposal.feedback, "target": proposal.target, "readership": proposal.readership,
            "platform": proposal.platform, "values": proposal.values, "base_version": proposal.base_version,
            "base_hash": proposal.base_hash, "status": proposal.status,
            "state": article_feedback.proposal_state(proposal, master), "proposed_title": proposal.proposed_title,
            "proposed_body": proposal.proposed_body, "error": proposal.error, "created_at": proposal.created_at,
            "updated_at": proposal.updated_at, "decision": proposal.decision, "decided_at": proposal.decided_at,
            "accepted_title": proposal.accepted_title, "accepted_body": proposal.accepted_body}


def _annotation_dict(item: local_annotations.LocalAnnotation) -> dict[str, Any]:
    return {"id": item.id, "project_id": item.project_id, "kind": item.kind, "feedback": item.feedback,
            "categories": list(item.categories), "excerpt": item.excerpt, "context_before": item.context_before,
            "context_after": item.context_after, "structural_anchor": item.structural_anchor,
            "paragraph_anchor": item.paragraph_anchor, "asset_id": item.asset_id, "source_version": item.source_version,
            "source_hash": item.source_hash, "resolved_version": item.resolved_version, "resolved_hash": item.resolved_hash,
            "status": item.status, "orphan_reason": item.orphan_reason, "created_at": item.created_at,
            "updated_at": item.updated_at}


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


def _feedback_input(body: dict[str, Any]) -> dict[str, str | None]:
    allowed = {"feedback", "target", "readership", "platform", "values"}
    if not isinstance(body, dict) or "feedback" not in body or set(body) - allowed:
        raise article_feedback.ArticleFeedbackError("feedback body must contain feedback and optional target, readership, platform, values")
    feedback = body["feedback"]
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback.strip()) > 8_000:
        raise article_feedback.ArticleFeedbackError("feedback must be a non-empty string")
    result: dict[str, str | None] = {"feedback": feedback.strip()}
    for name in allowed - {"feedback"}:
        value = body.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value.strip()) > 2_000):
            raise article_feedback.ArticleFeedbackError(f"{name} must be non-empty text when provided")
        result[name] = value.strip() if isinstance(value, str) else None
    return result


def _feedback_accept_input(body: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(body, dict) or set(body) != {"title", "body"}:
        raise article_feedback.ArticleFeedbackError("feedback acceptance must contain only title and body")
    title, text = body["title"], body["body"]
    if not isinstance(title, str) or not title.strip() or not isinstance(text, str) or not text.strip():
        raise article_feedback.ArticleFeedbackError("feedback acceptance title and body must be non-empty text")
    if len(title.strip()) > 500 or len(text.strip()) > 200_000:
        raise article_feedback.ArticleFeedbackError("feedback acceptance is too large")
    return title.strip(), text.strip()


def _annotation_input(body: dict[str, Any], *, kind: str) -> tuple[str, str, tuple[str, ...]]:
    required = {"feedback", "categories", "excerpt"} if kind == "text" else {"feedback", "categories", "asset_id"}
    if not isinstance(body, dict) or set(body) != required:
        raise local_annotations.LocalAnnotationError("local annotation body has missing or unknown fields")
    feedback = body["feedback"]
    anchor = body["excerpt"] if kind == "text" else body["asset_id"]
    categories = body["categories"]
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback.strip()) > 8_000:
        raise local_annotations.LocalAnnotationError("feedback must be a non-empty string")
    if not isinstance(anchor, str) or not anchor.strip() or len(anchor.strip()) > 8_000:
        raise local_annotations.LocalAnnotationError("annotation target must be non-empty text")
    if not isinstance(categories, list) or any(not isinstance(item, str) for item in categories):
        raise local_annotations.LocalAnnotationError("categories must be an array of strings")
    return feedback.strip(), anchor.strip(), tuple(categories)


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


@router.post("/projects/{project_id}/article/images/replace")
def replace_article_image(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Apply one reviewed visual candidate without moving any other image."""
    if not isinstance(body, dict) or set(body) != {"current_asset_id", "candidate_asset_id"}:
        raise _error(400, "invalid_image_replacement", "replacement requires current_asset_id and candidate_asset_id")
    current, candidate = body["current_asset_id"], body["candidate_asset_id"]
    if not isinstance(current, str) or not isinstance(candidate, str):
        raise _error(400, "invalid_image_replacement", "image asset ids must be text")
    try:
        result = article_image_revisions.replace_image_reference(
            project_id, current_asset_id=current, candidate_asset_id=candidate,
            now=_now(), projects_root=_root(),
        )
        return {"master": _master_dict(result.master), "selected_asset_id": result.selected.id}
    except article_image_revisions.ArticleImageRevisionError as error:
        message = str(error)
        status = 409 if "stale or ambiguous" in message else 400
        raise _error(status, "image_replacement_stale" if status == 409 else "invalid_image_replacement", error) from error
    except (master_store.MasterDocumentError, visuals.VisualsError) as error:
        raise _master_error(project_id, error) from error


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


@router.get("/projects/{project_id}/article/annotations")
def get_local_annotations(project_id: str) -> dict[str, Any]:
    """Return local author instructions and refresh their safe target status."""
    try:
        return {"items": [_annotation_dict(item) for item in local_annotations.resolve_all_annotations(
            project_id, now=_now(), projects_root=_root())]}
    except local_annotations.LocalAnnotationError as error:
        return _raise_annotation_error(project_id, error)


@router.post("/projects/{project_id}/article/annotations/text", status_code=201)
def create_local_text_annotation(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        feedback, excerpt, categories = _annotation_input(body, kind="text")
        return _annotation_dict(local_annotations.create_text_annotation(
            project_id, excerpt=excerpt, feedback=feedback, categories=categories, now=_now(), projects_root=_root()))
    except local_annotations.LocalAnnotationError as error:
        return _raise_annotation_error(project_id, error)


@router.post("/projects/{project_id}/article/annotations/image", status_code=201)
def create_local_image_annotation(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        feedback, asset_id, categories = _annotation_input(body, kind="image")
        return _annotation_dict(local_annotations.create_image_annotation(
            project_id, asset_id=asset_id, feedback=feedback, categories=categories, now=_now(), projects_root=_root()))
    except local_annotations.LocalAnnotationError as error:
        return _raise_annotation_error(project_id, error)


@router.delete("/projects/{project_id}/article/annotations/{annotation_id}", status_code=204)
def remove_local_annotation(project_id: str, annotation_id: str) -> None:
    try:
        local_annotations.remove_annotation(project_id, annotation_id, projects_root=_root())
    except local_annotations.LocalAnnotationError as error:
        return _raise_annotation_error(project_id, error)


@router.get("/projects/{project_id}/article/feedback")
def get_article_feedback(project_id: str) -> dict[str, Any]:
    """List whole-article feedback proposals without exposing an accept path yet."""
    try:
        master = master_store.load_master(project_id, projects_root=_root())
        if master is None:
            raise master_store.MasterDocumentError(f"master not found: {project_id}")
        return {"items": [_feedback_dict(item, master) for item in article_feedback.recover_acceptances(project_id, projects_root=_root())]}
    except (article_feedback.ArticleFeedbackError, master_store.MasterDocumentError) as error:
        return _raise_feedback_error(project_id, error)


@router.post("/projects/{project_id}/article/feedback", status_code=201)
def request_article_feedback(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Generate a whole-article proposal after an explicit author click.

    A failure is deliberately recorded first. This gives the author a retryable
    record of their instruction instead of silently discarding it.
    """
    try:
        data = _feedback_input(body)
        master = master_store.load_master(project_id, projects_root=_root())
        if master is None:
            raise master_store.MasterDocumentError(f"master not found: {project_id}")
    except (article_feedback.ArticleFeedbackError, master_store.MasterDocumentError) as error:
        return _raise_feedback_error(project_id, error)
    if not _llm_is_configured():
        try:
            failed = article_feedback.create_failed_proposal(project_id, **data, error="AI provider is not configured", now=_now(), projects_root=_root())
        except article_feedback.ArticleFeedbackError as error:
            return _raise_feedback_error(project_id, error)
        raise _error(503, "llm_provider_unavailable", {"message": "AI provider is not configured; your feedback has been saved and can be retried.", "feedback_id": failed.id})
    run_lock = _feedback_run_lock(project_id)
    if not run_lock.acquire(blocking=False):
        raise _error(409, "feedback_generation_in_progress", "a whole-article feedback proposal is already being prepared")
    try:
        proposal = _run_feedback_proposal(project_id, master, data)
        return _feedback_dict(proposal, master)
    except Exception as error:
        try:
            failed = article_feedback.create_failed_proposal(project_id, **data, error=str(error), now=_now(), projects_root=_root())
        except article_feedback.ArticleFeedbackError as save_error:
            return _raise_feedback_error(project_id, save_error)
        raise _error(502, "llm_feedback_failed", {"message": f"AI feedback proposal failed: {error}", "feedback_id": failed.id}) from error
    finally:
        run_lock.release()


@router.post("/projects/{project_id}/article/feedback/{proposal_id}/retry")
def retry_article_feedback(project_id: str, proposal_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if body:
        raise _error(400, "invalid_feedback_retry", "feedback retry request must be empty")
    try:
        master = master_store.load_master(project_id, projects_root=_root())
        if master is None:
            raise master_store.MasterDocumentError(f"master not found: {project_id}")
        failed = next(item for item in article_feedback.load_proposals(project_id, projects_root=_root()) if item.id == proposal_id)
        if failed.status != "failed":
            raise article_feedback.ArticleFeedbackError("only a failed feedback proposal can be retried")
    except StopIteration:
        return _raise_feedback_error(project_id, article_feedback.ArticleFeedbackError(f"feedback proposal not found: {proposal_id}"))
    except (article_feedback.ArticleFeedbackError, master_store.MasterDocumentError) as error:
        return _raise_feedback_error(project_id, error)
    if not _llm_is_configured():
        raise _error(503, "llm_provider_unavailable", "AI provider is not configured; your feedback remains saved for retry")
    if article_feedback.proposal_state(failed, master) != "current":
        raise _error(409, "feedback_obsolete", "the article changed; create a new proposal from the current version")
    try:
        proposed = _call_feedback_llm(project_id, master, failed.feedback, failed.target, failed.readership, failed.platform, failed.values)
        ready = article_feedback.complete_failed_proposal(project_id, failed.id, proposed_title=proposed["title"], proposed_body=proposed["body"], now=_now(), projects_root=_root())
        return _feedback_dict(ready, master)
    except Exception as error:
        raise _error(502, "llm_feedback_failed", f"AI feedback proposal failed: {error}") from error


@router.post("/projects/{project_id}/article/feedback/{proposal_id}/accept")
def accept_article_feedback(project_id: str, proposal_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Explicitly accept a reviewed proposal; stale bases are a hard conflict."""
    try:
        title, text = _feedback_accept_input(body)
        proposal = article_feedback.accept_proposal(project_id, proposal_id, title=title, body=text,
                                                    now=_now(), projects_root=_root())
        master = master_store.load_master(project_id, projects_root=_root())
        assert master is not None
        return {"master": _master_dict(master), "proposal": _feedback_dict(proposal, master)}
    except article_feedback.ArticleFeedbackError as error:
        if "obsolete" in str(error):
            raise _error(409, "feedback_obsolete", error) from error
        return _raise_feedback_error(project_id, error)
    except master_store.MasterDocumentError as error:
        return _raise_feedback_error(project_id, error)


@router.post("/projects/{project_id}/article/feedback/{proposal_id}/reject")
def reject_article_feedback(project_id: str, proposal_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Reject a proposal without writing the master document."""
    if body:
        raise _error(400, "invalid_feedback_rejection", "feedback rejection request must be empty")
    try:
        proposal = article_feedback.reject_proposal(project_id, proposal_id, now=_now(), projects_root=_root())
        master = master_store.load_master(project_id, projects_root=_root())
        assert master is not None
        return _feedback_dict(proposal, master)
    except (article_feedback.ArticleFeedbackError, master_store.MasterDocumentError) as error:
        return _raise_feedback_error(project_id, error)


def _run_feedback_proposal(project_id: str, master: master_store.MasterDocument, data: dict[str, str | None]) -> article_feedback.ArticleFeedbackProposal:
    proposed = _call_feedback_llm(project_id, master, data["feedback"] or "", data["target"], data["readership"], data["platform"], data["values"])
    current = master_store.load_master(project_id, projects_root=_root())
    if current is None or current.version != master.version or current.body != master.body or current.title != master.title:
        raise article_feedback.ArticleFeedbackError("master changed while AI was preparing this proposal")
    return article_feedback.create_proposal(project_id, **data, proposed_title=proposed["title"], proposed_body=proposed["body"], now=_now(), projects_root=_root(), base_version=master.version, base_hash=article_feedback.master_hash(master))


def _call_feedback_llm(project_id: str, master: master_store.MasterDocument, feedback: str, target: str | None,
                       readership: str | None, platform: str | None, values: str | None) -> dict[str, str]:
    conn = deps.get_conn()
    try:
        return llm.complete_json(_whole_feedback_prompt(project_id, master, feedback, target, readership, platform, values),
            stage="article_feedback_proposal", ref_id=project_id, model_tier="creative", max_tokens=6000, conn=conn, parse=_parse_article)
    finally:
        conn.close()


def _whole_feedback_prompt(project_id: str, master: master_store.MasterDocument, feedback: str, target: str | None,
                           readership: str | None, platform: str | None, values: str | None) -> str:
    project = projects_api.project_store.load_project(project_id, projects_root=_root())
    return f"""你是中文资深编辑。作者对整篇文章提出了明确反馈；请生成一个只供审阅的修改提案，绝不发布，也不代表作者确认。\n
正式文章标题：{master.title}\n正式文章正文：\n{master.body}\n\n作者意见：{feedback}\n希望达到：{target or '未指定'}\n目标读者：{readership or project.audience}\n目标平台：{platform or '未指定'}\n必须遵守的价值取向：{values or '未指定'}\n项目声音：{project.voice}\n\n规则：\n1. 保留作者没有要求放弃的真实经历、限制和不确定性；不虚构事实、引语、数据或案例。\n2. 这不是正式文章，不能说已经修改、已经发布或已经获得作者同意。\n3. 只返回严格 JSON：{{\"title\":\"...\",\"body\":\"...\"}}，不要代码围栏或额外文字。"""


def _raise_feedback_error(project_id: str, error: Exception) -> None:
    message = str(error)
    if message == f"project not found: {project_id}":
        raise _error(404, "project_not_found", error)
    if message == f"master not found: {project_id}":
        raise _error(404, "master_not_found", error)
    if message.startswith("feedback proposal not found:"):
        raise _error(404, "feedback_not_found", error)
    if "manifest" in message or "invalid feedback JSON" in message:
        raise _error(500, "feedback_manifest_invalid", error)
    raise _error(400, "invalid_feedback_request", error)


def _raise_annotation_error(project_id: str, error: Exception) -> None:
    message = str(error)
    if message == f"project not found: {project_id}":
        raise _error(404, "project_not_found", error)
    if message == f"master not found: {project_id}" or message.startswith("visual asset not found:") or message.startswith("local annotation not found:"):
        raise _error(404, "annotation_record_not_found", error)
    if "manifest" in message or "annotations JSON" in message:
        raise _error(500, "local_annotations_manifest_invalid", error)
    raise _error(400, "invalid_local_annotation_request", error)


def _feedback_run_lock(project_id: str) -> threading.Lock:
    with _FEEDBACK_RUN_LOCKS_GUARD:
        return _FEEDBACK_RUN_LOCKS.setdefault(project_id, threading.Lock())


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
