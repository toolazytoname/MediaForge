"""Autonomy budget gate (LAZY-88 / RFC §5.7).

Per-project spend is compared to the autonomy preset cap. Unknown or
unpriced costs refuse to count as 0 and pause the run in a recoverable
state. Monthly BudgetExceeded remains the global hard stop.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pipeline.autonomy import AutonomyPolicy
from pipeline.utils.errors import BudgetExceeded, UnpricedModelError


class BudgetPaused(BudgetExceeded):
    """Project-level autonomy cap hit; the project can resume after a reset."""

    def __init__(self, project_id: str, used_usd: float, limit_usd: float) -> None:
        super().__init__(stage=f"project:{project_id}", used_usd=used_usd, limit_usd=limit_usd)
        self.project_id = project_id
        self.code = "budget_paused"


@dataclass(frozen=True)
class ProjectSpend:
    project_id: str
    known_usd: float
    unpriced: bool

    @property
    def usable(self) -> bool:
        return not self.unpriced


def project_spend(conn: sqlite3.Connection, project_id: str) -> ProjectSpend:
    """Sum llm_calls billed to this project. NULL cost_usd is unpriced, not 0."""
    rows = conn.execute(
        """
        SELECT cost_usd FROM llm_calls
        WHERE ref_id = ? OR ref_id LIKE ?
        """,
        (project_id, f"{project_id}:%"),
    ).fetchall()
    known = 0.0
    unpriced = False
    for row in rows:
        value = row["cost_usd"]
        if value is None:
            unpriced = True
            continue
        known += float(value)
    return ProjectSpend(project_id, known, unpriced)


def require_priced(cost_usd: float | None, *, model: str) -> float:
    """Refuse to treat unknown/unpriced cost as 0."""
    if cost_usd is None:
        raise UnpricedModelError(model)
    if cost_usd == 0 and model in {"unknown", "unpriced", ""}:
        raise UnpricedModelError(model or "unknown")
    if cost_usd < 0:
        raise UnpricedModelError(model)
    return float(cost_usd)


def enforce_autonomy_budget(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    policy: AutonomyPolicy,
    estimated_cost: float | None,
    model: str = "scheduled",
) -> ProjectSpend:
    """Raise if this project cannot spend ``estimated_cost`` under its cap.

    ``estimated_cost`` must be a priced number. A scheduled path that
    performs no model call may pass 0.0 (known zero). Passing None or an
    unpriced existing row pauses the project instead of counting 0.
    """
    spend = project_spend(conn, project_id)
    if spend.unpriced:
        raise UnpricedModelError(f"{project_id}:unpriced")
    priced = require_priced(estimated_cost, model=model)
    limit = float(policy.llm_budget_usd)
    if spend.known_usd + priced > limit:
        raise BudgetPaused(project_id, spend.known_usd + priced, limit)
    return spend


__all__ = [
    "BudgetPaused",
    "ProjectSpend",
    "enforce_autonomy_budget",
    "project_spend",
    "require_priced",
]
