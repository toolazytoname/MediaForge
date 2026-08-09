"""Project v0 的 sidecar manifest 存储。

Project 不是新的 SQLite 实体；它把一个主题下的研究、创作意图和既有内容
ID 聚合在 ``output/projects/<project_id>/project.json``，为创作工作台提供
稳定边界，同时不改变 TECH_SPEC 冻结的 topics/contents 关系。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pipeline.utils.ids import new_id


DEFAULT_PROJECTS_ROOT = Path("output/projects")
_MANIFEST_NAME = "project.json"
_ALLOWED_AUTONOMY = frozenset({"assist", "collaborate", "draft", "pack"})


class ProjectManifestError(ValueError):
    """Project manifest 缺失、损坏或不符合 v0 契约。"""


@dataclass(frozen=True)
class Project:
    """主题项目的不可变 v0 记录。"""

    id: str
    title: str
    idea: str
    audience: str
    goal: str
    voice: str
    autonomy: str
    content_ids: tuple[str, ...]
    asset_paths: tuple[str, ...]
    created_at: str
    updated_at: str


def create_project(
    *,
    title: str,
    idea: str,
    audience: str,
    goal: str,
    voice: str,
    autonomy: str,
    now: str,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    project_id: str | None = None,
) -> Project:
    """创建并持久化一个新项目；同 ID 已存在时拒绝覆盖。"""
    project = Project(
        id=project_id or new_id("prj"),
        title=_required("title", title),
        idea=_required("idea", idea),
        audience=_required("audience", audience),
        goal=_required("goal", goal),
        voice=_required("voice", voice),
        autonomy=_autonomy(autonomy),
        content_ids=(),
        asset_paths=(),
        created_at=_timestamp("created_at", now),
        updated_at=_timestamp("updated_at", now),
    )
    path = _manifest_path(projects_root, project.id)
    if path.exists():
        raise ProjectManifestError(f"project already exists: {project.id}")
    _write_manifest(path, project)
    return project


def load_project(
    project_id: str,
    *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> Project:
    """读取并严格验证一个 Project manifest。"""
    path = _manifest_path(projects_root, project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectManifestError(f"project not found: {project_id}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectManifestError(f"invalid project JSON: {project_id}") from exc
    return _project_from_payload(payload, expected_id=project_id)


def list_projects(
    *, projects_root: str | Path = DEFAULT_PROJECTS_ROOT
) -> tuple[Project, ...]:
    """返回所有合法项目，按最近更新时间降序。"""
    root = Path(projects_root)
    if not root.exists():
        return ()
    projects = [
        load_project(path.parent.name, projects_root=root)
        for path in root.glob(f"*/{_MANIFEST_NAME}")
    ]
    return tuple(sorted(projects, key=lambda item: item.updated_at, reverse=True))


def update_project(
    project: Project,
    *,
    now: str,
    title: str | None = None,
    idea: str | None = None,
    audience: str | None = None,
    goal: str | None = None,
    voice: str | None = None,
    autonomy: str | None = None,
    content_ids: Iterable[str] | None = None,
    asset_paths: Iterable[str] | None = None,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> Project:
    """返回并持久化替换后的不可变项目，不改变调用方对象。"""
    updated = replace(
        project,
        title=_required("title", title) if title is not None else project.title,
        idea=_required("idea", idea) if idea is not None else project.idea,
        audience=_required("audience", audience) if audience is not None else project.audience,
        goal=_required("goal", goal) if goal is not None else project.goal,
        voice=_required("voice", voice) if voice is not None else project.voice,
        autonomy=_autonomy(autonomy) if autonomy is not None else project.autonomy,
        content_ids=_references("content_ids", content_ids) if content_ids is not None else project.content_ids,
        asset_paths=_references("asset_paths", asset_paths) if asset_paths is not None else project.asset_paths,
        updated_at=_timestamp("updated_at", now),
    )
    _write_manifest(_manifest_path(projects_root, updated.id), updated)
    return updated


def _manifest_path(projects_root: str | Path, project_id: str) -> Path:
    return Path(projects_root) / _project_id(project_id) / _MANIFEST_NAME


def _project_id(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("prj_"):
        raise ProjectManifestError(f"invalid project id: {value!r}")
    return value


def _write_manifest(path: Path, project: Project) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(
        json.dumps(asdict(project), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _project_from_payload(payload: Any, *, expected_id: str) -> Project:
    if not isinstance(payload, dict):
        raise ProjectManifestError("project manifest must be an object")
    fields = set(Project.__dataclass_fields__)
    if set(payload) != fields:
        raise ProjectManifestError("project manifest has missing or unknown fields")
    if payload.get("id") != expected_id:
        raise ProjectManifestError("project manifest id does not match its directory")
    project = Project(
        id=_project_id(payload["id"]),
        title=_required("title", payload["title"]),
        idea=_required("idea", payload["idea"]),
        audience=_required("audience", payload["audience"]),
        goal=_required("goal", payload["goal"]),
        voice=_required("voice", payload["voice"]),
        autonomy=_autonomy(payload["autonomy"]),
        content_ids=_references("content_ids", payload["content_ids"]),
        asset_paths=_references("asset_paths", payload["asset_paths"]),
        created_at=_timestamp("created_at", payload["created_at"]),
        updated_at=_timestamp("updated_at", payload["updated_at"]),
    )
    if datetime.fromisoformat(project.updated_at) < datetime.fromisoformat(project.created_at):
        raise ProjectManifestError("updated_at cannot be earlier than created_at")
    return project


def _required(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectManifestError(f"{name} must be a non-empty string")
    return value.strip()


def _autonomy(value: Any) -> str:
    if value not in _ALLOWED_AUTONOMY:
        raise ProjectManifestError(f"invalid autonomy: {value!r}")
    return value


def _references(name: str, values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ProjectManifestError(f"{name} must be a list of non-empty strings")
    normalized = tuple(value.strip() for value in values)
    if len(set(normalized)) != len(normalized):
        raise ProjectManifestError(f"{name} cannot contain duplicate references")
    if name == "content_ids" and any(not value.startswith("c_") for value in normalized):
        raise ProjectManifestError("content_ids must use existing c_ identifiers")
    if name == "asset_paths" and any(
        Path(value).is_absolute() or ".." in Path(value).parts or not value.startswith("output/")
        for value in normalized
    ):
        raise ProjectManifestError("asset_paths must be relative paths under output/")
    return normalized


def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectManifestError(f"{name} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProjectManifestError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None:
        raise ProjectManifestError(f"{name} must include a timezone")
    return value
