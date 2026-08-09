"""Project-scoped visual plan and auditable image candidate sidecars."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pipeline import projects as project_store
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "visuals.json"
_RATIOS = frozenset({"1:1", "16:9", "9:16", "4:3", "3:4"})


class VisualsError(ValueError):
    pass


@dataclass(frozen=True)
class VisualSlot:
    id: str
    purpose: str
    paragraph_anchor: str | None
    direction: str
    aspect_ratio: str


@dataclass(frozen=True)
class VisualAsset:
    id: str
    slot_id: str
    prompt: str
    model: str
    size: str
    version: int
    reference_asset_id: str | None
    cost_usd: float
    file_path: str | None
    status: str
    failure: str | None
    selection_reason: str | None
    user_rating: int | None
    created_at: str


@dataclass(frozen=True)
class VisualPlan:
    project_id: str
    bible: dict[str, str]
    slots: tuple[VisualSlot, ...]
    assets: tuple[VisualAsset, ...]


def load_visuals(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> VisualPlan:
    _ensure(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists():
        return VisualPlan(project_id, {}, (), ())
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: raise VisualsError(f"invalid visuals JSON: {project_id}") from exc
    if not isinstance(payload, dict) or set(payload) != {"project_id", "bible", "slots", "assets"}:
        raise VisualsError("visuals manifest has missing or unknown fields")
    if payload["project_id"] != project_id or not isinstance(payload["bible"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in payload["bible"].items()):
        raise VisualsError("visuals manifest has invalid project id or bible")
    slots = tuple(_slot(item) for item in payload["slots"])
    if len({item.id for item in slots}) != len(slots): raise VisualsError("duplicate visual slot id")
    assets = tuple(_asset(item, {slot.id for slot in slots}) for item in payload["assets"])
    if len({item.id for item in assets}) != len(assets): raise VisualsError("duplicate visual asset id")
    known_assets = {item.id for item in assets}
    if any(item.reference_asset_id and item.reference_asset_id not in known_assets for item in assets): raise VisualsError("unknown reference asset")
    return VisualPlan(project_id, dict(payload["bible"]), slots, assets)


def save_plan(project_id: str, *, bible: dict[str, str], slots: list[dict[str, Any]], projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> VisualPlan:
    current = load_visuals(project_id, projects_root=projects_root)
    if not isinstance(bible, dict) or not all(isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in bible.items()): raise VisualsError("bible must be a string map")
    new_slots = tuple(_slot(item) for item in slots)
    if len({item.id for item in new_slots}) != len(new_slots): raise VisualsError("duplicate visual slot id")
    removed = {asset.slot_id for asset in current.assets} - {slot.id for slot in new_slots}
    if removed: raise VisualsError("cannot remove a slot that has assets")
    plan = VisualPlan(project_id, dict(bible), new_slots, current.assets)
    _write(_path(projects_root, project_id), plan)
    return plan


def record_asset(project_id: str, *, slot_id: str, prompt: str, model: str, size: str, cost_usd: float, now: str,
                 file_path: str | None, status: str, failure: str | None = None, reference_asset_id: str | None = None,
                 projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT, asset_id: str | None = None) -> VisualAsset:
    plan = load_visuals(project_id, projects_root=projects_root)
    if slot_id not in {slot.id for slot in plan.slots}: raise VisualsError(f"visual slot not found: {slot_id}")
    if status not in {"candidate", "failed"}: raise VisualsError("new asset status must be candidate or failed")
    if status == "failed" and not failure: raise VisualsError("failed asset requires failure")
    if failure is not None: _text("failure", failure)
    if status == "failed" and file_path is not None: raise VisualsError("failed asset cannot have file_path")
    if status != "failed" and (not isinstance(file_path, str) or not file_path): raise VisualsError("successful asset requires file_path")
    if file_path is not None: _asset_file_path(file_path)
    if reference_asset_id is not None:
        _id(reference_asset_id, "vas_")
        if reference_asset_id not in {item.id for item in plan.assets}: raise VisualsError("unknown reference asset")
    version = 1 + max((item.version for item in plan.assets if item.slot_id == slot_id), default=0)
    asset = VisualAsset(_id(asset_id or new_id("vas"), "vas_"), slot_id, _text("prompt", prompt), _text("model", model), _text("size", size), version, reference_asset_id, _cost(cost_usd), file_path, status, failure, None, None, _timestamp(now))
    _write(_path(projects_root, project_id), replace(plan, assets=(*plan.assets, asset)))
    return asset


def select_asset(project_id: str, asset_id: str, *, reason: str, rating: int | None, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> VisualAsset:
    plan = load_visuals(project_id, projects_root=projects_root)
    target = next((item for item in plan.assets if item.id == asset_id), None)
    if target is None: raise VisualsError(f"visual asset not found: {asset_id}")
    if target.status == "failed": raise VisualsError("failed asset cannot be selected")
    if rating is not None and (not isinstance(rating, int) or not 1 <= rating <= 5): raise VisualsError("user_rating must be 1..5")
    updated = replace(target, status="selected", selection_reason=_text("selection_reason", reason), user_rating=rating)
    assets = tuple(updated if item.id == asset_id else replace(item, status="candidate", selection_reason=None, user_rating=None) if item.slot_id == target.slot_id and item.status == "selected" else item for item in plan.assets)
    _write(_path(projects_root, project_id), replace(plan, assets=assets))
    return updated


def asset_path(project_id: str, asset_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Path:
    return Path(projects_root) / _id(project_id, "prj_") / "assets" / f"{_id(asset_id, 'vas_')}.png"


def _slot(value: Any) -> VisualSlot:
    if not isinstance(value, dict) or set(value) != set(VisualSlot.__dataclass_fields__): raise VisualsError("visual slot has missing or unknown fields")
    ratio = value["aspect_ratio"]
    if ratio not in _RATIOS: raise VisualsError("invalid visual aspect ratio")
    anchor = value["paragraph_anchor"]
    if anchor is not None and (not isinstance(anchor, str) or not anchor.strip()): raise VisualsError("paragraph_anchor must be null or text")
    return VisualSlot(_id(value["id"], "vsl_"), _text("purpose", value["purpose"]), anchor, _text("direction", value["direction"]), ratio)


def _asset(value: Any, slot_ids: set[str]) -> VisualAsset:
    if not isinstance(value, dict) or set(value) != set(VisualAsset.__dataclass_fields__): raise VisualsError("visual asset has missing or unknown fields")
    asset = VisualAsset(**value)
    if _id(asset.id, "vas_") != asset.id or asset.slot_id not in slot_ids or asset.status not in {"candidate", "failed", "selected"} or not isinstance(asset.version, int) or asset.version < 1:
        raise VisualsError("invalid visual asset")
    _text("prompt", asset.prompt); _text("model", asset.model); _text("size", asset.size); _timestamp(asset.created_at); _cost(asset.cost_usd)
    if asset.reference_asset_id is not None: _id(asset.reference_asset_id, "vas_")
    if asset.status == "failed":
        if asset.file_path is not None or not isinstance(asset.failure, str) or not asset.failure.strip() or asset.selection_reason is not None or asset.user_rating is not None:
            raise VisualsError("invalid failed visual asset")
    else:
        if asset.failure is not None or not isinstance(asset.file_path, str): raise VisualsError("invalid successful visual asset")
        _asset_file_path(asset.file_path)
        if asset.status == "selected":
            _text("selection_reason", asset.selection_reason)
        elif asset.selection_reason is not None or asset.user_rating is not None:
            raise VisualsError("candidate visual asset cannot have selection metadata")
        if asset.user_rating is not None and (not isinstance(asset.user_rating, int) or isinstance(asset.user_rating, bool) or not 1 <= asset.user_rating <= 5):
            raise VisualsError("user_rating must be 1..5")
    return asset


def _ensure(project_id: str, root: str | Path) -> None:
    try: project_store.load_project(project_id, projects_root=root)
    except project_store.ProjectManifestError as exc: raise VisualsError(str(exc)) from exc

def _path(root: str | Path, project_id: str) -> Path: return Path(root) / _id(project_id, "prj_") / _NAME
def _write(path: Path, plan: VisualPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); tmp.replace(path)
def _id(value: Any, prefix: str) -> str:
    if not valid_sidecar_id(value, prefix): raise VisualsError(f"invalid id: {value!r}")
    return value
def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip(): raise VisualsError(f"{name} must be non-empty text")
    return value
def _asset_file_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 2 or path.parts[0] != "assets" or path.suffix.lower() != ".png" or not path.stem.startswith("vas_"):
        raise VisualsError("invalid visual asset file_path")
    return value
def _timestamp(value: Any) -> str:
    if not isinstance(value, str): raise VisualsError("created_at must be ISO timestamp")
    try: parsed = datetime.fromisoformat(value)
    except ValueError as exc: raise VisualsError("created_at must be ISO timestamp") from exc
    if parsed.tzinfo is None: raise VisualsError("created_at must include timezone")
    return value
def _cost(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0: raise VisualsError("cost_usd must be non-negative")
    return float(value)
