"""Atomic account spending and reservations in the existing spend sidecar."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any
import json
import math
from pathlib import Path

from pipeline.account_profiles import load_profile
from pipeline.utils.flock import acquire, release


@contextmanager
def _locked(account_id: str, accounts_root: str | Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    load_profile(account_id, accounts_root=accounts_root)  # validate before forming paths
    path = Path(accounts_root) / account_id / 'spend.json'
    lock = path.parent / 'locks' / 'budget.lock'
    acquire(lock)
    try:
        data = json.loads(path.read_text()) if path.exists() else {'spent_usd': 0.0}
        yield path, data
    finally:
        release(lock)


def _write(path: Path, data: dict[str, Any], now: str) -> None:
    payload = {**data, 'updated_at': now}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def reserved_spend(account_id: str, *, accounts_root: str | Path) -> float:
    load_profile(account_id, accounts_root=accounts_root)
    path = Path(accounts_root) / account_id / 'spend.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    return sum(float(v) for v in data.get('reservations', {}).values())


def reserve_spend(account_id: str, *, token: str, amount: float, now: str,
                  accounts_root: str | Path) -> None:
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError('reservation must have a positive finite price')
    with _locked(account_id, accounts_root) as (path, data):
        reservations = data.get('reservations', {})
        existing = {**reservations, **data.get('settled', {})}
        if token in existing:
            if float(existing[token]) != amount:
                raise ValueError('reservation price changed; reconcile original request first')
            return
        profile = load_profile(account_id, accounts_root=accounts_root)
        used = float(data['spent_usd']) + sum(float(v) for v in reservations.values())
        if used + amount > profile.budget_usd + 1e-12:
            raise ValueError('account image budget exceeded')
        _write(path, {**data, 'reservations': {**reservations, token: amount}}, now)


def settle_spend(account_id: str, *, token: str, now: str, accounts_root: str | Path) -> None:
    with _locked(account_id, accounts_root) as (path, data):
        if token in data.get('settled', {}):
            return
        reservations = data.get('reservations', {})
        amount = float(reservations[token])
        _write(path, {**data, 'spent_usd': float(data['spent_usd']) + amount,
                     'reservations': {k: v for k, v in reservations.items() if k != token},
                     'settled': {**data.get('settled', {}), token: amount}}, now)


def add_spend(account_id: str, *, amount: float, now: str, accounts_root: str | Path) -> float:
    if not math.isfinite(amount) or amount < 0:
        raise ValueError('spend must be finite and nonnegative')
    with _locked(account_id, accounts_root) as (path, data):
        total = float(data['spent_usd']) + amount
        _write(path, {**data, 'spent_usd': total}, now)
        return total


def release_reservation(account_id: str, *, token: str, now: str, accounts_root: str | Path) -> None:
    """Only for a failure known to have occurred before calling the provider."""
    with _locked(account_id, accounts_root) as (path, data):
        reservations = data.get('reservations', {})
        _write(path, {**data, 'reservations': {k: v for k, v in reservations.items() if k != token}}, now)
