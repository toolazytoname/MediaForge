"""Human-only, auditable content-package approval sidecar.

Approval deliberately has no connection to publications or publishers.  It is
only a local record that the current master, selected visual assets, and the
two supported platform variants have been inspected by a person.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import master_documents, projects as project_store, variants, visuals

_NAME = "approval.json"
_CHECK_IDS = ("master", "visuals", "wechat_mp", "toutiao")
_HISTORY_LIMIT = 100


class ApprovalError(ValueError):
    """The approval record or its upstream package is invalid."""


@dataclass(frozen=True)
class ApprovalSnapshot:
    master_version: int
    variant_versions: dict[str, int]
    visual_asset_ids: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalCheck:
    id: str
    status: str
    note: str | None
    approved_by: str | None
    approved_at: str | None


@dataclass(frozen=True)
class ApprovalEvent:
    action: str
    check_id: str | None
    note: str | None
    actor: str | None
    at: str


@dataclass(frozen=True)
class Approval:
    project_id: str
    snapshot: ApprovalSnapshot | None
    checks: tuple[ApprovalCheck, ...]
    history: tuple[ApprovalEvent, ...]


@dataclass(frozen=True)
class ApprovalStatus:
    approval: Approval
    ready: bool
    stale: bool
    blockers: tuple[str, ...]
    complete: bool


def load_approval(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> Approval:
    _ensure(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists():
        return Approval(project_id, None, (), ())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApprovalError("invalid approval JSON") from exc
    if not isinstance(raw, dict) or set(raw) != set(Approval.__dataclass_fields__):
        raise ApprovalError("approval manifest has missing or unknown fields")
    if _project_id(raw["project_id"]) != project_id:
        raise ApprovalError("approval project id does not match its directory")
    snapshot = None if raw["snapshot"] is None else _snapshot(raw["snapshot"])
    checks_raw, history_raw = raw["checks"], raw["history"]
    if not isinstance(checks_raw, list) or not isinstance(history_raw, list):
        raise ApprovalError("approval checks and history must be arrays")
    checks = tuple(_check(item) for item in checks_raw)
    if snapshot is None and checks:
        raise ApprovalError("approval checks require a package snapshot")
    if checks and tuple(item.id for item in checks) != _CHECK_IDS:
        raise ApprovalError("approval checks must be the fixed content-package checklist")
    history = tuple(_event(item) for item in history_raw)
    if len(history) > _HISTORY_LIMIT:
        raise ApprovalError("approval history exceeds its limit")
    return Approval(project_id, snapshot, checks, history)


def status(project_id: str, *, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ApprovalStatus:
    approval = load_approval(project_id, projects_root=projects_root)
    current, blockers = _current_snapshot(project_id, projects_root)
    ready = not blockers
    stale = approval.snapshot is not None and (not ready or approval.snapshot != current)
    complete = bool(approval.snapshot and ready and not stale and approval.checks and all(item.status == "approved" for item in approval.checks))
    return ApprovalStatus(approval, ready, stale, tuple(blockers), complete)


def recheck(project_id: str, *, actor: str, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ApprovalStatus:
    _actor(actor); timestamp = _timestamp(now)
    current, blockers = _current_snapshot(project_id, projects_root)
    old = load_approval(project_id, projects_root=projects_root)
    event = ApprovalEvent("rechecked", None, None, actor, timestamp)
    if blockers:
        result = Approval(project_id, None, (), (*old.history, event)[-_HISTORY_LIMIT:])
    else:
        result = Approval(project_id, current, tuple(ApprovalCheck(item, "pending", None, None, None) for item in _CHECK_IDS), (*old.history, event)[-_HISTORY_LIMIT:])
    _write(projects_root, result)
    return status(project_id, projects_root=projects_root)


def decide(project_id: str, check_id: str, *, approved: bool, note: str | None, actor: str, now: str, projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT) -> ApprovalStatus:
    if check_id not in _CHECK_IDS: raise ApprovalError("unknown approval check")
    if not isinstance(approved, bool): raise ApprovalError("approved must be boolean")
    _actor(actor); timestamp = _timestamp(now)
    state = status(project_id, projects_root=projects_root)
    if not state.ready: raise ApprovalError("content package is not ready for approval: " + "; ".join(state.blockers))
    if state.approval.snapshot is None or state.stale: raise ApprovalError("approval requires recheck after upstream changes")
    note_value = _optional_note(note)
    existing = next(item for item in state.approval.checks if item.id == check_id)
    updated = replace(existing, status="approved" if approved else "pending", note=note_value, approved_by=actor if approved else None, approved_at=timestamp if approved else None)
    checks = tuple(updated if item.id == check_id else item for item in state.approval.checks)
    event = ApprovalEvent("approved" if approved else "revoked", check_id, note_value, actor, timestamp)
    result = replace(state.approval, checks=checks, history=(*state.approval.history, event)[-_HISTORY_LIMIT:])
    _write(projects_root, result)
    return status(project_id, projects_root=projects_root)


def _current_snapshot(project_id: str, root: str | Path) -> tuple[ApprovalSnapshot | None, list[str]]:
    blockers: list[str] = []
    try: master = master_documents.load_master(project_id, projects_root=root)
    except master_documents.MasterDocumentError as exc: raise ApprovalError(f"cannot read master: {exc}") from exc
    if master is None: blockers.append("缺少主稿")
    try: items = variants.load_variants(project_id, projects_root=root).variants
    except variants.VariantsError as exc: raise ApprovalError(f"cannot read variants: {exc}") from exc
    by_platform = {item.platform: item for item in items}
    for platform, label in (("wechat_mp", "缺少微信公众号版本"), ("toutiao", "缺少头条版本")):
        item = by_platform.get(platform)
        if item is None: blockers.append(label)
        elif item.upstream_updated: blockers.append(f"{platform} 有未处理上游更新")
    try: plan = visuals.load_visuals(project_id, projects_root=root)
    except visuals.VisualsError as exc: raise ApprovalError(f"cannot read visuals: {exc}") from exc
    selected = tuple(sorted(item.id for item in plan.assets if item.status == "selected"))
    if not selected: blockers.append("尚未选择视觉资产")
    selected_set = set(selected)
    for item in by_platform.values():
        if not set(item.asset_ids) <= selected_set: blockers.append(f"{item.platform} 引用了不可解析的视觉资产")
    if blockers or master is None or len(by_platform) < 2: return None, blockers
    return ApprovalSnapshot(master.version, {"wechat_mp": by_platform["wechat_mp"].version, "toutiao": by_platform["toutiao"].version}, selected), blockers


def _snapshot(value: Any) -> ApprovalSnapshot:
    if not isinstance(value, dict) or set(value) != set(ApprovalSnapshot.__dataclass_fields__): raise ApprovalError("approval snapshot has missing or unknown fields")
    versions = value["variant_versions"]
    if not isinstance(versions, dict) or set(versions) != {"wechat_mp", "toutiao"}: raise ApprovalError("approval snapshot needs two platform versions")
    return ApprovalSnapshot(_positive("master_version", value["master_version"]), {key: _positive(key, versions[key]) for key in versions}, _assets(value["visual_asset_ids"]))
def _check(value: Any) -> ApprovalCheck:
    if not isinstance(value, dict) or set(value) != set(ApprovalCheck.__dataclass_fields__): raise ApprovalError("approval check has missing or unknown fields")
    if value["id"] not in _CHECK_IDS or value["status"] not in {"pending", "approved"}: raise ApprovalError("invalid approval check")
    approved = value["status"] == "approved"
    note = _optional_note(value["note"]); actor = value["approved_by"]; at = value["approved_at"]
    if approved:
        _actor(actor); _timestamp(at)
    elif actor is not None or at is not None: raise ApprovalError("pending approval check cannot have approval metadata")
    return ApprovalCheck(value["id"], value["status"], note, actor, at)
def _event(value: Any) -> ApprovalEvent:
    if not isinstance(value, dict) or set(value) != set(ApprovalEvent.__dataclass_fields__): raise ApprovalError("approval event has missing or unknown fields")
    if value["action"] not in {"rechecked", "approved", "revoked"}: raise ApprovalError("invalid approval event")
    check_id = value["check_id"]
    if value["action"] == "rechecked":
        if check_id is not None or value["note"] is not None: raise ApprovalError("invalid recheck event")
    elif check_id not in _CHECK_IDS: raise ApprovalError("invalid approval event check")
    return ApprovalEvent(value["action"], check_id, _optional_note(value["note"]), _actor(value["actor"]), _timestamp(value["at"]))
def _ensure(project_id: str, root: str | Path) -> None:
    try: project_store.load_project(_project_id(project_id), projects_root=root)
    except project_store.ProjectManifestError as exc: raise ApprovalError(str(exc)) from exc
def _path(root: str | Path, project_id: str) -> Path: return Path(root) / _project_id(project_id) / _NAME
def _write(root: str | Path, result: Approval) -> None:
    path = _path(root, result.project_id); path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_name(path.name + ".tmp"); tmp.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); tmp.replace(path)
def _project_id(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("prj_") or len(value) <= 4: raise ApprovalError("invalid project id")
    return value
def _positive(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1: raise ApprovalError(f"{name} must be positive")
    return value
def _assets(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.startswith("vas_") for item in value) or len(set(value)) != len(value): raise ApprovalError("visual_asset_ids must be distinct selected visual ids")
    return tuple(value)
def _optional_note(value: Any) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()): raise ApprovalError("note must be null or non-empty text")
    return value
def _actor(value: Any) -> str:
    if not isinstance(value, str) or not value.strip(): raise ApprovalError("actor must be non-empty text")
    return value
def _timestamp(value: Any) -> str:
    if not isinstance(value, str): raise ApprovalError("timestamp must be ISO timestamp")
    try: datetime.fromisoformat(value)
    except ValueError as exc: raise ApprovalError("timestamp must be ISO timestamp") from exc
    return value
