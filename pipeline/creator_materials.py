"""Project-local creator materials, kept outside the frozen SQLite contract.

The records are intentionally small and immutable-at-rest: original input is
copied once, its digest is retained, and later parsing may add a separate
result without rewriting the source file.  They are *not* the future Wiki.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from pipeline.projects import DEFAULT_PROJECTS_ROOT
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
_DRAFT_DIRECTORY = ".creator-drafts"
_KINDS = frozenset({"image", "url", "pdf", "markdown", "text"})


class CreatorMaterialError(ValueError):
    """A creator material cannot be accepted safely."""


@dataclass(frozen=True)
class CreatorMaterial:
    id: str
    kind: str
    source: str
    original_name: str | None
    sha256: str
    created_at: str
    status: str
    error: str | None
    stored_path: str | None


def add_file_material(
    draft_id: str, filename: str, payload: bytes, content_type: str | None,
    *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> CreatorMaterial:
    """Validate and store one uploaded file without trusting its filename."""
    draft = _draft_dir(projects_root, draft_id)
    name = _safe_filename(filename)
    if not isinstance(payload, bytes) or not payload:
        raise CreatorMaterialError("file must not be empty")
    if len(payload) > MAX_FILE_BYTES:
        raise CreatorMaterialError("file is too large (maximum 2 MiB)")
    kind = _file_kind(name, content_type)
    _validate_file(kind, payload)
    digest = _digest(payload)
    existing = _find_duplicate(_load(draft), kind, digest)
    if existing is not None:
        return existing
    destination = draft / "files" / f"{digest[:16]}-{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    material = _new_material(
        kind, source=name, original_name=name, digest=digest, status="ready",
        stored_path=str(destination.relative_to(draft)),
    )
    _append(draft, material)
    return material


def add_text_material(
    draft_id: str, value: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> CreatorMaterial:
    """Store pasted notes as a directly usable project-local source."""
    if not isinstance(value, str) or not value.strip():
        raise CreatorMaterialError("text must be a non-empty string")
    if len(value) > MAX_TEXT_CHARS:
        raise CreatorMaterialError("text is too large (maximum 100000 characters)")
    draft = _draft_dir(projects_root, draft_id)
    normalized = value.strip()
    digest = _digest(normalized.encode())
    existing = _find_duplicate(_load(draft), "text", digest)
    if existing is not None:
        return existing
    material = _new_material("text", source="pasted text", original_name=None, digest=digest, status="ready")
    text_path = draft / "texts" / f"{material.id}.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(normalized, encoding="utf-8")
    material = CreatorMaterial(**{**asdict(material), "stored_path": str(text_path.relative_to(draft))})
    _append(draft, material)
    return material


def add_url_material(
    draft_id: str, value: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> CreatorMaterial:
    """Record a URL; fetching and SSRF checks deliberately belong to PF-03."""
    draft = _draft_dir(projects_root, draft_id)
    normalized = value.strip() if isinstance(value, str) else ""
    parsed = urlparse(normalized)
    digest = _digest(normalized.encode())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        material = _new_material("url", source=normalized or "invalid URL", original_name=None, digest=digest,
                                 status="failed", error="URL must start with http:// or https://")
    else:
        existing = _find_duplicate(_load(draft), "url", digest)
        if existing is not None:
            return existing
        material = _new_material("url", source=normalized, original_name=None, digest=digest,
                                 status="needs_confirmation")
    _append(draft, material)
    return material


def list_draft_materials(
    draft_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> tuple[CreatorMaterial, ...]:
    return tuple(_load(_draft_dir(projects_root, draft_id)))


def remove_draft_material(
    draft_id: str, material_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> None:
    draft = _draft_dir(projects_root, draft_id)
    items = list(_load(draft))
    material = next((item for item in items if item.id == material_id), None)
    if material is None:
        raise CreatorMaterialError(f"unknown material: {material_id}")
    if material.stored_path:
        path = _safe_relative(draft, material.stored_path)
        if path.exists():
            path.unlink()
    _write(draft, [item for item in items if item.id != material_id])


def attach_draft_materials(
    draft_id: str, project_id: str, material_ids: Iterable[str], *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> tuple[CreatorMaterial, ...]:
    """Atomically associate chosen draft records with the newly created Project."""
    draft = _draft_dir(projects_root, draft_id)
    project = _project_dir(projects_root, project_id)
    chosen = selected_draft_materials(draft_id, material_ids, projects_root=projects_root)
    destination = project / "materials"
    copied: list[CreatorMaterial] = []
    for item in chosen:
        if item.stored_path:
            source = _safe_relative(draft, item.stored_path)
            target = destination / source.relative_to(draft)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(CreatorMaterial(**{**asdict(item), "stored_path": str(target.relative_to(destination))}))
        else:
            copied.append(item)
    _write(destination, copied)
    return tuple(copied)


def selected_draft_materials(
    draft_id: str, material_ids: Iterable[str], *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> tuple[CreatorMaterial, ...]:
    """Validate a requested attachment set before a new project is created."""
    draft = _draft_dir(projects_root, draft_id)
    requested = tuple(material_ids)
    if len(set(requested)) != len(requested):
        raise CreatorMaterialError("material_ids cannot contain duplicates")
    all_items = {item.id: item for item in _load(draft)}
    chosen: list[CreatorMaterial] = []
    for material_id in requested:
        item = all_items.get(material_id)
        if item is None:
            raise CreatorMaterialError(f"unknown material: {material_id}")
        chosen.append(item)
    return tuple(chosen)


def list_project_materials(
    project_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> tuple[CreatorMaterial, ...]:
    return tuple(_load(_project_dir(projects_root, project_id) / "materials"))


def _new_material(kind: str, *, source: str, original_name: str | None, digest: str, status: str,
                  error: str | None = None, stored_path: str | None = None) -> CreatorMaterial:
    return CreatorMaterial(new_id("mat"), kind, source, original_name, digest,
                           datetime.now(timezone.utc).isoformat(), status, error, stored_path)


def _draft_dir(root: str | Path, draft_id: str) -> Path:
    if not valid_sidecar_id(draft_id, "draft_"):
        raise CreatorMaterialError(f"invalid draft id: {draft_id!r}")
    return Path(root) / _DRAFT_DIRECTORY / draft_id / "materials"


def _project_dir(root: str | Path, project_id: str) -> Path:
    if not valid_sidecar_id(project_id, "prj_"):
        raise CreatorMaterialError(f"invalid project id: {project_id!r}")
    return Path(root) / project_id


def _load(directory: Path) -> list[CreatorMaterial]:
    manifest = directory / "materials.json"
    if not manifest.exists():
        return []
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CreatorMaterialError("invalid creator materials manifest") from exc
    if not isinstance(payload, list):
        raise CreatorMaterialError("creator materials manifest must be a list")
    return [_material_from_payload(item) for item in payload]


def _material_from_payload(payload: Any) -> CreatorMaterial:
    if not isinstance(payload, dict):
        raise CreatorMaterialError("creator material record must be an object")
    fields = set(CreatorMaterial.__dataclass_fields__)
    if set(payload) != fields:
        raise CreatorMaterialError("creator material record has missing or unknown fields")
    item = CreatorMaterial(**payload)
    if not valid_sidecar_id(item.id, "mat_") or item.kind not in _KINDS:
        raise CreatorMaterialError("invalid creator material record")
    if item.status not in {"pending", "reading", "ready", "needs_confirmation", "failed"}:
        raise CreatorMaterialError("invalid creator material status")
    if not isinstance(item.source, str) or not item.source or len(item.sha256) != 64:
        raise CreatorMaterialError("invalid creator material record")
    try:
        datetime.fromisoformat(item.created_at)
    except (TypeError, ValueError) as exc:
        raise CreatorMaterialError("invalid creator material timestamp") from exc
    if item.stored_path is not None:
        relative = Path(item.stored_path)
        if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
            raise CreatorMaterialError("unsafe material path")
    return item


def _append(directory: Path, material: CreatorMaterial) -> None:
    _write(directory, [*_load(directory), material])


def _write(directory: Path, materials: Iterable[CreatorMaterial]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "materials.json"
    temporary = manifest.with_suffix(".json.tmp")
    temporary.write_text(json.dumps([asdict(item) for item in materials], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)


def _safe_filename(value: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or value in {".", ".."}:
        raise CreatorMaterialError("invalid filename")
    return value


def _safe_relative(root: Path, value: str) -> Path:
    path = root / value
    if Path(value).is_absolute() or ".." in Path(value).parts or not path.resolve().is_relative_to(root.resolve()):
        raise CreatorMaterialError("unsafe material path")
    return path


def _file_kind(name: str, content_type: str | None) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"} and (content_type or "").startswith("image/"):
        return "image"
    if suffix == ".pdf" and content_type == "application/pdf":
        return "pdf"
    if suffix in {".md", ".markdown"} and content_type in {"text/markdown", "text/plain", "application/octet-stream"}:
        return "markdown"
    raise CreatorMaterialError("unsupported file type")


def _validate_file(kind: str, payload: bytes) -> None:
    if kind == "pdf" and not payload.startswith(b"%PDF-"):
        raise CreatorMaterialError("invalid PDF")
    if kind == "markdown" and not payload.decode("utf-8", errors="ignore").strip():
        raise CreatorMaterialError("empty Markdown")
    if kind == "image" and not payload.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF", b"<svg", b"<?xml")):
        raise CreatorMaterialError("invalid image")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _find_duplicate(items: Iterable[CreatorMaterial], kind: str, digest: str) -> CreatorMaterial | None:
    return next((item for item in items if item.kind == kind and item.sha256 == digest), None)
