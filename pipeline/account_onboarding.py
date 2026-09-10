"""Account positioning suggestions from readable sources + interview.

Suggestions stay on an onboarding sidecar until the user confirms.
Unread URLs are listed; they are never treated as if the text was read.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from pipeline.account_profiles import (
    DEFAULT_ACCOUNTS_ROOT,
    load_profile,
    update_profile,
)
from pipeline.creators.source_fetcher import fetch_text as default_fetch_text
from pipeline.utils.sidecar_ids import valid_sidecar_id


_MANIFEST_NAME = "onboarding.json"
_EXCERPT_LIMIT = 1200


class OnboardingError(ValueError):
    """Onboarding draft missing, incomplete, or already confirmed."""


@dataclass(frozen=True)
class OnboardingDraft:
    account_id: str
    interview: dict[str, str]
    sources: tuple[str, ...]
    read_excerpts: tuple[str, ...]
    unread_sources: tuple[str, ...]
    suggestion: dict[str, str]
    confirmed: bool
    created_at: str
    updated_at: str


def propose_onboarding(
    account_id: str,
    *,
    interview: dict[str, str],
    sources: Iterable[str],
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    fetch_text: Callable[[str], str | None] = default_fetch_text,
    complete_json: Callable[..., Any] | None = None,
) -> OnboardingDraft:
    if not valid_sidecar_id(account_id, "acc_"):
        raise OnboardingError(f"invalid account id: {account_id!r}")
    load_profile(account_id, accounts_root=accounts_root)
    cleaned = _interview(interview)
    urls = tuple(item.strip() for item in sources if isinstance(item, str) and item.strip())
    excerpts: list[str] = []
    unread: list[str] = []
    for url in urls:
        text = fetch_text(url)
        if not text or not text.strip():
            unread.append(url)
            continue
        excerpts.append(text.strip()[:_EXCERPT_LIMIT])
    parser = complete_json or _default_complete_json
    suggestion = parser(
        _prompt(cleaned, excerpts, unread),
        stage="account_onboarding",
        parse=_parse_suggestion,
        ref_id=account_id,
    )
    if not isinstance(suggestion, dict):
        raise OnboardingError("onboarding model must return an object")
    normalized = _parse_suggestion(json.dumps(suggestion, ensure_ascii=False))
    try:
        existing = load_onboarding(account_id, accounts_root=accounts_root)
        created_at = existing.created_at
    except OnboardingError:
        created_at = now
    draft = OnboardingDraft(
        account_id=account_id,
        interview=cleaned,
        sources=urls,
        read_excerpts=tuple(excerpts),
        unread_sources=tuple(unread),
        suggestion=normalized,
        confirmed=False,
        created_at=_timestamp(created_at),
        updated_at=_timestamp(now),
    )
    _write(_path(accounts_root, account_id), draft)
    return draft


def load_onboarding(
    account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> OnboardingDraft:
    path = _path(accounts_root, account_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OnboardingError(f"no onboarding draft for {account_id}") from exc
    except json.JSONDecodeError as exc:
        raise OnboardingError(f"invalid onboarding JSON: {account_id}") from exc
    return _from_payload(payload, expected_id=account_id)


def confirm_onboarding(
    account_id: str,
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> Any:
    draft = load_onboarding(account_id, accounts_root=accounts_root)
    if draft.confirmed:
        raise OnboardingError("onboarding already confirmed")
    _interview(draft.interview)
    profile = load_profile(account_id, accounts_root=accounts_root)
    updated = update_profile(
        profile,
        now=now,
        accounts_root=accounts_root,
        positioning=draft.suggestion["positioning"],
        audience=draft.suggestion["audience"],
        style=draft.suggestion["style"],
        references=draft.sources,
    )
    confirmed = OnboardingDraft(
        account_id=draft.account_id,
        interview=draft.interview,
        sources=draft.sources,
        read_excerpts=draft.read_excerpts,
        unread_sources=draft.unread_sources,
        suggestion=draft.suggestion,
        confirmed=True,
        created_at=draft.created_at,
        updated_at=_timestamp(now),
    )
    _write(_path(accounts_root, account_id), confirmed)
    return updated


def _interview(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise OnboardingError("interview is required")
    viewpoint = str(value.get("viewpoint") or "").strip()
    reader = str(value.get("reader") or "").strip()
    must_not = str(value.get("must_not") or "").strip()
    if not viewpoint or not reader or not must_not:
        raise OnboardingError("interview requires viewpoint, reader, and must_not")
    return {"viewpoint": viewpoint, "reader": reader, "must_not": must_not}


def _prompt(interview: dict[str, str], excerpts: list[str], unread: list[str]) -> str:
    unread_note = (
        "未读来源（不得假装读过）：" + "；".join(unread) if unread else "无未读来源"
    )
    body = "\n\n".join(excerpts) if excerpts else "（没有成功读取的参考正文）"
    return (
        "根据作者访谈和已读摘录，建议账号定位、读者和文风。"
        "只输出 JSON：positioning, audience, style, rationale。"
        "不要编造作者没说过的经历。\n"
        f"观点：{interview['viewpoint']}\n读者：{interview['reader']}\n禁区：{interview['must_not']}\n"
        f"{unread_note}\n已读摘录：\n{body}"
    )


def _parse_suggestion(text: str) -> dict[str, str]:
    payload = json.loads(text) if isinstance(text, str) else text
    if not isinstance(payload, dict):
        raise OnboardingError("suggestion must be an object")
    required = ("positioning", "audience", "style", "rationale")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise OnboardingError(f"suggestion missing {missing}")
    return {key: str(payload[key]).strip() for key in required}


def _default_complete_json(prompt: str, **kwargs: Any) -> dict[str, str]:
    from pipeline.creators.llm import complete_json
    return complete_json(prompt, parse=_parse_suggestion, **kwargs)


def _path(root: str | Path, account_id: str) -> Path:
    return Path(root) / account_id / _MANIFEST_NAME


def _write(path: Path, draft: OnboardingDraft) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = asdict(draft)
    payload["sources"] = list(draft.sources)
    payload["read_excerpts"] = list(draft.read_excerpts)
    payload["unread_sources"] = list(draft.unread_sources)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _from_payload(payload: Any, *, expected_id: str) -> OnboardingDraft:
    if not isinstance(payload, dict):
        raise OnboardingError("onboarding must be an object")
    if payload.get("account_id") != expected_id:
        raise OnboardingError("onboarding account_id does not match directory")
    return OnboardingDraft(
        account_id=expected_id,
        interview=_interview(payload.get("interview")),
        sources=tuple(payload.get("sources") or ()),
        read_excerpts=tuple(payload.get("read_excerpts") or ()),
        unread_sources=tuple(payload.get("unread_sources") or ()),
        suggestion=_parse_suggestion(json.dumps(payload.get("suggestion") or {})),
        confirmed=bool(payload.get("confirmed")),
        created_at=_timestamp(payload.get("created_at")),
        updated_at=_timestamp(payload.get("updated_at")),
    )


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise OnboardingError("timestamp must be an ISO8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise OnboardingError("timestamp must include a timezone")
    return value


__all__ = [
    "OnboardingDraft",
    "OnboardingError",
    "confirm_onboarding",
    "load_onboarding",
    "propose_onboarding",
]
