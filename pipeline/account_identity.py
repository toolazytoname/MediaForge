"""Resolve a platform account by explicit id. Never silently use accounts[0]
when more than one account exists.
"""
from __future__ import annotations

from typing import Any


class AccountIdentityError(ValueError):
    """Caller did not name a unique account."""

    def __init__(self, message: str, *, code: str = "account_id_required"):
        super().__init__(message)
        self.code = code


def resolve_platform_account(plat: Any, *, account_id: str | None) -> Any:
    accounts = list(getattr(plat, "accounts", None) or [])
    if account_id:
        for account in accounts:
            if getattr(account, "id", None) == account_id:
                return account
        raise AccountIdentityError(
            f"unknown account_id {account_id!r}", code="account_not_found",
        )
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise AccountIdentityError("no account configured", code="account_missing")
    raise AccountIdentityError(
        "account_id required when multiple accounts exist",
        code="account_id_required",
    )


def account_credentials_path(account: Any) -> str:
    creds = getattr(account, "credentials", None) or getattr(account, "cookies", None)
    if not creds:
        raise AccountIdentityError("account is missing credentials", code="account_missing")
    return str(creds)


__all__ = [
    "AccountIdentityError",
    "account_credentials_path",
    "resolve_platform_account",
]
