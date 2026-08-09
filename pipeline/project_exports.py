"""Local, non-publishing export for an approved Project content package."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from pipeline import approvals, master_documents, projects, research, variants, visuals


class ProjectExportError(ValueError):
    """The package is incomplete, stale, or unsafe to export."""


@dataclass(frozen=True)
class ProjectExport:
    project_id: str
    file_name: str
    path: str


def create_export(
    project_id: str,
    *,
    projects_root: str | Path = projects.DEFAULT_PROJECTS_ROOT,
) -> ProjectExport:
    """Create an approval-versioned ZIP; this never touches Publication or a publisher."""
    try:
        project = projects.load_project(project_id, projects_root=projects_root)
        state = approvals.status(project_id, projects_root=projects_root)
        master = master_documents.load_master(project_id, projects_root=projects_root)
        board = research.load_research(project_id, projects_root=projects_root)
        variant_set = variants.load_variants(project_id, projects_root=projects_root)
        plan = visuals.load_visuals(project_id, projects_root=projects_root)
    except (projects.ProjectManifestError, approvals.ApprovalError,
            master_documents.MasterDocumentError, research.ResearchManifestError,
            variants.VariantsError, visuals.VisualsError) as error:
        raise ProjectExportError(str(error)) from error
    if not state.complete or state.stale or state.approval.snapshot is None:
        raise ProjectExportError("content package must have a current completed approval")
    if master is None:
        raise ProjectExportError("master is missing")
    by_platform = {item.platform: item for item in variant_set.variants}
    if set(by_platform) != {"wechat_mp", "toutiao"}:
        raise ProjectExportError("both platform variants are required")

    snapshot = state.approval.snapshot
    file_name = (
        f"content-package-m{snapshot.master_version}"
        f"-w{snapshot.variant_versions['wechat_mp']}"
        f"-t{snapshot.variant_versions['toutiao']}"
        f"-a{len(state.approval.history)}.zip"
    )
    relative = Path("exports") / file_name
    destination = Path(projects_root) / project.id / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    manifest: dict[str, Any] = {
        "project": asdict(project),
        "master": asdict(master),
        "research": asdict(board),
        "approval": asdict(state.approval),
        "variants": [asdict(by_platform[name]) for name in ("wechat_mp", "toutiao")],
        "visuals": asdict(plan),
        "notice": "本地安全导出；没有创建平台草稿，也没有执行真实发布。",
    }
    if destination.exists():
        _validate_existing(destination, manifest, set(snapshot.visual_asset_ids))
        return ProjectExport(project.id, file_name, relative.as_posix())
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for platform in ("wechat_mp", "toutiao"):
                archive.writestr(f"{platform}.md", render_variant_markdown(by_platform[platform], plan))
            _write_assets(archive, project.id, plan, set(snapshot.visual_asset_ids), projects_root)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return ProjectExport(project.id, file_name, relative.as_posix())


def render_variant_markdown(
    variant: variants.Variant,
    plan: visuals.VisualPlan,
    *,
    image_prefix: str = "assets/",
) -> str:
    assets = {item.id: item for item in plan.assets if item.status == "selected"}
    slots = {item.id: item for item in plan.slots}
    chosen = [assets[item] for item in variant.asset_ids if item in assets]
    covers = [item for item in chosen if "封面" in slots[item.slot_id].purpose]
    inserts = [item for item in chosen if item not in covers]
    paragraphs = [item for item in variant.body.split("\n\n") if item.strip()]
    result = [f"# {variant.title}", "", f"> {variant.summary}", ""]
    for item in covers:
        result.extend([_image_line(item, slots[item.slot_id], image_prefix), ""])
    pending = list(inserts)
    for index, paragraph in enumerate(paragraphs):
        result.extend([paragraph.strip(), ""])
        anchored = [
            item for item in pending
            if slots[item.slot_id].paragraph_anchor
            and slots[item.slot_id].paragraph_anchor in paragraph
        ]
        if not anchored and pending and index in {0, max(1, len(paragraphs) // 2)}:
            anchored = [pending[0]]
        for item in anchored:
            result.extend([_image_line(item, slots[item.slot_id], image_prefix), ""])
            pending.remove(item)
    for item in pending:
        result.extend([_image_line(item, slots[item.slot_id], image_prefix), ""])
    return "\n".join(result).rstrip() + "\n"


def _image_line(asset: visuals.VisualAsset, slot: visuals.VisualSlot, image_prefix: str) -> str:
    return f"![{slot.purpose}]({image_prefix}{asset.id}.png)"


def _write_assets(
    archive: ZipFile,
    project_id: str,
    plan: visuals.VisualPlan,
    selected_ids: set[str],
    projects_root: str | Path,
) -> None:
    for asset in plan.assets:
        if asset.id not in selected_ids or asset.file_path is None:
            continue
        source = Path(projects_root) / project_id / asset.file_path
        if not source.is_file():
            raise ProjectExportError(f"selected visual file is missing: {asset.id}")
        archive.write(source, f"assets/{asset.id}.png")


def _validate_existing(
    destination: Path,
    manifest: dict[str, Any],
    selected_ids: set[str],
) -> None:
    """Reuse only a byte-readable package for the exact same approval snapshot."""
    expected_names = {
        "manifest.json", "wechat_mp.md", "toutiao.md",
        *(f"assets/{asset_id}.png" for asset_id in selected_ids),
    }
    try:
        with ZipFile(destination) as archive:
            if set(archive.namelist()) != expected_names or archive.testzip() is not None:
                raise ProjectExportError("existing export is incomplete or corrupt; move it aside before retrying")
            stored = json.loads(archive.read("manifest.json"))
    except (BadZipFile, OSError, KeyError, json.JSONDecodeError) as error:
        raise ProjectExportError("existing export is incomplete or corrupt; move it aside before retrying") from error
    expected_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    stored_json = json.dumps(stored, ensure_ascii=False, sort_keys=True)
    if stored_json != expected_json:
        raise ProjectExportError("existing export belongs to a different approval snapshot; move it aside before retrying")


__all__ = ["ProjectExport", "ProjectExportError", "create_export", "render_variant_markdown"]
