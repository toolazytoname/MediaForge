"""平台无关的 Idea sidecar manifest 存储。

Idea 是尚未承诺为内容项目的原始观察或材料。它保存在
``output/ideas/<idea_id>/idea.json``，因此不会触碰已冻结的 SQLite
状态机，也不会隐式产生任何平台稿。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


DEFAULT_IDEAS_ROOT = Path("output/ideas")
_MANIFEST_NAME = "idea.json"
_ALLOWED_INPUT_TYPES = frozenset({"thought", "url", "text"})


class IdeaManifestError(ValueError):
    """Idea manifest 缺失、损坏或不符合 v0 契约。"""


@dataclass(frozen=True)
class Idea:
    id: str
    input_type: str
    content: str
    title: str
    project_id: str | None
    created_at: str
    updated_at: str


def create_idea(
    *,
    input_type: str,
    content: str,
    title: str,
    now: str,
    ideas_root: str | Path = DEFAULT_IDEAS_ROOT,
    idea_id: str | None = None,
) -> Idea:
    """创建 Idea；同 ID 已存在时拒绝覆盖。"""
    idea = Idea(
        id=idea_id or new_id("idea"),
        input_type=_input_type(input_type),
        content=_content(input_type, content),
        title=_required("title", title),
        project_id=None,
        created_at=_timestamp("created_at", now),
        updated_at=_timestamp("updated_at", now),
    )
    path = _manifest_path(ideas_root, idea.id)
    if path.exists():
        raise IdeaManifestError(f"idea already exists: {idea.id}")
    _write_manifest(path, idea)
    return idea


def load_idea(idea_id: str, *, ideas_root: str | Path = DEFAULT_IDEAS_ROOT) -> Idea:
    path = _manifest_path(ideas_root, idea_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IdeaManifestError(f"idea not found: {idea_id}") from exc
    except json.JSONDecodeError as exc:
        raise IdeaManifestError(f"invalid idea JSON: {idea_id}") from exc
    return _idea_from_payload(payload, expected_id=idea_id)


def list_ideas(*, ideas_root: str | Path = DEFAULT_IDEAS_ROOT) -> tuple[Idea, ...]:
    root = Path(ideas_root)
    if not root.exists():
        return ()
    ideas = [load_idea(path.parent.name, ideas_root=root) for path in root.glob(f"*/{_MANIFEST_NAME}")]
    return tuple(sorted(ideas, key=lambda item: item.updated_at, reverse=True))


def promote_idea(
    idea: Idea,
    *,
    project_id: str,
    now: str,
    ideas_root: str | Path = DEFAULT_IDEAS_ROOT,
) -> Idea:
    """返回关联到 Project 的新 Idea，绝不修改传入对象。"""
    project_id = _project_id(project_id)
    if idea.project_id is not None and idea.project_id != project_id:
        raise IdeaManifestError(f"idea already promoted: {idea.id}")
    if idea.project_id == project_id:
        return idea
    updated = replace(idea, project_id=project_id, updated_at=_timestamp("updated_at", now))
    _write_manifest(_manifest_path(ideas_root, updated.id), updated)
    return updated


def _manifest_path(ideas_root: str | Path, idea_id: str) -> Path:
    return Path(ideas_root) / _idea_id(idea_id) / _MANIFEST_NAME


def _idea_id(value: Any) -> str:
    if not valid_sidecar_id(value, "idea_"):
        raise IdeaManifestError(f"invalid idea id: {value!r}")
    return value


def _project_id(value: Any) -> str:
    if not valid_sidecar_id(value, "prj_"):
        raise IdeaManifestError(f"invalid project id: {value!r}")
    return value


def _write_manifest(path: Path, idea: Idea) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(idea), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _idea_from_payload(payload: Any, *, expected_id: str) -> Idea:
    if not isinstance(payload, dict):
        raise IdeaManifestError("idea manifest must be an object")
    fields = set(Idea.__dataclass_fields__)
    if set(payload) != fields:
        raise IdeaManifestError("idea manifest has missing or unknown fields")
    if payload.get("id") != expected_id:
        raise IdeaManifestError("idea manifest id does not match its directory")
    idea = Idea(
        id=_idea_id(payload["id"]),
        input_type=_input_type(payload["input_type"]),
        content=_content(payload["input_type"], payload["content"]),
        title=_required("title", payload["title"]),
        project_id=_project_id(payload["project_id"]) if payload["project_id"] is not None else None,
        created_at=_timestamp("created_at", payload["created_at"]),
        updated_at=_timestamp("updated_at", payload["updated_at"]),
    )
    if datetime.fromisoformat(idea.updated_at) < datetime.fromisoformat(idea.created_at):
        raise IdeaManifestError("updated_at cannot be earlier than created_at")
    return idea


def _required(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdeaManifestError(f"{name} must be a non-empty string")
    return value.strip()


def _input_type(value: Any) -> str:
    if value not in _ALLOWED_INPUT_TYPES:
        raise IdeaManifestError(f"invalid input_type: {value!r}")
    return value


def _content(input_type: Any, value: Any) -> str:
    content = _required("content", value)
    if input_type == "url":
        parsed = urlparse(content)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise IdeaManifestError("url content must be an absolute http(s) URL")
    return content


def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise IdeaManifestError(f"{name} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IdeaManifestError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None:
        raise IdeaManifestError(f"{name} must include a timezone")
    return value
