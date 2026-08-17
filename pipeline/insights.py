"""Pending learning-suggestion cards (LAZY-88).

Generate writes pending cards only. Accept/reject persist the decision
on the card. Visual bible, Project brand fields, and CapabilityRegistry
are never mutated from this module.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import projects as project_store, visuals
from pipeline.publishers.capability_registry import all_capabilities
from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id

_NAME = "insights.json"
ALLOWED_KINDS = frozenset({"visual_bible", "brand_rule", "capability", "general"})
ALLOWED_STATUS = frozenset({"pending", "accepted", "rejected"})
PROTECTED_KINDS = frozenset({"visual_bible", "brand_rule", "capability"})


class InsightError(ValueError):
    """Suggestion sidecar is invalid or a protected store would be mutated."""


@dataclass(frozen=True)
class Suggestion:
    id: str
    kind: str
    title: str
    body: str
    payload: dict[str, str]
    status: str
    created_at: str
    decided_at: str | None
    actor: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InsightBoard:
    project_id: str
    suggestions: tuple[Suggestion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "suggestions": [item.to_dict() for item in self.suggestions],
        }


def load_insights(
    project_id: str,
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> InsightBoard:
    _ensure(project_id, projects_root)
    path = _path(projects_root, project_id)
    if not path.exists():
        return InsightBoard(project_id, ())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InsightError("invalid insights JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"project_id", "suggestions"}:
        raise InsightError("insights manifest has missing or unknown fields")
    if raw["project_id"] != project_id:
        raise InsightError("insights project id does not match its directory")
    items = raw["suggestions"]
    if not isinstance(items, list):
        raise InsightError("suggestions must be an array")
    return InsightBoard(project_id, tuple(_suggestion(item) for item in items))


def generate_suggestions(
    project_id: str,
    *,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    actor: str = "cron",
) -> InsightBoard:
    """Write pending cards. Does not touch bible / brand / capabilities."""
    _timestamp(now)
    project = project_store.load_project(project_id, projects_root=projects_root)
    bible_before = dict(visuals.load_visuals(project_id, projects_root=projects_root).bible)
    caps_before = _capability_fingerprint()
    existing = load_insights(project_id, projects_root=projects_root)
    pending_kinds = {item.kind for item in existing.suggestions if item.status == "pending"}
    created: list[Suggestion] = []
    templates = (
        Suggestion(
            new_id("ins"), "visual_bible", "视觉圣经待确认",
            "建议把配色写得更克制；确认前不会改 visuals.bible。",
            {"field": "bible.style", "proposed": "克制、少装饰"},
            "pending", now, None, None,
        ),
        Suggestion(
            new_id("ins"), "brand_rule", "品牌语气待确认",
            "建议统一第二人称；确认前不会改 Project.voice / goal。",
            {"field": "voice", "proposed": project.voice},
            "pending", now, None, None,
        ),
        Suggestion(
            new_id("ins"), "capability", "平台能力声明待确认",
            "Bilibili 等长尾平台应保持仅导出，不得改成直发。",
            {"platform": "bilibili", "proposed": "export_only"},
            "pending", now, None, None,
        ),
        Suggestion(
            new_id("ins"), "general", "复盘观察",
            "夹具指标可先人工看趋势；未确认建议不会落成规则。",
            {"note": "review_only"},
            "pending", now, None, None,
        ),
    )
    for card in templates:
        if card.kind in pending_kinds:
            continue
        created.append(card)
    board = InsightBoard(project_id, (*existing.suggestions, *created))
    _write(projects_root, board)
    bible_after = dict(visuals.load_visuals(project_id, projects_root=projects_root).bible)
    if bible_after != bible_before:
        raise InsightError("generate must not mutate the visual bible")
    if _capability_fingerprint() != caps_before:
        raise InsightError("generate must not mutate capability statements")
    reloaded = project_store.load_project(project_id, projects_root=projects_root)
    if (reloaded.voice, reloaded.goal, reloaded.audience) != (project.voice, project.goal, project.audience):
        raise InsightError("generate must not mutate brand fields")
    _ = actor
    return board


def decide_suggestion(
    project_id: str,
    suggestion_id: str,
    *,
    accepted: bool,
    actor: str,
    now: str,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> InsightBoard:
    """Persist the decision on the card. Protected stores stay unchanged."""
    if not isinstance(accepted, bool):
        raise InsightError("accepted must be boolean")
    if not isinstance(actor, str) or not actor.strip():
        raise InsightError("actor must be non-empty text")
    _timestamp(now)
    project = project_store.load_project(project_id, projects_root=projects_root)
    bible_before = dict(visuals.load_visuals(project_id, projects_root=projects_root).bible)
    caps_before = _capability_fingerprint()
    board = load_insights(project_id, projects_root=projects_root)
    found = next((item for item in board.suggestions if item.id == suggestion_id), None)
    if found is None:
        raise InsightError("suggestion not found")
    if found.status != "pending":
        raise InsightError("suggestion already decided")
    updated = replace(
        found,
        status="accepted" if accepted else "rejected",
        decided_at=now,
        actor=actor.strip(),
    )
    if updated.kind in PROTECTED_KINDS and accepted:
        # 落盘的是建议卡片，不是视觉圣经 / 品牌规则 / 能力声明。
        pass
    result = InsightBoard(
        project_id,
        tuple(updated if item.id == suggestion_id else item for item in board.suggestions),
    )
    _write(projects_root, result)
    bible_after = dict(visuals.load_visuals(project_id, projects_root=projects_root).bible)
    if bible_after != bible_before:
        raise InsightError("accept must not mutate the visual bible")
    if _capability_fingerprint() != caps_before:
        raise InsightError("accept must not mutate capability statements")
    reloaded = project_store.load_project(project_id, projects_root=projects_root)
    if (reloaded.voice, reloaded.goal, reloaded.audience) != (project.voice, project.goal, project.audience):
        raise InsightError("accept must not mutate brand fields")
    return result


def list_all_insights(
    *,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for project in project_store.list_projects(projects_root=projects_root):
        board = load_insights(project.id, projects_root=projects_root)
        for card in board.suggestions:
            payload = card.to_dict()
            payload["project_id"] = project.id
            items.append(payload)
    return items


def _capability_fingerprint() -> tuple[tuple[str, bool, bool, bool], ...]:
    return tuple(
        (item.platform, item.delivery.direct, item.official_api, item.lane == "export")
        for item in all_capabilities()
    )


def _suggestion(value: Any) -> Suggestion:
    if not isinstance(value, dict) or set(value) != set(Suggestion.__dataclass_fields__):
        raise InsightError("suggestion has missing or unknown fields")
    if value["kind"] not in ALLOWED_KINDS or value["status"] not in ALLOWED_STATUS:
        raise InsightError("invalid suggestion kind or status")
    payload = value["payload"]
    if not isinstance(payload, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items()):
        raise InsightError("payload must be a string map")
    return Suggestion(
        value["id"], value["kind"], value["title"], value["body"],
        dict(payload), value["status"], value["created_at"],
        value["decided_at"], value["actor"],
    )


def _ensure(project_id: str, root: str | Path) -> None:
    if not valid_sidecar_id(project_id, "prj_"):
        raise InsightError("invalid project id")
    project_store.load_project(project_id, projects_root=root)


def _path(root: str | Path, project_id: str) -> Path:
    return Path(root) / project_id / _NAME


def _write(root: str | Path, board: InsightBoard) -> None:
    path = _path(root, board.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(board.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise InsightError("timestamp must be ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InsightError("timestamp must be ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise InsightError("timestamp must include timezone")
    return value


__all__ = [
    "ALLOWED_KINDS",
    "InsightBoard",
    "InsightError",
    "PROTECTED_KINDS",
    "Suggestion",
    "decide_suggestion",
    "generate_suggestions",
    "list_all_insights",
    "load_insights",
]
