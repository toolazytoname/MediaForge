"""Account publishing cadence as sidecars + durable_jobs. No SQLite schema change."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.account_profiles import DEFAULT_ACCOUNTS_ROOT, load_profile
from pipeline.jobs import store as jobs_store
from pipeline.utils.sidecar_ids import valid_sidecar_id

_PLAN_NAME = "plan.json"
_SPEND_NAME = "spend.json"
_FREQ_RE = re.compile(r"^(\d+)/(day|week)$")


class AccountPlanError(ValueError):
    """Plan sidecar missing, corrupt, or cadence cannot be scheduled."""


@dataclass(frozen=True)
class PlannedSlot:
    local_date: str
    slot: str
    scheduled_at: str
    status: str


@dataclass(frozen=True)
class AccountPlan:
    account_id: str
    timezone: str
    slots: tuple[PlannedSlot, ...]
    paused: bool
    pause_reason: str | None
    spent_usd: float
    updated_at: str


@dataclass(frozen=True)
class ScheduleResult:
    account_id: str
    jobs: tuple[Any, ...]
    plan: AccountPlan


def load_plan(account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT) -> AccountPlan:
    path = _plan_path(accounts_root, account_id)
    if not path.exists():
        profile = load_profile(account_id, accounts_root=accounts_root)
        return AccountPlan(
            account_id=account_id, timezone=profile.timezone, slots=(),
            paused=False, pause_reason=None, spent_usd=_load_spend(account_id, accounts_root),
            updated_at=profile.updated_at,
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    slots = tuple(PlannedSlot(**item) for item in payload.get("slots") or ())
    return AccountPlan(
        account_id=account_id,
        timezone=str(payload.get("timezone") or "Asia/Shanghai"),
        slots=slots,
        paused=bool(payload.get("paused")),
        pause_reason=payload.get("pause_reason"),
        spent_usd=float(payload.get("spent_usd") or 0),
        updated_at=str(payload["updated_at"]),
    )


def load_spend(
    account_id: str, *, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> float:
    return _load_spend(account_id, accounts_root)


def record_spend(
    account_id: str, *, amount: float, now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> float:
    if not valid_sidecar_id(account_id, "acc_"):
        raise AccountPlanError(f"invalid account id: {account_id!r}")
    from pipeline.account_budget import add_spend
    return add_spend(account_id, amount=amount, now=now, accounts_root=accounts_root)


def schedule_account(
    conn,
    account_id: str,
    *,
    now: str,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> ScheduleResult:
    profile = load_profile(account_id, accounts_root=accounts_root)
    spent = _load_spend(account_id, accounts_root)
    if not profile.operations_enabled:
        plan = AccountPlan(
            account_id=account_id, timezone=profile.timezone, slots=(),
            paused=False, pause_reason=None, spent_usd=spent, updated_at=now,
        )
        _save_plan(accounts_root, plan)
        return ScheduleResult(account_id, (), plan)
    from pipeline.account_budget import reserved_spend
    if spent + reserved_spend(account_id, accounts_root=accounts_root) >= profile.budget_usd:
        plan = AccountPlan(
            account_id=account_id, timezone=profile.timezone, slots=(),
            paused=True, pause_reason="budget exceeded", spent_usd=spent, updated_at=now,
        )
        _save_plan(accounts_root, plan)
        return ScheduleResult(account_id, (), plan)

    slots = _upcoming_slots(profile.frequency, profile.timezone, now)
    jobs = []
    persisted: list[PlannedSlot] = []
    for slot in slots:
        key = f"ops:{account_id}:{slot.local_date}:{slot.slot}"
        existing = jobs_store.get_job_by_key(conn, key)
        if existing is not None:
            jobs.append(existing)
            persisted.append(slot)
            continue
        job = jobs_store.insert_job(
            conn,
            kind="delivery",
            idempotency_key=key,
            request={
                "ops": True,
                "account_id": account_id,
                "slot": slot.slot,
                "local_date": slot.local_date,
                "scheduled_at": slot.scheduled_at,
                "stage": "research",
            },
            engine="ops",
            now=now,
        )
        jobs.append(job)
        persisted.append(slot)
    plan = AccountPlan(
        account_id=account_id, timezone=profile.timezone, slots=tuple(persisted),
        paused=False, pause_reason=None, spent_usd=spent, updated_at=now,
    )
    _save_plan(accounts_root, plan)
    return ScheduleResult(account_id, tuple(jobs), plan)


def list_upcoming_plans(*, accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT) -> tuple[AccountPlan, ...]:
    root = Path(accounts_root)
    if not root.exists():
        return ()
    plans = []
    for path in root.glob(f"*/{_PLAN_NAME}"):
        account_id = path.parent.name
        if not valid_sidecar_id(account_id, "acc_"):
            continue
        plan = load_plan(account_id, accounts_root=root)
        if plan.slots and not plan.paused:
            plans.append(plan)
    return tuple(plans)


def _upcoming_slots(frequency: str, timezone: str, now: str) -> tuple[PlannedSlot, ...]:
    freq = (frequency or "off").strip().lower()
    if freq in {"off", "0/week", "0/day"}:
        return ()
    tz = ZoneInfo(timezone or "Asia/Shanghai")
    current = datetime.fromisoformat(now)
    if current.tzinfo is None:
        raise AccountPlanError("now must include a timezone")
    local = current.astimezone(tz)
    match = _FREQ_RE.match(freq)
    if freq == "daily":
        count, unit = 1, "day"
    elif match:
        count, unit = int(match.group(1)), match.group(2)
    else:
        raise AccountPlanError(f"invalid frequency: {frequency!r}")
    if count < 1:
        return ()
    slots: list[PlannedSlot] = []
    if unit == "day":
        for index in range(min(count, 3)):
            hour = 9 + index * 4
            when = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if when <= local:
                when = when + timedelta(days=1)
            slots.append(_slot(when, f"slot{index + 1}"))
    else:
        monday = local.date() - timedelta(days=local.weekday())
        hours = (9, 16, 20)
        for index in range(min(count, 3)):
            day = monday + timedelta(days=min(index, 4))
            hour = hours[index % len(hours)]
            when = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
            if when <= local:
                when = when + timedelta(days=7)
            slots.append(_slot(when, f"slot{index + 1}"))
    return tuple(slots)


def _slot(when: datetime, name: str) -> PlannedSlot:
    return PlannedSlot(
        local_date=when.date().isoformat(),
        slot=name,
        scheduled_at=when.astimezone(ZoneInfo("UTC")).isoformat(),
        status="scheduled",
    )


def _load_spend(account_id: str, accounts_root: str | Path) -> float:
    path = Path(accounts_root) / account_id / _SPEND_NAME
    if not path.exists():
        return 0.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    return float(payload.get("spent_usd") or 0)


def _save_plan(accounts_root: str | Path, plan: AccountPlan) -> None:
    payload = asdict(plan)
    payload["slots"] = [asdict(item) for item in plan.slots]
    _write(_plan_path(accounts_root, plan.account_id), payload)


def _plan_path(root: str | Path, account_id: str) -> Path:
    if not valid_sidecar_id(account_id, "acc_"):
        raise AccountPlanError(f"invalid account id: {account_id!r}")
    return Path(root) / account_id / _PLAN_NAME


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "AccountPlan",
    "AccountPlanError",
    "PlannedSlot",
    "ScheduleResult",
    "list_upcoming_plans",
    "load_plan",
    "load_spend",
    "record_spend",
    "schedule_account",
]
