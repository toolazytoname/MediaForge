"""Project Deliverable sidecar with variants.json dual-write (RFC §5.2)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import projects as project_store, variants as variant_store, visuals as visual_store
from pipeline.publishers.capability_registry import (
    KIND_GALLERY,
    gallery_image_limits_for,
    platforms_for,
)
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "deliverables.json"
_HISTORY_LIMIT = 20
_SCHEMA_VERSION = 1
KIND_ARTICLE = "article"
KIND_VIDEO = "video"
SEED_IDS = {
    "wechat_mp": "dlv_article_wechat_mp",
    "toutiao": "dlv_article_toutiao",
}
SEED_PLATFORMS = {value: key for key, value in SEED_IDS.items()}


class DeliverablesError(ValueError):
    """A deliverables sidecar is missing, malformed, or unsafe to change."""


@dataclass(frozen=True)
class ArticlePayload:
    summary: str
    body: str
    locale: str = "zh-CN"


@dataclass(frozen=True)
class GalleryCrop:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class GallerySlide:
    asset_id: str
    order: int
    alt: str
    crop: GalleryCrop | None = None


@dataclass(frozen=True)
class GalleryPayload:
    caption: str
    tags: tuple[str, ...]
    cover_asset_id: str
    slides: tuple[GallerySlide, ...]


@dataclass(frozen=True)
class DeliverableSnapshot:
    version: int
    title: str
    kind: str
    targets: tuple[str, ...]
    payload: dict[str, Any]
    asset_ids: tuple[str, ...]
    saved_at: str
    reason: str


@dataclass(frozen=True)
class Deliverable:
    id: str
    kind: str
    title: str
    version: int
    status: str
    source_master_version: int | None
    locked: bool
    manually_modified: bool
    upstream_updated: bool
    targets: tuple[str, ...]
    payload: dict[str, Any]
    asset_ids: tuple[str, ...]
    created_at: str
    updated_at: str
    history: tuple[DeliverableSnapshot, ...]


@dataclass(frozen=True)
class DeliverableSet:
    project_id: str
    schema_version: int
    items: tuple[Deliverable, ...]


def seed_id_for(platform: str) -> str:
    try:
        return SEED_IDS[platform]
    except KeyError as exc:
        raise DeliverablesError(f"no seed deliverable for platform {platform!r}") from exc


def platform_for_seed(deliverable_id: str) -> str | None:
    return SEED_PLATFORMS.get(deliverable_id)


def load_deliverables(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> DeliverableSet:
    _ensure(project_id, projects_root)
    path = _path(projects_root, project_id)
    if path.exists():
        return _from_file(path, project_id)
    return project_from_variants(project_id, projects_root=projects_root)


def get_deliverable(
    project_id: str,
    deliverable_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    item = next(
        (item for item in load_deliverables(project_id, projects_root=projects_root).items if item.id == deliverable_id),
        None,
    )
    if item is None:
        raise DeliverablesError(f"deliverable not found: {deliverable_id}")
    return item


def project_from_variants(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> DeliverableSet:
    try:
        variant_set = variant_store.load_variants(project_id, projects_root=projects_root)
    except variant_store.VariantsError as exc:
        raise DeliverablesError(f"cannot read variants: {exc}") from exc
    items = tuple(_from_variant(item) for item in variant_set.variants)
    return DeliverableSet(_project_id(project_id), _SCHEMA_VERSION, items)


def sync_from_variant_set(
    variant_set: variant_store.VariantSet,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> DeliverableSet:
    """Write-through projection used by variants._write. Does not call variants."""
    projected = tuple(_from_variant(item) for item in variant_set.variants)
    current_path = _path(projects_root, variant_set.project_id)
    extras: list[Deliverable] = []
    if current_path.exists():
        existing = _from_file(current_path, variant_set.project_id)
        seed_ids = {SEED_IDS[item.platform] for item in variant_set.variants}
        extras = [item for item in existing.items if item.id not in seed_ids]
    result = DeliverableSet(variant_set.project_id, _SCHEMA_VERSION, (*projected, *extras))
    _write(projects_root, result)
    return result


def article_payload(item: Deliverable) -> ArticlePayload:
    if item.kind != KIND_ARTICLE:
        raise DeliverablesError("deliverable is not an article")
    payload = item.payload
    return ArticlePayload(
        _text("summary", payload.get("summary")),
        _text("body", payload.get("body")),
        str(payload.get("locale") or "zh-CN"),
    )


def gallery_payload(item: Deliverable | dict[str, Any]) -> GalleryPayload:
    payload = item.payload if isinstance(item, Deliverable) else item
    if isinstance(item, Deliverable) and item.kind != KIND_GALLERY:
        raise DeliverablesError("deliverable is not a gallery")
    if not isinstance(payload, dict):
        raise DeliverablesError("gallery payload must be an object")
    slides_raw = payload.get("slides")
    if not isinstance(slides_raw, list) or not slides_raw:
        raise DeliverablesError("gallery slides must be a non-empty array")
    slides = tuple(_gallery_slide(value) for value in slides_raw)
    orders = [slide.order for slide in slides]
    if orders != list(range(len(slides))):
        raise DeliverablesError("gallery slide order must be contiguous from 0")
    asset_ids = [slide.asset_id for slide in slides]
    if len(set(asset_ids)) != len(asset_ids):
        raise DeliverablesError("gallery slide assets must be unique")
    cover = payload.get("cover_asset_id")
    if not valid_sidecar_id(cover, "vas_"):
        raise DeliverablesError("cover_asset_id must be a visual asset id")
    if cover not in asset_ids:
        raise DeliverablesError("cover_asset_id must be one of the slides")
    tags_raw = payload.get("tags", [])
    if tags_raw is None:
        tags_raw = []
    if not isinstance(tags_raw, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags_raw):
        raise DeliverablesError("tags must be a string array")
    return GalleryPayload(
        _text("caption", payload.get("caption")),
        tuple(tag.strip() for tag in tags_raw),
        cover,
        slides,
    )


def create_gallery(
    project_id: str,
    *,
    title: str,
    caption: str,
    slides: list[dict[str, Any]],
    cover_asset_id: str,
    now: str,
    tags: list[str] | None = None,
    targets: list[str] | None = None,
    locked: bool = False,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    _ensure(project_id, projects_root)
    timestamp = _timestamp(now)
    target_tuple = _gallery_targets(targets)
    payload = gallery_payload({
        "caption": caption,
        "tags": tags or [],
        "cover_asset_id": cover_asset_id,
        "slides": slides,
    })
    _assert_gallery_limits(payload, target_tuple)
    _assert_gallery_selected(project_id, payload, projects_root)
    item = Deliverable(
        new_id("dlv"), KIND_GALLERY, _text("title", title), 1,
        "ready_for_approval" if locked else "drafting", None, locked, True, False,
        target_tuple, _gallery_dict(payload), tuple(slide.asset_id for slide in payload.slides),
        timestamp, timestamp, (),
    )
    bundle = load_deliverables(project_id, projects_root=projects_root)
    _write(projects_root, DeliverableSet(bundle.project_id, _SCHEMA_VERSION, (*bundle.items, item)))
    return item


def update_gallery(
    project_id: str,
    deliverable_id: str,
    *,
    now: str,
    title: str | None = None,
    caption: str | None = None,
    tags: list[str] | None = None,
    cover_asset_id: str | None = None,
    slides: list[dict[str, Any]] | None = None,
    targets: list[str] | None = None,
    reason: str = "edit",
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    current = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    if current.kind != KIND_GALLERY:
        raise DeliverablesError("deliverable is not a gallery")
    if current.locked:
        raise DeliverablesError("locked gallery cannot be edited")
    existing = gallery_payload(current)
    payload = gallery_payload({
        "caption": existing.caption if caption is None else caption,
        "tags": list(existing.tags) if tags is None else tags,
        "cover_asset_id": existing.cover_asset_id if cover_asset_id is None else cover_asset_id,
        "slides": slides if slides is not None else [_slide_dict(slide) for slide in existing.slides],
    })
    target_tuple = current.targets if targets is None else _gallery_targets(targets)
    _assert_gallery_limits(payload, target_tuple)
    _assert_gallery_selected(project_id, payload, projects_root)
    timestamp = _timestamp(now)
    snapshot = DeliverableSnapshot(
        current.version, current.title, current.kind, current.targets,
        current.payload, current.asset_ids, timestamp, reason,
    )
    updated = replace(
        current,
        title=_text("title", title) if title is not None else current.title,
        version=current.version + 1,
        status="drafting",
        manually_modified=True,
        targets=target_tuple,
        payload=_gallery_dict(payload),
        asset_ids=tuple(slide.asset_id for slide in payload.slides),
        updated_at=timestamp,
        history=(snapshot, *current.history)[:_HISTORY_LIMIT],
    )
    _replace_item(project_id, updated, projects_root)
    return updated


def set_gallery_locked(
    project_id: str,
    deliverable_id: str,
    *,
    locked: bool,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    if not isinstance(locked, bool):
        raise DeliverablesError("locked must be boolean")
    current = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    if current.kind != KIND_GALLERY:
        raise DeliverablesError("deliverable is not a gallery")
    if current.locked == locked:
        return current
    payload = gallery_payload(current)
    if locked:
        _assert_gallery_limits(payload, current.targets)
        _assert_gallery_selected(project_id, payload, projects_root)
    updated = replace(
        current,
        locked=locked,
        status="ready_for_approval" if locked else "drafting",
        updated_at=_timestamp(now),
    )
    _replace_item(project_id, updated, projects_root)
    return updated


def list_galleries(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> tuple[Deliverable, ...]:
    return tuple(
        item for item in load_deliverables(project_id, projects_root=projects_root).items
        if item.kind == KIND_GALLERY
    )


@dataclass(frozen=True)
class VideoPayload:
    script: str
    duration_s: int
    aspect: str
    engine: str | None
    storyboard: tuple[dict[str, Any], ...]
    render_job_id: str | None
    output_path: str | None
    subtitle_path: str | None
    audio_track_path: str | None


def video_payload(item: Deliverable | dict[str, Any]) -> VideoPayload:
    payload = item.payload if isinstance(item, Deliverable) else item
    if isinstance(item, Deliverable) and item.kind != KIND_VIDEO:
        raise DeliverablesError("deliverable is not a video")
    if not isinstance(payload, dict):
        raise DeliverablesError("video payload must be an object")
    duration = payload.get("duration_s")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
        raise DeliverablesError("duration_s must be a positive integer")
    aspect = payload.get("aspect")
    if aspect not in {"9:16", "16:9"}:
        raise DeliverablesError("aspect must be 9:16 or 16:9")
    engine = payload.get("engine")
    if engine is not None and engine not in {"mpt", "pixelle", "digitalhuman", "fake"}:
        raise DeliverablesError("engine must be mpt, pixelle, digitalhuman, fake, or null")
    storyboard = payload.get("storyboard") or []
    if not isinstance(storyboard, list):
        raise DeliverablesError("storyboard must be an array")
    render_job_id = payload.get("render_job_id")
    if render_job_id is not None and not valid_sidecar_id(render_job_id, "job_"):
        raise DeliverablesError("render_job_id must be a job_ id")
    return VideoPayload(
        _text("script", payload.get("script")),
        duration,
        aspect,
        engine,
        tuple(storyboard),
        render_job_id,
        _rel_path("output_path", payload.get("output_path")),
        _rel_path("subtitle_path", payload.get("subtitle_path")),
        _rel_path("audio_track_path", payload.get("audio_track_path")),
    )


def create_video(
    project_id: str,
    *,
    title: str,
    script: str,
    duration_s: int,
    aspect: str,
    now: str,
    engine: str | None = None,
    targets: list[str] | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    _ensure(project_id, projects_root)
    timestamp = _timestamp(now)
    payload = video_payload({
        "script": script,
        "duration_s": duration_s,
        "aspect": aspect,
        "engine": engine,
        "storyboard": [],
        "render_job_id": None,
        "output_path": None,
        "subtitle_path": None,
        "audio_track_path": None,
    })
    item = Deliverable(
        new_id("dlv"), KIND_VIDEO, _text("title", title), 1,
        "drafting", None, False, True, False,
        tuple(targets) if targets else ("local",),
        _video_dict(payload), (),
        timestamp, timestamp, (),
    )
    bundle = load_deliverables(project_id, projects_root=projects_root)
    _write(projects_root, DeliverableSet(bundle.project_id, _SCHEMA_VERSION, (*bundle.items, item)))
    return item


def attach_video_job(
    project_id: str,
    deliverable_id: str,
    *,
    job_id: str,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Deliverable:
    current = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    if current.kind != KIND_VIDEO:
        raise DeliverablesError("deliverable is not a video")
    if not valid_sidecar_id(job_id, "job_"):
        raise DeliverablesError("render_job_id must be a job_ id")
    existing = video_payload(current)
    if existing.render_job_id == job_id:
        return current
    payload = _video_dict(existing)
    payload["render_job_id"] = job_id
    timestamp = _timestamp(now)
    snapshot = DeliverableSnapshot(
        current.version, current.title, current.kind, current.targets,
        current.payload, current.asset_ids, timestamp, "attach_render_job",
    )
    updated = replace(
        current,
        version=current.version + 1,
        payload=payload,
        updated_at=timestamp,
        history=(snapshot, *current.history)[:_HISTORY_LIMIT],
    )
    _replace_item(project_id, updated, projects_root)
    return updated


def _video_dict(payload: VideoPayload) -> dict[str, Any]:
    return {
        "script": payload.script,
        "duration_s": payload.duration_s,
        "aspect": payload.aspect,
        "engine": payload.engine,
        "storyboard": list(payload.storyboard),
        "render_job_id": payload.render_job_id,
        "output_path": payload.output_path,
        "subtitle_path": payload.subtitle_path,
        "audio_track_path": payload.audio_track_path,
    }


def _rel_path(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DeliverablesError(f"{name} must be a relative path or null")
    path = value.replace("\\", "/").strip()
    if path.startswith("/") or path.startswith("..") or "/../" in f"/{path}/":
        raise DeliverablesError(f"{name} must be a relative path")
    return path


def _from_variant(variant: variant_store.Variant) -> Deliverable:
    deliverable_id = seed_id_for(variant.platform)
    payload = {"summary": variant.summary, "body": variant.body, "locale": "zh-CN"}
    history = tuple(
        DeliverableSnapshot(
            item.version, item.title, KIND_ARTICLE, (variant.platform,),
            {"summary": item.summary, "body": item.body, "locale": "zh-CN"},
            item.asset_ids, item.saved_at, item.reason,
        )
        for item in variant.history
    )
    status = "ready_for_approval" if variant.locked and not variant.upstream_updated else "drafting"
    return Deliverable(
        deliverable_id, KIND_ARTICLE, variant.title, variant.version, status,
        variant.source_master_version, variant.locked, variant.manually_modified,
        variant.upstream_updated, (variant.platform,), payload, variant.asset_ids,
        variant.created_at, variant.updated_at, history,
    )


def _from_file(path: Path, project_id: str) -> DeliverableSet:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeliverablesError("invalid deliverables JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"project_id", "schema_version", "items"}:
        raise DeliverablesError("deliverables manifest has missing or unknown fields")
    if _project_id(raw["project_id"]) != project_id:
        raise DeliverablesError("deliverables project id does not match its directory")
    if raw["schema_version"] != _SCHEMA_VERSION:
        raise DeliverablesError("unsupported deliverables schema_version")
    if not isinstance(raw["items"], list):
        raise DeliverablesError("deliverables items must be an array")
    items = tuple(_item(value) for value in raw["items"])
    if len({item.id for item in items}) != len(items):
        raise DeliverablesError("duplicate deliverable id")
    return DeliverableSet(project_id, _SCHEMA_VERSION, items)


def _item(value: Any) -> Deliverable:
    if not isinstance(value, dict) or set(value) != set(Deliverable.__dataclass_fields__):
        raise DeliverablesError("deliverable has missing or unknown fields")
    if not valid_sidecar_id(value["id"], "dlv_"):
        raise DeliverablesError("invalid deliverable id")
    if value["kind"] not in {KIND_ARTICLE, KIND_GALLERY, KIND_VIDEO}:
        raise DeliverablesError("invalid deliverable kind")
    if value["kind"] == KIND_GALLERY:
        gallery_payload(value["payload"])
    if value["kind"] == KIND_VIDEO:
        video_payload(value["payload"])
    if value["status"] not in {"drafting", "ready_for_approval", "approved", "superseded"}:
        raise DeliverablesError("invalid deliverable status")
    targets = value["targets"]
    if not isinstance(targets, list) or not targets or any(not isinstance(item, str) or not item for item in targets):
        raise DeliverablesError("targets must be a non-empty string array")
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise DeliverablesError("payload must be an object")
    history_raw = value["history"]
    if not isinstance(history_raw, list):
        raise DeliverablesError("deliverable history must be an array")
    source_master = value["source_master_version"]
    if source_master is not None:
        source_master = _positive("source_master_version", source_master)
    return Deliverable(
        value["id"], value["kind"], _text("title", value["title"]),
        _positive("version", value["version"]), value["status"], source_master,
        _boolean("locked", value["locked"]), _boolean("manually_modified", value["manually_modified"]),
        _boolean("upstream_updated", value["upstream_updated"]), tuple(targets), payload,
        _asset_ids(value["asset_ids"]), _timestamp(value["created_at"]), _timestamp(value["updated_at"]),
        tuple(_snapshot(item) for item in history_raw),
    )


def _snapshot(value: Any) -> DeliverableSnapshot:
    if not isinstance(value, dict) or set(value) != set(DeliverableSnapshot.__dataclass_fields__):
        raise DeliverablesError("deliverable snapshot has missing or unknown fields")
    targets = value["targets"]
    payload = value["payload"]
    if not isinstance(targets, list) or not isinstance(payload, dict):
        raise DeliverablesError("invalid deliverable snapshot")
    return DeliverableSnapshot(
        _positive("version", value["version"]), _text("title", value["title"]),
        value["kind"], tuple(targets), payload, _asset_ids(value["asset_ids"]),
        _timestamp(value["saved_at"]), _text("reason", value["reason"]),
    )


def _gallery_slide(value: Any) -> GallerySlide:
    if not isinstance(value, dict):
        raise DeliverablesError("gallery slide must be an object")
    allowed = {"asset_id", "order", "alt", "crop"}
    if set(value) - allowed:
        raise DeliverablesError("gallery slide has unknown fields")
    if not valid_sidecar_id(value.get("asset_id"), "vas_"):
        raise DeliverablesError("slide asset_id must be a visual asset id")
    order = value.get("order")
    if not isinstance(order, int) or isinstance(order, bool) or order < 0:
        raise DeliverablesError("slide order must be a non-negative integer")
    crop_raw = value.get("crop")
    crop = None if crop_raw is None else _gallery_crop(crop_raw)
    return GallerySlide(value["asset_id"], order, _text("alt", value.get("alt")), crop)


def _gallery_crop(value: Any) -> GalleryCrop:
    if not isinstance(value, dict) or set(value) != {"x", "y", "w", "h"}:
        raise DeliverablesError("crop must be {x,y,w,h}")
    numbers = {}
    for key in ("x", "y", "w", "h"):
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise DeliverablesError("crop values must be numbers")
        numbers[key] = float(raw)
    if numbers["w"] <= 0 or numbers["h"] <= 0:
        raise DeliverablesError("crop width and height must be positive")
    if numbers["x"] < 0 or numbers["y"] < 0:
        raise DeliverablesError("crop origin must be >= 0")
    if numbers["x"] + numbers["w"] > 1.0001 or numbers["y"] + numbers["h"] > 1.0001:
        raise DeliverablesError("crop box must stay within the unit square")
    return GalleryCrop(numbers["x"], numbers["y"], numbers["w"], numbers["h"])


def _gallery_targets(targets: list[str] | None) -> tuple[str, ...]:
    allowed = set(platforms_for(kind=KIND_GALLERY))
    chosen = tuple(targets) if targets else tuple(allowed)
    if not chosen:
        raise DeliverablesError("no gallery-capable platform is registered")
    if any(not isinstance(item, str) or not item for item in chosen):
        raise DeliverablesError("targets must be a non-empty string array")
    unknown = [item for item in chosen if item not in allowed]
    if unknown:
        raise DeliverablesError(f"targets do not support gallery: {', '.join(unknown)}")
    return chosen


def _assert_gallery_limits(payload: GalleryPayload, targets: tuple[str, ...]) -> None:
    minimum, maximum = gallery_image_limits_for(targets)
    count = len(payload.slides)
    if count < minimum or count > maximum:
        raise DeliverablesError(
            f"gallery needs {minimum}..{maximum} images for {', '.join(targets)}; got {count}"
        )


def _assert_gallery_selected(
    project_id: str,
    payload: GalleryPayload,
    root: str | Path,
) -> None:
    try:
        plan = visual_store.load_visuals(project_id, projects_root=root)
    except visual_store.VisualsError as exc:
        raise DeliverablesError(f"cannot read visuals: {exc}") from exc
    selected = {item.id for item in plan.assets if item.status == "selected"}
    needed = {payload.cover_asset_id, *(slide.asset_id for slide in payload.slides)}
    missing = sorted(needed - selected)
    if missing:
        raise DeliverablesError("gallery assets must be currently selected: " + ", ".join(missing))


def _gallery_dict(payload: GalleryPayload) -> dict[str, Any]:
    return {
        "caption": payload.caption,
        "tags": list(payload.tags),
        "cover_asset_id": payload.cover_asset_id,
        "slides": [_slide_dict(slide) for slide in payload.slides],
    }


def _slide_dict(slide: GallerySlide) -> dict[str, Any]:
    row: dict[str, Any] = {"asset_id": slide.asset_id, "order": slide.order, "alt": slide.alt}
    if slide.crop is not None:
        row["crop"] = {"x": slide.crop.x, "y": slide.crop.y, "w": slide.crop.w, "h": slide.crop.h}
    return row


def _replace_item(project_id: str, item: Deliverable, root: str | Path) -> None:
    bundle = load_deliverables(project_id, projects_root=root)
    items = tuple(item if existing.id == item.id else existing for existing in bundle.items)
    if item.id not in {existing.id for existing in bundle.items}:
        raise DeliverablesError(f"deliverable not found: {item.id}")
    _write(root, DeliverableSet(bundle.project_id, _SCHEMA_VERSION, items))


def _ensure(project_id: str, root: str | Path) -> None:
    try:
        project_store.load_project(_project_id(project_id), projects_root=root)
    except project_store.ProjectManifestError as exc:
        raise DeliverablesError(str(exc)) from exc


def _path(root: str | Path, project_id: str) -> Path:
    return Path(root) / _project_id(project_id) / _NAME


def _write(root: str | Path, result: DeliverableSet) -> None:
    path = _path(root, result.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"):
        raise DeliverablesError("invalid project id")
    return value


def _asset_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not valid_sidecar_id(item, "vas_") for item in value) or len(set(value)) != len(value):
        raise DeliverablesError("asset_ids must be distinct visual asset ids")
    return tuple(value)


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliverablesError(f"{name} must be non-empty text")
    return value


def _positive(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DeliverablesError(f"{name} must be a positive integer")
    return value


def _boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise DeliverablesError(f"{name} must be boolean")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise DeliverablesError("timestamp must be ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeliverablesError("timestamp must be ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DeliverablesError("timestamp must include timezone")
    return value


__all__ = [
    "ArticlePayload",
    "Deliverable",
    "DeliverableSet",
    "DeliverableSnapshot",
    "DeliverablesError",
    "GalleryCrop",
    "GalleryPayload",
    "GallerySlide",
    "KIND_ARTICLE",
    "KIND_GALLERY",
    "KIND_VIDEO",
    "SEED_IDS",
    "VideoPayload",
    "article_payload",
    "attach_video_job",
    "create_gallery",
    "create_video",
    "gallery_payload",
    "get_deliverable",
    "list_galleries",
    "load_deliverables",
    "platform_for_seed",
    "project_from_variants",
    "seed_id_for",
    "set_gallery_locked",
    "sync_from_variant_set",
    "update_gallery",
    "video_payload",
]
