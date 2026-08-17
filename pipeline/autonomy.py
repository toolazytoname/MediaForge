"""Per-stage autonomy policy (RFC §5.7 / LAZY-2 §5.2).

UI labels follow §5.2: assist is 手工 / 零 LLM, not “我写，AI 协助”.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from pipeline import approvals, master_documents, projects as project_store, research


ALLOWED_AUTONOMY = frozenset({"assist", "collaborate", "draft", "pack"})
DELIVERY_MODES = frozenset({"preview", "export", "draft", "direct"})


class AutonomyError(ValueError):
    """A Project action is forbidden by the current autonomy policy."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "autonomy_forbids_llm",
        http_status: int = 400,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class AutonomyPolicy:
    key: str
    label: str
    help: str
    llm_allowed: bool
    image_gen_allowed: bool
    persist_ai_draft: bool
    persist_ai_adapt: bool
    pack_prepare: bool
    delivery_modes: tuple[str, ...]
    llm_budget_usd: float
    images_per_slot: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_POLICIES: dict[str, AutonomyPolicy] = {
    "assist": AutonomyPolicy(
        key="assist",
        label="手工",
        help="零 LLM：只手录、手导 PNG、手点导出或草稿。生图和起草按钮不会调用模型。",
        llm_allowed=False,
        image_gen_allowed=False,
        persist_ai_draft=False,
        persist_ai_adapt=False,
        pack_prepare=False,
        delivery_modes=("preview", "export", "draft"),
        llm_budget_usd=0.0,
        images_per_slot=0,
    ),
    "collaborate": AutonomyPolicy(
        key="collaborate",
        label="协作",
        help="显式点击才生成建议或预览；接受前不落盘主稿或平台适配。",
        llm_allowed=True,
        image_gen_allowed=True,
        persist_ai_draft=False,
        persist_ai_adapt=False,
        pack_prepare=False,
        delivery_modes=("preview", "export", "draft"),
        llm_budget_usd=2.0,
        images_per_slot=3,
    ),
    "draft": AutonomyPolicy(
        key="draft",
        label="AI 起草",
        help="可自动提出主稿建议和未锁定平台草稿；交付仍要人点，且必须先审批。",
        llm_allowed=True,
        image_gen_allowed=True,
        persist_ai_draft=False,
        persist_ai_adapt=True,
        pack_prepare=False,
        delivery_modes=("preview", "export", "draft"),
        llm_budget_usd=5.0,
        images_per_slot=4,
    ),
    "pack": AutonomyPolicy(
        key="pack",
        label="自动内容包",
        help="可批量准备到待审批；不得草稿或直发，后台只许预览和导出准备。",
        llm_allowed=True,
        image_gen_allowed=True,
        persist_ai_draft=True,
        persist_ai_adapt=True,
        pack_prepare=True,
        delivery_modes=("preview", "export"),
        llm_budget_usd=8.0,
        images_per_slot=4,
    ),
}


def get_policy(autonomy: str) -> AutonomyPolicy:
    if autonomy not in _POLICIES:
        raise AutonomyError(f"invalid autonomy: {autonomy!r}", code="invalid_autonomy")
    return _POLICIES[autonomy]


def all_policies() -> tuple[AutonomyPolicy, ...]:
    return tuple(_POLICIES[key] for key in ("assist", "collaborate", "draft", "pack"))


def load_policy(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> tuple[project_store.Project, AutonomyPolicy]:
    try:
        project = project_store.load_project(project_id, projects_root=projects_root)
    except project_store.ProjectManifestError as error:
        raise AutonomyError(str(error), code="project_not_found", http_status=404) from error
    return project, get_policy(project.autonomy)


def require_llm(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> tuple[project_store.Project, AutonomyPolicy]:
    project, policy = load_policy(project_id, projects_root=projects_root)
    if not policy.llm_allowed:
        raise AutonomyError(
            f"{policy.label}模式下禁止调用语言模型",
            code="autonomy_forbids_llm",
        )
    return project, policy


def require_image_gen(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> tuple[project_store.Project, AutonomyPolicy]:
    project, policy = load_policy(project_id, projects_root=projects_root)
    if not policy.image_gen_allowed:
        raise AutonomyError(
            f"{policy.label}模式下禁止自动生图",
            code="autonomy_forbids_llm",
        )
    return project, policy


def require_delivery_mode(
    project_id: str,
    mode: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> tuple[project_store.Project, AutonomyPolicy]:
    if mode not in DELIVERY_MODES:
        raise AutonomyError(f"unknown delivery mode: {mode}", code="invalid_delivery_mode")
    project, policy = load_policy(project_id, projects_root=projects_root)
    if mode not in policy.delivery_modes:
        raise AutonomyError(
            f"{policy.label}模式下禁止 {mode}",
            code="autonomy_forbids_delivery",
            http_status=403,
        )
    return project, policy


def research_is_ready(board: research.ResearchBoard) -> bool:
    if len(board.sources) < 3:
        return False
    if not any(item.kind == "judgment" for item in board.claims):
        return False
    if any(
        item.kind == "fact" and (item.status == "unverified" or not item.source_ids)
        for item in board.claims
    ):
        return False
    if any(item.kind == "open_question" and item.status == "open" for item in board.claims):
        return False
    return True


def master_is_ready(master: master_documents.MasterDocument | None) -> bool:
    return bool(master and master.body.strip() and len(master.body.strip()) >= 800)


def next_cta(
    *,
    research_ready: bool,
    master_ready: bool,
    approval_complete: bool,
    autonomy: str,
) -> dict[str, str]:
    if not research_ready:
        return {"key": "research", "label": "继续研究"}
    if not master_ready:
        return {"key": "master", "label": "写主稿"}
    if not approval_complete:
        return {"key": "approval", "label": "去审批"}
    if autonomy == "pack":
        return {"key": "export", "label": "去导出"}
    return {"key": "deliver", "label": "去导出或草稿"}


def next_action(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> dict[str, Any]:
    project, policy = load_policy(project_id, projects_root=projects_root)
    board = research.load_research(project_id, projects_root=projects_root)
    master = master_documents.load_master(project_id, projects_root=projects_root)
    approval = approvals.status(project_id, projects_root=projects_root)
    from pipeline.deliverables import KIND_ARTICLE, KIND_GALLERY, load_deliverables
    try:
        bundle = load_deliverables(project_id, projects_root=projects_root)
    except Exception:
        bundle = None
    galleries = () if bundle is None else tuple(item for item in bundle.items if item.kind == KIND_GALLERY)
    articles = () if bundle is None else tuple(item for item in bundle.items if item.kind == KIND_ARTICLE)
    if galleries and not articles:
        if any(not item.locked for item in galleries):
            cta = {"key": "gallery", "label": "去组图"}
        elif not approval.complete:
            cta = {"key": "approval", "label": "去审批"}
        else:
            cta = {"key": "export", "label": "去导出"}
    else:
        cta = next_cta(
            research_ready=research_is_ready(board),
            master_ready=master_is_ready(master),
            approval_complete=approval.complete,
            autonomy=project.autonomy,
        )
    return {
        "project_id": project.id,
        "autonomy": project.autonomy,
        "policy": policy.to_dict(),
        "research_ready": research_is_ready(board),
        "master_ready": master_is_ready(master),
        "approval_complete": approval.complete,
        "cta": cta,
    }


def highest_status(statuses: Iterable[str]) -> str:
    order = ("drafting", "ready_for_approval", "approved", "superseded")
    rank = {name: index for index, name in enumerate(order)}
    highest = "drafting"
    for status in statuses:
        if status not in rank:
            raise AutonomyError(f"unknown deliverable status: {status}", code="invalid_deliverable_status")
        if rank[status] > rank[highest]:
            highest = status
    return highest
