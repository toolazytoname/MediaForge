"""Local WeChat Official Account roster. Secrets stay in gitignored files."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pipeline.env_keys import mask
from pipeline.publishers.wechat_mp import load_wechat_credentials


DEFAULT_SECRETS_ROOT = Path("secrets")
_REGISTRY_NAME = "wechat_accounts.json"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class WechatAccountError(ValueError):
    """Account id or roster file is invalid."""


@dataclass(frozen=True)
class WechatAccount:
    id: str
    label: str
    app_id_masked: str


def list_accounts(*, secrets_root: str | Path = DEFAULT_SECRETS_ROOT) -> tuple[WechatAccount, ...]:
    root = Path(secrets_root)
    seen: dict[str, str] = {}
    for item in _read_registry(root):
        seen[_account_id(item["id"])] = _label(item.get("label") or item["id"])
    main = root / "wechat_mp_main.json"
    if main.is_file() and "main" not in seen:
        seen["main"] = "主账号"
    accounts: list[WechatAccount] = []
    for account_id, label in seen.items():
        path = _credential_file(root, account_id)
        if not path.is_file():
            continue
        try:
            app_id, _secret = load_wechat_credentials(path)
        except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError):
            continue
        accounts.append(WechatAccount(id=account_id, label=label, app_id_masked=mask(app_id)))
    return tuple(accounts)


def upsert_account(
    *,
    account_id: str,
    label: str,
    app_id: str,
    app_secret: str,
    secrets_root: str | Path = DEFAULT_SECRETS_ROOT,
) -> WechatAccount:
    root = Path(secrets_root)
    account_id = _account_id(account_id)
    label = _label(label)
    if not isinstance(app_id, str) or not app_id.strip() or not isinstance(app_secret, str) or not app_secret.strip():
        raise WechatAccountError("app_id and app_secret must be non-empty text")
    root.mkdir(parents=True, exist_ok=True)
    path = _credential_file(root, account_id)
    _write_secret(path, {"app_id": app_id.strip(), "app_secret": app_secret.strip()})
    registry = [item for item in _read_registry(root) if item.get("id") != account_id]
    registry.append({"id": account_id, "label": label})
    _write_secret(root / _REGISTRY_NAME, {"accounts": registry})
    return WechatAccount(id=account_id, label=label, app_id_masked=mask(app_id.strip()))


def delete_account(account_id: str, *, secrets_root: str | Path = DEFAULT_SECRETS_ROOT) -> None:
    root = Path(secrets_root)
    account_id = _account_id(account_id)
    _credential_file(root, account_id).unlink(missing_ok=True)
    registry = [item for item in _read_registry(root) if item.get("id") != account_id]
    if registry:
        _write_secret(root / _REGISTRY_NAME, {"accounts": registry})
    else:
        (root / _REGISTRY_NAME).unlink(missing_ok=True)


def credentials_path(account_id: str, *, secrets_root: str | Path = DEFAULT_SECRETS_ROOT) -> Path:
    root = Path(secrets_root)
    account_id = _account_id(account_id)
    path = _credential_file(root, account_id)
    if not path.is_file():
        raise WechatAccountError(f"wechat account not found: {account_id}")
    return path


def account_label(account_id: str, *, secrets_root: str | Path = DEFAULT_SECRETS_ROOT) -> str:
    for item in list_accounts(secrets_root=secrets_root):
        if item.id == account_id:
            return item.label
    return account_id


def resolve_account_id(
    requested: str | None, *, secrets_root: str | Path = DEFAULT_SECRETS_ROOT,
) -> str:
    items = list_accounts(secrets_root=secrets_root)
    if requested and requested.strip():
        account_id = _account_id(requested)
        if any(item.id == account_id for item in items):
            return account_id
        raise WechatAccountError(f"wechat account not found: {account_id}")
    if len(items) == 1:
        return items[0].id
    if not items:
        raise WechatAccountError("还没有保存公众号账号。先到设置里添加。")
    raise WechatAccountError("绑定了多个公众号，请先选择要送进哪一个。")


def _read_registry(root: Path) -> list[dict[str, Any]]:
    path = root / _REGISTRY_NAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WechatAccountError("wechat account roster is unreadable") from error
    raw = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise WechatAccountError("wechat account roster is invalid")
    return [item for item in raw if isinstance(item, dict)]


def _credential_file(root: Path, account_id: str) -> Path:
    return root / f"wechat_mp_{account_id}.json"


def _account_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise WechatAccountError("account id must be like main or life_01")
    return value


def _label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WechatAccountError("account label must be non-empty text")
    return value.strip()[:40]


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
