"""Real auto-create pipeline for pack/ops. Never loops the theme as filler."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import master_documents, research, variants
from pipeline.autonomy import AutonomyError, load_policy
from pipeline.interviews import InterviewError, ProjectInterview, require_confirmed_interview
from pipeline.projects import DEFAULT_PROJECTS_ROOT, Project, list_projects
from pipeline.research import ResearchBoard


DraftFn = Callable[[Project, ProjectInterview, ResearchBoard, str | None], tuple[str, str]]
ScoreFn = Callable[[str, str], tuple[str, float]]
VisualFn = Callable[[str], None]


class AutoCreateError(AutonomyError):
    """Auto-create cannot proceed or quality gate paused the run."""

    def __init__(self, message: str, *, code: str = "auto_create_failed"):
        super().__init__(message, code=code, http_status=409)


@dataclass(frozen=True)
class AutoCreateResult:
    project_id: str
    title: str
    body: str
    revision_count: int
    gate_verdict: str
    created_master: bool
    created_platforms: tuple[str, ...]
    paused: bool


def run_auto_create(
    project_id: str,
    *,
    now: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    draft_fn: DraftFn | None = None,
    score_fn: ScoreFn | None = None,
    visual_fn: VisualFn | None = None,
    max_revisions: int = 2,
    account_id: str | None = None,
    accounts_root: str | Path | None = None,
) -> AutoCreateResult:
    if max_revisions > 2:
        max_revisions = 2
    project, _policy = load_policy(project_id, projects_root=projects_root)
    try:
        interview = require_confirmed_interview(project_id, projects_root=projects_root)
    except InterviewError as error:
        raise AutoCreateError(str(error), code="interview_required") from error
    board = research.load_research(project_id, projects_root=projects_root)
    if not board.sources:
        raise AutoCreateError("自动写稿需要至少一条来源", code="sources_required")

    created_master = False
    revisions = 0
    scorer = score_fn or (lambda title, body: score_manuscript(
        title, body, sources=board.sources, interview=interview,
    )[:2])
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        _assert_not_duplicate_theme(project, projects_root=projects_root)
        writer = draft_fn or _default_draft
        title, body = writer(project, interview, board, None)
        verdict, _score = scorer(title, body)
        while verdict != "pass" and revisions < max_revisions:
            revisions += 1
            title, body = writer(project, interview, board, f"质量不合格：{verdict}")
            verdict, _score = scorer(title, body)
        if verdict != "pass":
            if account_id and accounts_root is not None:
                from pipeline.account_authorization import record_quality_result
                record_quality_result(
                    account_id, project_id=project_id, score=float(_score),
                    verdict=verdict, now=now, accounts_root=accounts_root,
                )
            raise AutoCreateError("质量门禁不合格，已暂停，不进入交付", code="quality_paused")
        if _is_theme_loop(project, body):
            raise AutoCreateError("自动写稿不得用主题循环粘贴凑字", code="placeholder_body")
        master = master_documents.save_manual(
            project_id, title=title, body=body, now=now, projects_root=projects_root,
        )
        created_master = True
    # Check persisted edits before spending on images; never rewrite them on resume.
    _check_saved_quality(project_id, scorer=scorer, now=now, projects_root=projects_root,
                         account_id=account_id, accounts_root=accounts_root)
    visuals_fn = visual_fn or (
        lambda pid: _default_visuals(
            pid, now=now, projects_root=projects_root,
            account_id=account_id, accounts_root=accounts_root,
        )
    )
    visuals_fn(project_id)
    master = master_documents.load_master(project_id, projects_root=projects_root)
    assert master is not None

    created_platforms: list[str] = []
    existing = {
        item.platform
        for item in variants.load_variants(project_id, projects_root=projects_root).variants
    }
    for platform in ("wechat_mp", "toutiao"):
        if platform in existing:
            continue
        adapted_title, summary, adapted_body = _adapt_for_platform(master, platform)
        variants.create_adapted(
            project_id, platform, title=adapted_title, summary=summary,
            body=adapted_body, now=now, projects_root=projects_root,
        )
        created_platforms.append(platform)
    verdict, _score = _check_saved_quality(
        project_id, scorer=scorer, now=now, projects_root=projects_root,
        account_id=account_id, accounts_root=accounts_root,
    )
    return AutoCreateResult(
        project_id=project.id,
        title=master.title,
        body=master.body,
        revision_count=revisions,
        gate_verdict=verdict,
        created_master=created_master,
        created_platforms=tuple(created_platforms),
        paused=False,
    )


def _check_saved_quality(
    project_id: str, *, scorer: ScoreFn, now: str, projects_root: str | Path,
    account_id: str | None, accounts_root: str | Path | None,
) -> tuple[str, float]:
    from pipeline.account_authorization import (
        AuthorizationError, load_authorization, quality_fingerprint, record_quality_result,
    )
    auth_version = 0
    if account_id and accounts_root is not None:
        try:
            auth_version = load_authorization(account_id, accounts_root=accounts_root).version
        except AuthorizationError:
            # A quality check is useful before authorization, but cannot authorize delivery.
            auth_version = 0
    fingerprint = quality_fingerprint(project_id, projects_root=projects_root)
    master = master_documents.load_master(project_id, projects_root=projects_root)
    assert master is not None
    documents = (master, *variants.load_variants(project_id, projects_root=projects_root).variants)
    results = [scorer(item.title, item.body) for item in documents]
    verdict = next((value for value, _ in results if value != "pass"), "pass")
    score = min(float(value) for _, value in results)
    if fingerprint != quality_fingerprint(project_id, projects_root=projects_root):
        raise AutoCreateError("质量检查期间正文已修改，请重新检查", code="quality_stale")
    if account_id and accounts_root is not None:
        record_quality_result(
            account_id, project_id=project_id, score=score, verdict=verdict, now=now,
            accounts_root=accounts_root, content_fingerprint=fingerprint,
            master_version=master.version, authorization_version=auth_version,
        )
    if verdict != "pass":
        raise AutoCreateError("质量门禁不合格，正文已保留，请修改后重试", code="quality_paused")
    return verdict, score


def build_draft_prompt(project: Project, interview: ProjectInterview, board: ResearchBoard) -> str:
    unread = "、".join(interview.unread_books) if interview.unread_books else "无"
    sources = "\n".join(
        f"[{item.id}] {item.title} | {item.reference} | {item.summary}"
        for item in board.sources
    )
    claims = "\n".join(
        f"- {item.kind}/{item.status}: {item.text}" for item in board.claims
    ) or "（无声明）"
    return (
        "你是中文资深编辑。根据已确认访谈和来源写一篇可审阅主稿，不要发布。\n"
        f"项目想法：{project.idea}\n目标读者：{project.audience}\n发布目的：{project.goal}\n"
        f"作者观点：{interview.viewpoint}\n动机：{interview.motive}\n经历：{interview.experience}\n"
        f"未读原书（不得假装读过）：{unread}\n来源：\n{sources}\n声明：\n{claims}\n"
        "只返回 JSON {\"title\":\"...\",\"body\":\"...\"}。"
    )


def _default_draft(
    project: Project, interview: ProjectInterview, board: ResearchBoard, critique: str | None,
) -> tuple[str, str]:
    prompt = build_draft_prompt(project, interview, board)
    if critique:
        prompt += f"\n请修订：{critique}"
    try:
        from pipeline.creators import llm
        from pipeline.webui import deps
        conn = deps.get_conn()
        try:
            payload = llm.complete_json(
                prompt, stage="project_master_draft", ref_id=project.id,
                model_tier="creative", max_tokens=6000, conn=conn,
                parse=_parse_article,
            )
        finally:
            conn.close()
    except Exception as error:
        raise AutoCreateError(f"AI draft unavailable: {error}", code="draft_unavailable") from error
    return payload["title"], payload["body"]


def _parse_article(text: str) -> dict[str, str]:
    import json
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    payload = json.loads(cleaned)
    if set(payload) != {"title", "body"}:
        raise ValueError("draft must contain only title and body")
    return {key: str(payload[key]).strip() for key in ("title", "body")}


def score_manuscript(
    title: str,
    body: str,
    *,
    sources: tuple[Any, ...] = (),
    interview: ProjectInterview | None = None,
) -> tuple[str, float, tuple[str, ...]]:
    reasons: list[str] = []
    text = body.strip()
    if not title.strip() or len(text) < 800:
        reasons.append("too_short")
    words = text.split()
    unique_ratio = len(set(words)) / max(len(words), 1)
    if unique_ratio < 0.28:
        reasons.append("low_diversity")
    from collections import Counter
    chunks = [text[index:index + 20] for index in range(0, min(len(text), 2000), 20)]
    common = Counter(chunk for chunk in chunks if chunk.strip()).most_common(1)
    if common and common[0][1] >= 5:
        reasons.append("repetition")
    cliches = ("赋能", "抓手", "底层逻辑", "打造新格局", "这是自动内容包生成的主稿候选")
    if any(item in text for item in cliches):
        reasons.append("cliche")
    if sources:
        grounded = any(
            (getattr(item, "title", "") and getattr(item, "title")[:4] in text)
            or (getattr(item, "summary", "") and str(getattr(item, "summary"))[:6] in text)
            for item in sources
        )
        if not grounded:
            reasons.append("ungrounded")
    if interview is not None and interview.viewpoint.strip():
        token = interview.viewpoint.strip()[:6]
        if token not in text and interview.viewpoint.strip() not in text:
            reasons.append("no_viewpoint")
    if reasons:
        return "fail", 3.0, tuple(reasons)
    return "pass", 8.0, ()


def _default_score(title: str, body: str) -> tuple[str, float]:
    verdict, score, _reasons = score_manuscript(title, body)
    return verdict, score


def _adapt_for_platform(master: Any, platform: str) -> tuple[str, str, str]:
    summary = master.body.strip().replace("\n", " ")[:80]
    if platform == "toutiao":
        title = master.title.strip()[:30]
        body = master.body.replace("## ", "**").replace("# ", "")
        return title, summary, body
    title = master.title.strip()[:64]
    body = master.body
    return title, summary, body


def _image_cost_usd(model: str) -> float:
    from pipeline.creators import llm as llm_mod
    prices = llm_mod.MODEL_PRICES.get(model, {}) or {}
    cost = float(prices.get("per_image_usd") or 0)
    if cost <= 0:
        raise AutoCreateError("image generation is unpriced", code="unpriced_image")
    return cost


def _slots_missing_selected(plan) -> tuple:
    selected = {
        item.slot_id for item in plan.assets
        if item.status == "selected" and item.file_path
    }
    return tuple(slot for slot in plan.slots if slot.id not in selected)


def _default_visuals(
    project_id: str, *, now: str, projects_root: str | Path,
    account_id: str | None = None, accounts_root: str | Path | None = None,
) -> None:
    from pipeline import visuals
    from pipeline.creators.image_gen import generate_image
    from pipeline.utils.ids import new_id
    from pipeline.webui import deps
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    if not plan.slots:
        visuals.save_plan(
            project_id,
            bible={"style": "克制写实"},
            slots=[
                {"id": "vsl_cover", "purpose": "封面", "paragraph_anchor": None, "direction": "封面", "aspect_ratio": "16:9"},
                {"id": "vsl_one", "purpose": "正文插图一", "paragraph_anchor": "正文", "direction": "插图", "aspect_ratio": "16:9"},
                {"id": "vsl_two", "purpose": "正文插图二", "paragraph_anchor": "正文", "direction": "插图", "aspect_ratio": "16:9"},
            ],
            projects_root=projects_root,
        )
        plan = visuals.load_visuals(project_id, projects_root=projects_root)
    missing = _slots_missing_selected(plan)
    if not missing:
        return
    conn = deps.get_conn()
    try:
        for slot in missing:
            asset_id = new_id("vas")
            path = visuals.asset_path(project_id, asset_id, projects_root=projects_root)
            generated = generate_image(
                slot.direction or slot.purpose,
                out_path=path, aspect_ratio=slot.aspect_ratio,
                stage="create_image", ref_id=project_id, conn=conn,
            )
            cost = _image_cost_usd(generated.model)
            if account_id and accounts_root is not None:
                from pipeline.account_plans import load_spend, record_spend
                from pipeline.account_profiles import load_profile
                profile = load_profile(account_id, accounts_root=accounts_root)
                spent = load_spend(account_id, accounts_root=accounts_root)
                if spent + cost > profile.budget_usd:
                    raise AutoCreateError("account image budget exceeded", code="budget_exceeded")
                record_spend(account_id, amount=cost, now=now, accounts_root=accounts_root)
            asset = visuals.record_asset(
                project_id, slot_id=slot.id, prompt=slot.direction or slot.purpose,
                model=generated.model, size=slot.aspect_ratio, cost_usd=cost, now=now,
                file_path=f"assets/{asset_id}.png", status="candidate",
                asset_id=asset_id, projects_root=projects_root,
            )
            visuals.select_asset(project_id, asset.id, reason="自动选中", rating=3, projects_root=projects_root)
    except AutoCreateError:
        raise
    except Exception as error:
        raise AutoCreateError(f"自动配图失败，已暂停: {error}", code="visuals_required") from error
    finally:
        conn.close()
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    if _slots_missing_selected(plan):
        raise AutoCreateError("自动配图尚未完成，已暂停", code="visuals_required")


def _is_theme_loop(project: Project, body: str) -> bool:
    seed = (project.idea.strip() or project.title.strip())
    if not seed:
        return False
    return body.count(seed) >= 4


def _assert_not_duplicate_theme(project: Project, *, projects_root: str | Path) -> None:
    idea = project.idea.strip()
    if not idea:
        return
    for other in list_projects(projects_root=projects_root):
        if other.id == project.id:
            continue
        if other.idea.strip() != idea:
            continue
        if master_documents.load_master(other.id, projects_root=projects_root) is None:
            continue
        raise AutoCreateError(
            f"theme already used by {other.id}", code="theme_duplicate",
        )


__all__ = [
    "AutoCreateError",
    "AutoCreateResult",
    "build_draft_prompt",
    "run_auto_create",
    "score_manuscript",
]
