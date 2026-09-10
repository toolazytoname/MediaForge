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
    _assert_not_duplicate_theme(project, projects_root=projects_root)

    writer = draft_fn or _default_draft
    scorer = score_fn or _default_score
    title, body = writer(project, interview, board, None)
    verdict, _score = scorer(title, body)
    revisions = 0
    while verdict != "pass" and revisions < max_revisions:
        revisions += 1
        title, body = writer(project, interview, board, f"质量不合格：{verdict}")
        verdict, _score = scorer(title, body)
    if verdict != "pass":
        raise AutoCreateError("质量门禁不合格，已暂停，不进入交付", code="quality_paused")
    if _is_theme_loop(project, body):
        raise AutoCreateError("自动写稿不得用主题循环粘贴凑字", code="placeholder_body")

    created_master = False
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        master_documents.save_manual(
            project_id, title=title, body=body, now=now, projects_root=projects_root,
        )
        created_master = True
    if visual_fn is not None:
        visual_fn(project_id)

    created_platforms: list[str] = []
    existing = {
        item.platform
        for item in variants.load_variants(project_id, projects_root=projects_root).variants
    }
    for platform in ("wechat_mp", "toutiao"):
        if platform in existing:
            continue
        variants.create_from_master(
            project_id, platform, now=now, projects_root=projects_root,
        )
        created_platforms.append(platform)
    return AutoCreateResult(
        project_id=project.id,
        title=title,
        body=body,
        revision_count=revisions,
        gate_verdict=verdict,
        created_master=created_master,
        created_platforms=tuple(created_platforms),
        paused=False,
    )


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


def _default_score(title: str, body: str) -> tuple[str, float]:
    if len(body.strip()) < 800 or not title.strip():
        return "fail", 3.0
    return "pass", 8.0


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
        if other.idea.strip() == idea:
            raise AutoCreateError(
                f"theme already used by {other.id}", code="theme_duplicate",
            )


__all__ = [
    "AutoCreateError",
    "AutoCreateResult",
    "build_draft_prompt",
    "run_auto_create",
]
