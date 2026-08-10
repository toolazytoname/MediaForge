"""Project-scoped, independently editable WeChat and Toutiao variants."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import master_documents, projects as project_store, visuals
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "variants.json"
_PLATFORMS = frozenset({"wechat_mp", "toutiao"})
_HISTORY_LIMIT = 20


class VariantsError(ValueError):
    """A project variant sidecar is missing, malformed, or unsafe to change."""


@dataclass(frozen=True)
class VariantSnapshot:
    version: int
    title: str
    summary: str
    body: str
    asset_ids: tuple[str, ...]
    saved_at: str
    reason: str


@dataclass(frozen=True)
class Variant:
    platform: str
    title: str
    summary: str
    body: str
    asset_ids: tuple[str, ...]
    source_master_version: int
    version: int
    locked: bool
    manually_modified: bool
    upstream_updated: bool
    created_at: str
    updated_at: str
    history: tuple[VariantSnapshot, ...]


@dataclass(frozen=True)
class VariantSet:
    project_id: str
    variants: tuple[Variant, ...]


def load_variants(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> VariantSet:
    _ensure(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists(): return VariantSet(_project_id(project_id), ())
    try: raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: raise VariantsError("invalid variants JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"project_id", "variants"}: raise VariantsError("variants manifest has missing or unknown fields")
    if _project_id(raw["project_id"]) != project_id: raise VariantsError("variants project id does not match its directory")
    if not isinstance(raw["variants"], list): raise VariantsError("variants must be an array")
    variants = tuple(_variant(item, project_id, projects_root) for item in raw["variants"])
    if len({item.platform for item in variants}) != len(variants): raise VariantsError("duplicate variant platform")
    return VariantSet(project_id, variants)


def create_from_master(project_id: str, platform: str, *, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Variant:
    platform = _platform(platform)
    current = load_variants(project_id, projects_root=projects_root)
    existing = next((item for item in current.variants if item.platform == platform), None)
    if existing is not None: return existing
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None: raise VariantsError(f"master not found: {project_id}")
    assets = _selected_assets(project_id, projects_root)
    timestamp = _timestamp(now)
    variant = Variant(platform, master.title, _summary(master.body), master.body, assets, master.version, 1, False, False, False, timestamp, timestamp, ())
    _write(projects_root, VariantSet(project_id, (*current.variants, variant)))
    return variant


def create_adapted(
    project_id: str,
    platform: str,
    *,
    title: str,
    summary: str,
    body: str,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Variant:
    """Create an explicit first platform draft without mutating the master."""
    platform = _platform(platform)
    current = load_variants(project_id, projects_root=projects_root)
    existing = next((item for item in current.variants if item.platform == platform), None)
    if existing is not None:
        return existing
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        raise VariantsError(f"master not found: {project_id}")
    timestamp = _timestamp(now)
    variant = Variant(
        platform, _text("title", title), _text("summary", summary), _text("body", body),
        _selected_assets(project_id, projects_root), master.version, 1, False, False,
        False, timestamp, timestamp, (),
    )
    _write(projects_root, VariantSet(project_id, (*current.variants, variant)))
    return variant


def save_manual(project_id: str, platform: str, *, title: str, summary: str, body: str, asset_ids: list[str], now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Variant:
    current = _required(project_id, platform, projects_root)
    if current.locked: raise VariantsError("variant is locked; unlock it before editing")
    return _replace(project_id, current, title=title, summary=summary, body=body, asset_ids=asset_ids, now=now, reason="manual", manually_modified=True, projects_root=projects_root)


def set_locked(project_id: str, platform: str, *, locked: bool, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Variant:
    if not isinstance(locked, bool): raise VariantsError("locked must be boolean")
    current = _required(project_id, platform, projects_root)
    result = replace(current, locked=locked, updated_at=_timestamp(now))
    _replace_in_set(project_id, current.platform, result, projects_root)
    return result


def check_upstream(project_id: str, platform: str, *, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Variant:
    current = _required(project_id, platform, projects_root)
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None: raise VariantsError(f"master not found: {project_id}")
    result = replace(current, upstream_updated=master.version > current.source_master_version, updated_at=_timestamp(now))
    _replace_in_set(project_id, current.platform, result, projects_root)
    return result


def acknowledge_master_update(
    project_id: str,
    platform: str,
    *,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> Variant:
    """Record that the current independent draft has incorporated the latest master.

    This is deliberately an acknowledgement, not a merge: none of the platform
    copy or selected assets are overwritten.  Requiring an unlocked draft makes
    the human review step explicit and the new version keeps an audit snapshot.
    """
    current = _required(project_id, platform, projects_root)
    if current.locked:
        raise VariantsError("variant is locked; unlock it before acknowledging the master update")
    master = master_documents.load_master(project_id, projects_root=projects_root)
    if master is None:
        raise VariantsError(f"master not found: {project_id}")
    if master.version < current.source_master_version:
        raise VariantsError("variant source master version is newer than the current master")
    if master.version == current.source_master_version:
        if not current.upstream_updated:
            return current
        result = replace(current, upstream_updated=False, updated_at=_timestamp(now))
        _replace_in_set(project_id, current.platform, result, projects_root)
        return result
    return _replace(
        project_id,
        current,
        title=current.title,
        summary=current.summary,
        body=current.body,
        asset_ids=list(current.asset_ids),
        now=now,
        reason=f"acknowledge-master:{current.source_master_version}->{master.version}",
        manually_modified=True,
        source_master_version=master.version,
        upstream_updated=False,
        projects_root=projects_root,
    )


def restore_version(project_id: str, platform: str, version: int, *, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Variant:
    current = _required(project_id, platform, projects_root)
    if current.locked: raise VariantsError("variant is locked; unlock it before restoring")
    snapshot = next((item for item in current.history if item.version == version), None)
    if snapshot is None: raise VariantsError(f"variant version not found: {version}")
    return _replace(project_id, current, title=snapshot.title, summary=snapshot.summary, body=snapshot.body, asset_ids=list(snapshot.asset_ids), now=now, reason=f"restore:{version}", manually_modified=True, projects_root=projects_root)


def _replace(
    project_id: str,
    current: Variant,
    *,
    title: str,
    summary: str,
    body: str,
    asset_ids: list[str],
    now: str,
    reason: str,
    manually_modified: bool,
    projects_root: str | Path,
    source_master_version: int | None = None,
    upstream_updated: bool | None = None,
) -> Variant:
    _validate_assets(project_id, asset_ids, projects_root)
    snapshot = VariantSnapshot(current.version, current.title, current.summary, current.body, current.asset_ids, current.updated_at, reason)
    result = Variant(
        current.platform,
        _text("title", title),
        _text("summary", summary),
        _text("body", body),
        tuple(asset_ids),
        current.source_master_version if source_master_version is None else _positive("source_master_version", source_master_version),
        current.version + 1,
        current.locked,
        manually_modified,
        current.upstream_updated if upstream_updated is None else _boolean("upstream_updated", upstream_updated),
        current.created_at,
        _timestamp(now),
        (*current.history, snapshot)[-_HISTORY_LIMIT:],
    )
    _replace_in_set(project_id, current.platform, result, projects_root)
    return result


def _required(project_id: str, platform: str, root: str | Path) -> Variant:
    target = _platform(platform)
    item = next((item for item in load_variants(project_id, projects_root=root).variants if item.platform == target), None)
    if item is None: raise VariantsError(f"variant not found: {target}")
    return item


def _replace_in_set(project_id: str, platform: str, variant: Variant, root: str | Path) -> None:
    current = load_variants(project_id, projects_root=root)
    _write(root, VariantSet(project_id, tuple(variant if item.platform == platform else item for item in current.variants)))


def _variant(value: Any, project_id: str, root: str | Path) -> Variant:
    if not isinstance(value, dict) or set(value) != set(Variant.__dataclass_fields__): raise VariantsError("variant has missing or unknown fields")
    history_raw = value["history"]
    if not isinstance(history_raw, list): raise VariantsError("variant history must be an array")
    item = Variant(_platform(value["platform"]), _text("title", value["title"]), _text("summary", value["summary"]), _text("body", value["body"]), _asset_ids(value["asset_ids"]), _positive("source_master_version", value["source_master_version"]), _positive("version", value["version"]), _boolean("locked", value["locked"]), _boolean("manually_modified", value["manually_modified"]), _boolean("upstream_updated", value["upstream_updated"]), _timestamp(value["created_at"]), _timestamp(value["updated_at"]), tuple(_snapshot(x) for x in history_raw))
    history_versions = [item.version for item in item.history]
    if (len(item.history) > _HISTORY_LIMIT or any(version >= item.version for version in history_versions)
            or len(set(history_versions)) != len(history_versions)
            or history_versions != sorted(history_versions)):
        raise VariantsError("invalid variant history")
    for snapshot in item.history:
        _validate_historical_assets(project_id, list(snapshot.asset_ids), root)
    # Selecting a replacement visual deliberately turns the previous asset
    # into a candidate.  Existing drafts must remain readable and recoverable
    # until the creator explicitly saves a new version with selected assets.
    _validate_historical_assets(project_id, list(item.asset_ids), root)
    return item


def _snapshot(value: Any) -> VariantSnapshot:
    if not isinstance(value, dict) or set(value) != set(VariantSnapshot.__dataclass_fields__): raise VariantsError("variant snapshot has missing or unknown fields")
    return VariantSnapshot(_positive("version", value["version"]), _text("title", value["title"]), _text("summary", value["summary"]), _text("body", value["body"]), _asset_ids(value["asset_ids"]), _timestamp(value["saved_at"]), _text("reason", value["reason"]))


def _selected_assets(project_id: str, root: str | Path) -> tuple[str, ...]:
    try: return tuple(item.id for item in visuals.load_visuals(project_id, projects_root=root).assets if item.status == "selected")
    except visuals.VisualsError as exc: raise VariantsError(f"cannot read visuals: {exc}") from exc
def _validate_assets(project_id: str, ids: list[str], root: str | Path) -> None:
    values = _asset_ids(ids)
    try: known = {item.id for item in visuals.load_visuals(project_id, projects_root=root).assets if item.status == "selected"}
    except visuals.VisualsError as exc: raise VariantsError(f"cannot read visuals: {exc}") from exc
    if not set(values) <= known: raise VariantsError("variant references an unknown or unselected visual asset")


def _validate_historical_assets(project_id: str, ids: list[str], root: str | Path) -> None:
    """Validate an existing version without requiring assets to stay selected.

    Selection is a mutable visual-plan decision, whereas a platform draft and
    its snapshots are immutable authoring history.  Failed or unknown assets
    remain invalid; a formerly selected candidate remains readable only until
    an explicit save replaces it through `_validate_assets`.
    """
    values = _asset_ids(ids)
    try:
        known = {
            item.id for item in visuals.load_visuals(project_id, projects_root=root).assets
            if item.status in {"candidate", "selected"}
        }
    except visuals.VisualsError as exc:
        raise VariantsError(f"cannot read visuals: {exc}") from exc
    if not set(values) <= known:
        raise VariantsError("variant references an unknown or unusable historical visual asset")
def _asset_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not valid_sidecar_id(x, "vas_") for x in value) or len(set(value)) != len(value): raise VariantsError("asset_ids must be distinct visual asset ids")
    return tuple(value)
def _summary(body: str) -> str: return body.strip().replace("\n", " ")[:180] or "摘要待补充"
def _ensure(project_id: str, root: str | Path) -> None:
    try: project_store.load_project(_project_id(project_id), projects_root=root)
    except project_store.ProjectManifestError as exc: raise VariantsError(str(exc)) from exc
def _path(root: str | Path, project_id: str) -> Path: return Path(root) / _project_id(project_id) / _NAME
def _write(root: str | Path, result: VariantSet) -> None:
    path = _path(root, result.project_id); path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); tmp.replace(path)
def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"): raise VariantsError("invalid project id")
    return value
def _platform(value: Any) -> str:
    if value not in _PLATFORMS: raise VariantsError("platform must be wechat_mp or toutiao")
    return value
def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip(): raise VariantsError(f"{name} must be non-empty text")
    return value
def _positive(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1: raise VariantsError(f"{name} must be a positive integer")
    return value
def _boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool): raise VariantsError(f"{name} must be boolean")
    return value
def _timestamp(value: Any) -> str:
    if not isinstance(value, str): raise VariantsError("timestamp must be ISO timestamp")
    try: parsed = datetime.fromisoformat(value)
    except ValueError as exc: raise VariantsError("timestamp must be ISO timestamp") from exc
    if parsed.tzinfo is None: raise VariantsError("timestamp must include timezone")
    return value
