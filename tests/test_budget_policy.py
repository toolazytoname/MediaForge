"""LAZY-88: autonomy budget cap and unknown cost pause."""
from __future__ import annotations

import pytest

from pipeline import db, projects
from pipeline.automation import PAUSED_BUDGET, PAUSED_UNPRICED, prepare_project
from pipeline.autonomy import get_policy
from pipeline.budget import BudgetPaused, enforce_autonomy_budget, project_spend, require_priced
from pipeline.utils.errors import UnpricedModelError

_NOW = "2026-08-17T07:00:00+00:00"


def _conn(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    return conn


def test_unknown_cost_is_not_zero():
    with pytest.raises(UnpricedModelError):
        require_priced(None, model="mystery-model")
    with pytest.raises(UnpricedModelError):
        require_priced(0, model="unknown")
    with pytest.raises(UnpricedModelError):
        require_priced(-0.1, model="gpt-test")


def test_over_budget_pauses_recoverably(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(
        title="预算", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="pack", now=_NOW, project_id="prj_budget", projects_root=root,
    )
    conn = _conn(tmp_path)
    db.insert_llm_call(
        conn, stage="create", ref_id="prj_budget", model="fake",
        input_tokens=10, output_tokens=10, cost_usd=9.0, created_at=_NOW,
    )
    policy = get_policy("pack")
    spend = project_spend(conn, "prj_budget")
    assert spend.known_usd == pytest.approx(9.0)
    assert spend.unpriced is False
    with pytest.raises(BudgetPaused) as error:
        enforce_autonomy_budget(
            conn, project_id="prj_budget", policy=policy, estimated_cost=0.0,
        )
    assert error.value.used_usd == pytest.approx(9.0)
    assert error.value.limit_usd == pytest.approx(8.0)

    item = prepare_project(conn, "prj_budget", now=_NOW, actor="cron", projects_root=root)
    assert item.status == PAUSED_BUDGET
    actions = {row["action"] for row in conn.execute("SELECT action FROM audit_events")}
    assert "automation.paused_budget" in actions
    # Recoverable: lowering spend would allow a later run; state is not terminal failed.
    conn.execute("DELETE FROM llm_calls")
    conn.commit()
    resumed = prepare_project(conn, "prj_budget", now=_NOW, actor="cron", projects_root=root)
    assert resumed.status == "awaiting_approval"


def test_unpriced_existing_row_pauses(tmp_path):
    root = tmp_path / "projects"
    projects.create_project(
        title="未知成本", idea="想法", audience="读者", goal="文章", voice="清晰",
        autonomy="pack", now=_NOW, project_id="prj_unpriced", projects_root=root,
    )
    conn = _conn(tmp_path)
    db.insert_llm_call(
        conn, stage="create", ref_id="prj_unpriced", model="mystery",
        input_tokens=1, output_tokens=1, cost_usd=None, created_at=_NOW,
    )
    spend = project_spend(conn, "prj_unpriced")
    assert spend.unpriced is True
    assert spend.known_usd == 0.0
    with pytest.raises(UnpricedModelError):
        enforce_autonomy_budget(
            conn, project_id="prj_unpriced", policy=get_policy("pack"),
            estimated_cost=0.0,
        )
    item = prepare_project(conn, "prj_unpriced", now=_NOW, actor="cron", projects_root=root)
    assert item.status == PAUSED_UNPRICED
    actions = {row["action"] for row in conn.execute("SELECT action FROM audit_events")}
    assert "automation.paused_unpriced" in actions
