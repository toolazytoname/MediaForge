"""Project Deliverable sidecar with variants.json dual-write (RFC §5.2)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import projects as project_store, variants as variant_store
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "deliverables.json"
_HISTORY_LIMIT = 20
_SCHEMA_VERSION = 1
KIND_ARTICLE = "article"
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
    if value["kind"] not in {KIND_ARTICLE, "gallery", "video"}:
        raise DeliverablesError("invalid deliverable kind")
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
    "KIND_ARTICLE",
    "SEED_IDS",
    "article_payload",
    "get_deliverable",
    "load_deliverables",
    "platform_for_seed",
    "project_from_variants",
    "seed_id_for",
    "sync_from_variant_set",
]
