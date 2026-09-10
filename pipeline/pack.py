"""Pack-mode content-package preparation (RFC §5.7).

Terminal state is at most ready_for_approval. This path never calls
safe_publish(dry_run=False) and never locks, approves, drafts, or directs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline import deliverables, master_documents, projects as project_store
from pipeline.auto_create import AutoCreateResult, run_auto_create
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
    revision_count: int = 0

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
    draft_fn: Callable[..., tuple[str, str]] | None = None,
    score_fn: Callable[[str, str], tuple[str, float]] | None = None,
    visual_fn: Callable[[str], None] | None = None,
) -> PackPrepareResult:
    """Fill Master + unlocked article candidates. Stop before approval/delivery."""
    project, policy = load_policy(project_id, projects_root=projects_root)
    if not policy.pack_prepare:
        raise AutonomyError(
            f"{policy.label}模式不能批量准备内容包",
            code="autonomy_pack_only",
            http_status=400,
        )
    created_master = False
    created_platforms: tuple[str, ...] = ()
    revision_count = 0
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        created: AutoCreateResult = run_auto_create(
            project_id, now=now, projects_root=projects_root,
            draft_fn=draft_fn, score_fn=score_fn, visual_fn=visual_fn,
        )
        master = master_documents.load_master(project_id, projects_root=projects_root)
        created_master = created.created_master
        created_platforms = created.created_platforms
        revision_count = created.revision_count
    assert master is not None

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
        created_platforms=created_platforms,
        revision_count=revision_count,
    )


# Imported by tests that assert the policy table stays aligned.
assert get_policy("pack").pack_prepare
