"""Account operations authorization, separate from project autonomy and human approval."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pipeline.account_bindings import AccountBindingError, load_bindings, require_binding
from pipeline.account_profiles import DEFAULT_ACCOUNTS_ROOT, load_profile
from pipeline.autonomy import load_policy
from pipeline.projects import DEFAULT_PROJECTS_ROOT
from pipeline.utils.sidecar_ids import valid_sidecar_id


_AUTH_NAME = "authorization.json"
_QUALITY_NAME = "quality_results.json"


class AuthorizationError(ValueError):
    """Account is not allowed to deliver this work automatically or as bound."""

    def __init__(self, message: str, *, code: str = "account_not_authorized"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AccountAuthorization:
    account_id: str
    version: int
    operations_enabled: bool
    allow_draft: bool
    allow_direct: bool
    quality_floor: float
    actor: str
    enabled_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class QualityResult:
    project_id: str
    score: float
    verdict: str
    human_verified: bool
    recorded_at: str


def save_authorization(
    account_id: str,
    *,
    actor: str,
    now: str,
    operations_enabled: bool,
    allow_draft: bool,
    allow_direct: bool,
    quality_floor: float,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> AccountAuthorization:
    if not valid_sidecar_id(account_id, "acc_"):
        raise AuthorizationError(f"invalid account id: {account_id!r}")
    load_profile(account_id, accounts_root=accounts_root)
    try:
        current = load_authorization(account_id, accounts_root=accounts_root)
        version = current.version + 1
        created_at = current.created_at
    except AuthorizationError:
        version = 1
        created_at = now
    auth = AccountAuthorization(
        account_id=account_id,
        version=version,
        operations_enabled=bool(operations_enabled),
        allow_draft=bool(allow_draft),
        allow_direct=bool(allow_direct),
        quality_floor=float(quality_floor),
        actor=actor.strip() or "unknown",
        enabled_at=now if operations_enabled else None,
        created_at=_timestamp(created_at),
        updated_at=_timestamp(now),
    )
    _write(_auth_path(accounts_root, account_id), asdict(auth))
    return auth


def load_authorization(
    account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> AccountAuthorization:
    path = _auth_path(accounts_root, account_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthorizationError(f"no authorization for {account_id}") from exc
    return AccountAuthorization(
        account_id=account_id,
        version=int(payload["version"]),
        operations_enabled=bool(payload["operations_enabled"]),
        allow_draft=bool(payload["allow_draft"]),
        allow_direct=bool(payload["allow_direct"]),
        quality_floor=float(payload["quality_floor"]),
        actor=str(payload["actor"]),
        enabled_at=payload.get("enabled_at"),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
    )


def load_quality_results(
    account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> tuple[QualityResult, ...]:
    path = Path(accounts_root) / account_id / _QUALITY_NAME
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return ()
    items = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        items.append(QualityResult(
            project_id=str(item.get("project_id") or ""),
            score=float(item.get("score") or 0),
            verdict=str(item.get("verdict") or ""),
            human_verified=bool(item.get("human_verified")),
            recorded_at=str(item.get("recorded_at") or ""),
        ))
    return tuple(items)


def record_quality_result(
    account_id: str,
    *,
    project_id: str,
    score: float,
    verdict: str,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> QualityResult:
    result = QualityResult(
        project_id=project_id,
        score=float(score),
        verdict=verdict,
        human_verified=False,
        recorded_at=_timestamp(now),
    )
    path = Path(accounts_root) / account_id / _QUALITY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    existing.append(asdict(result))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return result


def assert_account_may_deliver(
    project_id: str,
    *,
    platform: str,
    account_id: str,
    mode: Literal["preview", "export", "draft", "direct"],
    path: Literal["human", "auto"],
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> None:
    _project, policy = load_policy(project_id, projects_root=projects_root)
    if mode == "direct" and "direct" not in policy.delivery_modes:
        raise AuthorizationError(
            f"{policy.label}模式不能直发", code="autonomy_forbids_direct",
        )
    if valid_sidecar_id(account_id, "acc_"):
        try:
            require_binding(
                project_id, platform=platform, account_id=account_id,
                projects_root=projects_root,
            )
        except AccountBindingError as error:
            raise AuthorizationError(str(error), code="account_not_bound") from error
    else:
        try:
            bindings = load_bindings(project_id, projects_root=projects_root)
        except AccountBindingError:
            bindings = None
        if bindings is not None:
            raise AuthorizationError(
                f"project {project_id} {platform} does not match account {account_id}",
                code="account_not_bound",
            )
    if path == "human":
        return
    try:
        auth = load_authorization(account_id, accounts_root=accounts_root)
    except AuthorizationError as error:
        raise AuthorizationError(
            f"operations disabled for {account_id}", code="operations_disabled",
        ) from error
    if not auth.operations_enabled or not auth.enabled_at:
        raise AuthorizationError(
            f"operations disabled for {account_id}", code="operations_disabled",
        )
    if auth.version < 1:
        raise AuthorizationError("authorization version is invalid", code="auth_version_invalid")
    if mode == "draft" and not auth.allow_draft:
        raise AuthorizationError("account is not authorized for draft", code="draft_not_allowed")
    if mode == "direct" and not auth.allow_direct:
        raise AuthorizationError("account is not authorized for direct", code="direct_not_allowed")
    results = [
        item for item in load_quality_results(account_id, accounts_root=accounts_root)
        if item.project_id == project_id
    ]
    if not results:
        raise AuthorizationError("no quality result for automatic delivery", code="quality_missing")
    latest = results[-1]
    if latest.score < auth.quality_floor:
        raise AuthorizationError(
            f"quality {latest.score} below floor {auth.quality_floor}",
            code="quality_below_floor",
        )


def _auth_path(root: str | Path, account_id: str) -> Path:
    return Path(root) / account_id / _AUTH_NAME


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise AuthorizationError("timestamp must include a timezone")
    return value


__all__ = [
    "AccountAuthorization",
    "AuthorizationError",
    "QualityResult",
    "assert_account_may_deliver",
    "load_authorization",
    "load_quality_results",
    "record_quality_result",
    "save_authorization",
]
