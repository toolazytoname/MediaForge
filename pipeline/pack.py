"""Pack-mode content-package preparation (RFC §5.7).

Terminal state is at most ready_for_approval. This path never calls
safe_publish(dry_run=False) and never locks, approves, drafts, or directs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pipeline import deliverables, master_documents, projects as project_store, variants
from pipeline.autonomy import AutonomyError, get_policy, highest_status, load_policy


_ALLOWED_TERMINAL = frozenset({"drafting", "ready_for_approval"})


@dataclass(frozen=True)
class PackPrepareResult:
    project_id: str
    master_version: int
    deliverable_statuses: tuple[str, ...]
    terminal_status: str
    created_master: bool
    created_platforms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["deliverable_statuses"] = list(self.deliverable_statuses)
        payload["created_platforms"] = list(self.created_platforms)
        return payload


def prepare_pack(
    project_id: str,
    *,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> PackPrepareResult:
    """Pack-only wrapper. Draft/pack scheduled prepare uses prepare_candidates."""
    _project, policy = load_policy(project_id, projects_root=projects_root)
    if not policy.pack_prepare:
        raise AutonomyError(
            f"{policy.label}模式不能批量准备内容包",
            code="autonomy_pack_only",
            http_status=400,
        )
    return prepare_candidates(project_id, now=now, projects_root=projects_root)


def prepare_candidates(
    project_id: str,
    *,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> PackPrepareResult:
    """Fill Master + unlocked article candidates. Stop before approval/delivery."""
    project, policy = load_policy(project_id, projects_root=projects_root)
    if not (policy.pack_prepare or policy.persist_ai_adapt):
        raise AutonomyError(
            f"{policy.label}模式不能批量准备内容包",
            code="autonomy_pack_only",
            http_status=400,
        )
    created_master = False
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        master = master_documents.save_manual(
            project_id,
            title=project.title,
            body=_candidate_body(project),
            now=now,
            projects_root=projects_root,
        )
        created_master = True

    existing = {
        item.platform
        for item in variants.load_variants(project_id, projects_root=projects_root).variants
    }
    created_platforms: list[str] = []
    for platform in ("wechat_mp", "toutiao"):
        if platform in existing:
            continue
        variants.create_from_master(
            project_id, platform, now=now, projects_root=projects_root,
        )
        created_platforms.append(platform)

    items = deliverables.load_deliverables(project_id, projects_root=projects_root).items
    statuses = tuple(item.status for item in items) or ("drafting",)
    terminal = highest_status(statuses)
    if terminal not in _ALLOWED_TERMINAL:
        raise AutonomyError(
            f"pack prepare must stop at ready_for_approval, got {terminal}",
            code="pack_exceeded_approval",
            http_status=409,
        )
    if any(item.locked for item in items):
        # Human may have locked already; pack itself never locks, but must not
        # promote those items into approved/delivery.
        pass
    return PackPrepareResult(
        project_id=project.id,
        master_version=master.version,
        deliverable_statuses=statuses,
        terminal_status=terminal,
        created_master=created_master,
        created_platforms=tuple(created_platforms),
    )


def _candidate_body(project: project_store.Project) -> str:
    seed = project.idea.strip() or project.title.strip()
    block = (
        f"{seed}\n\n"
        "这是自动内容包生成的主稿候选，尚未审批，也不是平台草稿或直发。\n"
        "作者需要核对事实边界、锁定平台版本，并亲自完成审批。"
    )
    parts = [block]
    while sum(len(part) for part in parts) < 800:
        parts.append(block)
    return "\n\n".join(parts)


# Imported by tests that assert the policy table stays aligned.
assert get_policy("pack").pack_prepare
