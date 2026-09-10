"""Account creative profiles as sidecars. Secrets stay in secrets/; this file
only stores a relative credentials_ref. operations_enabled defaults to False.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from pipeline.utils.ids import new_id
from pipeline.utils.sidecar_ids import valid_sidecar_id


DEFAULT_ACCOUNTS_ROOT = Path("output/accounts")
_MANIFEST_NAME = "account.json"
_PLATFORMS = frozenset({"wechat_mp", "toutiao"})
_CREATION_MODES = frozenset({"manual", "assisted", "auto"})
_DELIVERY_TARGETS = frozenset({"draft", "direct", "export"})


class AccountProfileError(ValueError):
    """Account profile missing, corrupt, or outside the v0 contract."""


@dataclass(frozen=True)
class AccountProfile:
    id: str
    platform: str
    display_name: str
    positioning: str
    audience: str
    style: str
    references: tuple[str, ...]
    creation_mode: str
    frequency: str
    timezone: str
    budget_usd: float
    delivery_target: str
    operations_enabled: bool
    credentials_ref: str | None
    config_account_id: str
    created_at: str
    updated_at: str


def create_profile(
    *,
    platform: str,
    display_name: str,
    positioning: str,
    audience: str,
    style: str,
    references: Iterable[str],
    creation_mode: str,
    frequency: str,
    timezone: str,
    budget_usd: float,
    delivery_target: str,
    config_account_id: str,
    now: str,
    credentials_ref: str | None = None,
    operations_enabled: bool = False,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    profile_id: str | None = None,
) -> AccountProfile:
    profile = AccountProfile(
        id=profile_id or new_id("acc"),
        platform=_platform(platform),
        display_name=_required("display_name", display_name),
        positioning=_required("positioning", positioning),
        audience=_required("audience", audience),
        style=_required("style", style),
        references=_references(references),
        creation_mode=_one_of("creation_mode", creation_mode, _CREATION_MODES),
        frequency=_required("frequency", frequency),
        timezone=_required("timezone", timezone) if timezone else "Asia/Shanghai",
        budget_usd=_budget(budget_usd),
        delivery_target=_one_of("delivery_target", delivery_target, _DELIVERY_TARGETS),
        operations_enabled=False if operations_enabled is False else bool(operations_enabled),
        credentials_ref=_credentials_ref(credentials_ref),
        config_account_id=_required("config_account_id", config_account_id),
        created_at=_timestamp("created_at", now),
        updated_at=_timestamp("updated_at", now),
    )
    _account_id(profile.id)
    path = _manifest_path(accounts_root, profile.id)
    if path.exists():
        raise AccountProfileError(f"account already exists: {profile.id}")
    _write_manifest(path, profile)
    return profile


def load_profile(
    account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> AccountProfile:
    path = _manifest_path(accounts_root, account_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AccountProfileError(f"account not found: {account_id}") from exc
    except json.JSONDecodeError as exc:
        raise AccountProfileError(f"invalid account JSON: {account_id}") from exc
    return _from_payload(payload, expected_id=account_id)


def list_profiles(
    *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> tuple[AccountProfile, ...]:
    root = Path(accounts_root)
    if not root.exists():
        return ()
    profiles = [
        load_profile(path.parent.name, accounts_root=root)
        for path in root.glob(f"*/{_MANIFEST_NAME}")
    ]
    return tuple(sorted(profiles, key=lambda item: item.updated_at, reverse=True))


def update_profile(
    profile: AccountProfile,
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    **changes: Any,
) -> AccountProfile:
    allowed = set(AccountProfile.__dataclass_fields__) - {"id", "created_at", "updated_at"}
    unknown = set(changes) - allowed
    if unknown:
        raise AccountProfileError(f"cannot update fields: {sorted(unknown)}")
    data = asdict(profile)
    data.update(changes)
    updated = _from_payload(data, expected_id=profile.id)
    updated = replace(updated, updated_at=_timestamp("updated_at", now))
    _write_manifest(_manifest_path(accounts_root, updated.id), updated)
    return updated


def import_platform_accounts(
    platforms: Mapping[str, Any],
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> tuple[AccountProfile, ...]:
    """Create missing profiles from config wechat_mp/toutiao accounts.

    Never sets operations_enabled. Existing platform+config_account_id pairs are reused.
    """
    existing = list_profiles(accounts_root=accounts_root)
    by_key = {(item.platform, item.config_account_id): item for item in existing}
    created: list[AccountProfile] = []
    for platform in ("wechat_mp", "toutiao"):
        plat = platforms.get(platform) if hasattr(platforms, "get") else getattr(platforms, platform, None)
        if plat is None:
            continue
        for account in getattr(plat, "accounts", None) or []:
            key = (platform, account.id)
            if key in by_key:
                created.append(by_key[key])
                continue
            creds = getattr(account, "credentials", None) or getattr(account, "cookies", None)
            profile = create_profile(
                platform=platform,
                display_name=account.id,
                positioning="待确认",
                audience="待确认",
                style="待确认",
                references=(),
                creation_mode="assisted",
                frequency="off",
                timezone="Asia/Shanghai",
                budget_usd=0.0,
                delivery_target="draft",
                credentials_ref=str(creds) if creds else None,
                config_account_id=account.id,
                operations_enabled=False,
                now=now,
                accounts_root=accounts_root,
            )
            by_key[key] = profile
            created.append(profile)
    return tuple(created)


def _manifest_path(root: str | Path, account_id: str) -> Path:
    return Path(root) / _account_id(account_id) / _MANIFEST_NAME


def _account_id(value: Any) -> str:
    if not valid_sidecar_id(value, "acc_"):
        raise AccountProfileError(f"invalid account id: {value!r}")
    return value


def _write_manifest(path: Path, profile: AccountProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    payload = asdict(profile)
    payload["references"] = list(profile.references)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _from_payload(payload: Any, *, expected_id: str) -> AccountProfile:
    if not isinstance(payload, dict):
        raise AccountProfileError("account manifest must be an object")
    fields = set(AccountProfile.__dataclass_fields__)
    if set(payload) != fields:
        raise AccountProfileError("account manifest has missing or unknown fields")
    if payload.get("id") != expected_id:
        raise AccountProfileError("account manifest id does not match its directory")
    profile = AccountProfile(
        id=_account_id(payload["id"]),
        platform=_platform(payload["platform"]),
        display_name=_required("display_name", payload["display_name"]),
        positioning=_required("positioning", payload["positioning"]),
        audience=_required("audience", payload["audience"]),
        style=_required("style", payload["style"]),
        references=_references(payload["references"]),
        creation_mode=_one_of("creation_mode", payload["creation_mode"], _CREATION_MODES),
        frequency=_required("frequency", payload["frequency"]),
        timezone=_required("timezone", payload["timezone"]),
        budget_usd=_budget(payload["budget_usd"]),
        delivery_target=_one_of("delivery_target", payload["delivery_target"], _DELIVERY_TARGETS),
        operations_enabled=bool(payload["operations_enabled"]),
        credentials_ref=_credentials_ref(payload["credentials_ref"]),
        config_account_id=_required("config_account_id", payload["config_account_id"]),
        created_at=_timestamp("created_at", payload["created_at"]),
        updated_at=_timestamp("updated_at", payload["updated_at"]),
    )
    if datetime.fromisoformat(profile.updated_at) < datetime.fromisoformat(profile.created_at):
        raise AccountProfileError("updated_at cannot be earlier than created_at")
    return profile


def _required(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountProfileError(f"{name} must be a non-empty string")
    return value.strip()


def _platform(value: Any) -> str:
    return _one_of("platform", value, _PLATFORMS)


def _one_of(name: str, value: Any, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise AccountProfileError(f"invalid {name}: {value!r}")
    return value


def _references(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise AccountProfileError("references must be a list of strings")
    refs = tuple(_required("references item", item) for item in values)
    if len(set(refs)) != len(refs):
        raise AccountProfileError("references cannot contain duplicates")
    return refs


def _budget(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccountProfileError("budget_usd must be a number")
    if value < 0:
        raise AccountProfileError("budget_usd cannot be negative")
    return float(value)


def _credentials_ref(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise AccountProfileError("credentials_ref must be a string")
    text = value.strip().replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or not text.startswith("secrets/"):
        raise AccountProfileError("credentials_ref must be a relative path under secrets/")
    return text


def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise AccountProfileError(f"{name} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AccountProfileError(f"{name} must be an ISO8601 string") from exc
    if parsed.tzinfo is None:
        raise AccountProfileError(f"{name} must include a timezone")
    return value


__all__ = [
    "AccountProfile",
    "AccountProfileError",
    "DEFAULT_ACCOUNTS_ROOT",
    "create_profile",
    "import_platform_accounts",
    "list_profiles",
    "load_profile",
    "update_profile",
]
