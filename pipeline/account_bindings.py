"""Project-to-account bindings. One account per platform per project."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.account_profiles import AccountProfileError, load_profile
from pipeline.projects import DEFAULT_PROJECTS_ROOT, ProjectManifestError, load_project
from pipeline.utils.sidecar_ids import valid_sidecar_id


_MANIFEST_NAME = "account_binding.json"
_BINDING_FIELDS = frozenset({"platform", "account_id"})
_DOC_FIELDS = frozenset({"project_id", "items", "updated_at"})


class AccountBindingError(ValueError):
    """Project account binding missing or inconsistent."""


@dataclass(frozen=True)
class AccountBinding:
    platform: str
    account_id: str


@dataclass(frozen=True)
class ProjectAccountBindings:
    project_id: str
    items: tuple[AccountBinding, ...]
    updated_at: str


def load_bindings(
    project_id: str, *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> ProjectAccountBindings:
    _project_id(project_id)
    path = _path(projects_root, project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AccountBindingError(f"project {project_id} is not bound to an account") from exc
    except json.JSONDecodeError as exc:
        raise AccountBindingError(f"invalid account binding JSON: {project_id}") from exc
    return _from_payload(payload, expected_id=project_id)


def bind_project_account(
    project_id: str,
    *,
    platform: str,
    account_id: str,
    now: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    accounts_root: str | Path,
) -> ProjectAccountBindings:
    try:
        load_project(project_id, projects_root=projects_root)
    except ProjectManifestError as exc:
        raise AccountBindingError(f"project not found: {project_id}") from exc
    try:
        profile = load_profile(account_id, accounts_root=accounts_root)
    except AccountProfileError as exc:
        raise AccountBindingError(f"account not found: {account_id}") from exc
    if profile.platform != platform:
        raise AccountBindingError(
            f"account {account_id} is {profile.platform}, not {platform}"
        )
    try:
        current = load_bindings(project_id, projects_root=projects_root)
        items = [item for item in current.items if item.platform != platform]
    except AccountBindingError:
        items = []
    items.append(AccountBinding(platform=platform, account_id=account_id))
    bindings = ProjectAccountBindings(
        project_id=project_id,
        items=tuple(sorted(items, key=lambda item: item.platform)),
        updated_at=_timestamp(now),
    )
    _write(_path(projects_root, project_id), bindings)
    return bindings


def require_binding(
    project_id: str,
    *,
    platform: str,
    account_id: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> AccountBinding:
    bindings = load_bindings(project_id, projects_root=projects_root)
    for item in bindings.items:
        if item.platform == platform:
            if item.account_id != account_id:
                raise AccountBindingError(
                    f"project {project_id} {platform} does not match account {account_id}"
                )
            return item
    raise AccountBindingError(f"project {project_id} is not bound to an account")


def _path(projects_root: str | Path, project_id: str) -> Path:
    return Path(projects_root) / _project_id(project_id) / _MANIFEST_NAME


def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"):
        raise AccountBindingError(f"invalid project id: {value!r}")
    return value


def _write(path: Path, bindings: ProjectAccountBindings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    payload = {
        "project_id": bindings.project_id,
        "updated_at": bindings.updated_at,
        "items": [
            {"platform": item.platform, "account_id": item.account_id}
            for item in bindings.items
        ],
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _from_payload(payload: Any, *, expected_id: str) -> ProjectAccountBindings:
    if not isinstance(payload, dict):
        raise AccountBindingError("account binding must be an object")
    if set(payload) != _DOC_FIELDS:
        raise AccountBindingError("account binding has missing or unknown fields")
    if payload.get("project_id") != expected_id:
        raise AccountBindingError("account binding project_id does not match directory")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise AccountBindingError("items must be a list")
    items: list[AccountBinding] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != _BINDING_FIELDS:
            raise AccountBindingError("account binding has missing or unknown fields")
        platform = raw["platform"]
        account_id = raw["account_id"]
        if platform in seen:
            raise AccountBindingError(f"duplicate platform binding: {platform}")
        if not valid_sidecar_id(account_id, "acc_"):
            raise AccountBindingError(f"invalid account id: {account_id!r}")
        seen.add(platform)
        items.append(AccountBinding(platform=str(platform), account_id=str(account_id)))
    return ProjectAccountBindings(
        project_id=expected_id,
        items=tuple(items),
        updated_at=_timestamp(payload["updated_at"]),
    )


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountBindingError("updated_at must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AccountBindingError("updated_at must be an ISO8601 string") from exc
    if parsed.tzinfo is None:
        raise AccountBindingError("updated_at must include a timezone")
    return value


__all__ = [
    "AccountBinding",
    "AccountBindingError",
    "ProjectAccountBindings",
    "bind_project_account",
    "load_bindings",
    "require_binding",
]
