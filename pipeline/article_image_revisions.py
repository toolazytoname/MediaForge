"""Explicit, article-local image candidate application.

Visual candidates deliberately live independently of the article.  This
module is the only bridge that can select a candidate and rewrite one image
reference in the current master document.  It never deletes the prior file or
candidate, so restoring an earlier image is just another explicit selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline import master_documents, visuals


class ArticleImageRevisionError(ValueError):
    """A candidate cannot safely be applied to the current article."""


@dataclass(frozen=True)
class ArticleImageRevision:
    master: master_documents.MasterDocument
    selected: visuals.VisualAsset


def replace_image_reference(
    project_id: str,
    *,
    current_asset_id: str,
    candidate_asset_id: str,
    now: str,
    projects_root: str | Path,
) -> ArticleImageRevision:
    """Select one same-slot image and replace exactly its live Markdown URL.

    The visible reference is the stale guard: if the article no longer embeds
    ``current_asset_id`` we reject rather than guessing which picture the user
    meant.  The link is replaced before the visual selection is recorded, so a
    failed stale check can never silently change the selected image.
    """
    plan = visuals.load_visuals(project_id, projects_root=projects_root)
    current = _asset(plan, current_asset_id, "current")
    candidate = _asset(plan, candidate_asset_id, "candidate")
    if current.slot_id != candidate.slot_id:
        raise ArticleImageRevisionError("candidate must belong to the same image location")
    if not current.file_path or not candidate.file_path:
        raise ArticleImageRevisionError("image asset has no usable file")
    if candidate.status == "failed":
        raise ArticleImageRevisionError("failed image candidate cannot be selected")

    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        raise ArticleImageRevisionError(f"master not found: {project_id}")
    before = _reference(project_id, current.file_path)
    after = _reference(project_id, candidate.file_path)
    occurrences = master.body.count(before)
    if occurrences != 1:
        raise ArticleImageRevisionError("current image reference is stale or ambiguous; reopen the image and compare again")
    body = master.body.replace(before, after, 1)
    # Preserve the actual Markdown alt text and position.  A replacement is a
    # new immutable article version, not a mutation of its historical text.
    updated = master_documents.save_image_replacement(
        project_id, current_asset_id=current.id, candidate_asset_id=candidate.id,
        title=master.title, body=body, now=now, projects_root=projects_root,
    )
    selected = visuals.select_asset(
        project_id, candidate.id, reason=f"文章内显式换图：{current.id}", rating=None,
        projects_root=projects_root,
    )
    return ArticleImageRevision(updated, selected)


def _asset(plan: visuals.VisualPlan, asset_id: str, role: str) -> visuals.VisualAsset:
    try:
        return next(item for item in plan.assets if item.id == asset_id)
    except StopIteration as error:
        raise ArticleImageRevisionError(f"{role} visual asset not found: {asset_id}") from error


def _reference(project_id: str, file_path: str) -> str:
    # The asset manifest validates this relative path; avoid accepting a URL
    # supplied by the request as a replacement target.
    return f"/output/projects/{project_id}/{file_path}"
