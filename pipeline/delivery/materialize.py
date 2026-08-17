"""Materialize a Project article into the WeChat adapter directory contract."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from pipeline import master_documents, projects as project_store, visuals
from pipeline.deliverables import ArticlePayload, Deliverable, article_payload
from pipeline.publishers.wechat_mp import DIGEST_MAX_LEN
from pipeline.utils.ids import new_id

_INLINE_PREFIX = "../images/inline-"


@dataclass(frozen=True)
class MaterializedArticle:
    content_id: str
    canonical_path: Path
    materialize_dir: Path
    title: str
    digest: str


def project_content_hash(project_id: str, deliverable_id: str) -> str:
    return hashlib.sha256(f"project:{project_id}:{deliverable_id}".encode("utf-8")).hexdigest()


def materialize_wechat_article(
    project_id: str,
    deliverable: Deliverable,
    *,
    content_id: str | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> MaterializedArticle:
    root = Path(projects_root)
    project = project_store.load_project(project_id, projects_root=root)
    master = master_documents.load_master(project_id, projects_root=root)
    if master is None:
        raise ValueError("master is missing")
    plan = visuals.load_visuals(project_id, projects_root=root)
    payload = article_payload(deliverable)
    assigned_id = content_id or new_id("c")
    dest = root / project.id / "legacy" / assigned_id
    dest.mkdir(parents=True, exist_ok=True)
    wechat_dir = dest / "wechat_mp"
    wechat_dir.mkdir(exist_ok=True)

    cover_asset, inline_assets = _split_assets(deliverable, plan)
    if cover_asset is None or cover_asset.file_path is None:
        raise ValueError("wechat draft requires a selected cover image")
    cover_src = root / project.id / cover_asset.file_path
    if not cover_src.is_file():
        raise ValueError(f"cover file missing: {cover_asset.id}")
    shutil.copy2(cover_src, dest / "cover.png")
    _copy_inline_images(project.id, inline_assets, dest, projects_root=root)

    article_md = _wechat_body(payload, inline_assets, plan)
    digest = payload.summary.strip().replace("\n", " ")[:DIGEST_MAX_LEN]
    (wechat_dir / "article.md").write_text(article_md, encoding="utf-8")
    (wechat_dir / "meta.json").write_text(
        json.dumps({"title": deliverable.title, "digest": digest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (dest / "canonical.md").write_text(f"# {master.title}\n\n{master.body}\n", encoding="utf-8")
    return MaterializedArticle(assigned_id, dest / "canonical.md", dest, deliverable.title, digest)


def _split_assets(deliverable: Deliverable, plan: visuals.VisualPlan):
    assets = {item.id: item for item in plan.assets if item.status == "selected"}
    slots = {item.id: item for item in plan.slots}
    chosen = [assets[item] for item in deliverable.asset_ids if item in assets]
    covers = [item for item in chosen if item.slot_id in slots and "封面" in slots[item.slot_id].purpose]
    inserts = [item for item in chosen if item not in covers]
    cover = covers[0] if covers else (chosen[0] if chosen else None)
    return cover, inserts


def _copy_inline_images(
    project_id: str,
    inserts: list[visuals.VisualAsset],
    dest: Path,
    *,
    projects_root: Path,
) -> None:
    images_dir = dest / "images"
    images_dir.mkdir(exist_ok=True)
    for index, asset in enumerate(inserts, start=1):
        if asset.file_path is None:
            continue
        source = projects_root / project_id / asset.file_path
        if source.is_file():
            shutil.copy2(source, images_dir / f"inline-{index}.png")


def _wechat_body(
    payload: ArticlePayload,
    inserts: list[visuals.VisualAsset],
    plan: visuals.VisualPlan,
) -> str:
    slots = {item.id: item for item in plan.slots}
    paragraphs = [item for item in payload.body.split("\n\n") if item.strip()] or [payload.body.strip()]
    result: list[str] = []
    pending = list(inserts)
    for index, paragraph in enumerate(paragraphs):
        result.extend([paragraph.strip(), ""])
        if pending and index in {0, max(1, len(paragraphs) // 2)}:
            asset = pending.pop(0)
            n = len(inserts) - len(pending)
            purpose = slots[asset.slot_id].purpose if asset.slot_id in slots else "插图"
            result.extend([f"![{purpose}]({_INLINE_PREFIX}{n}.png)", ""])
    for offset, asset in enumerate(pending, start=len(inserts) - len(pending) + 1):
        purpose = slots[asset.slot_id].purpose if asset.slot_id in slots else "插图"
        result.extend([f"![{purpose}]({_INLINE_PREFIX}{offset}.png)", ""])
    return "\n".join(result).rstrip() + "\n"
