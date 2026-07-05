"""utils/errors.py 单元测试。

覆盖：
  - PipelineError 基类
  - IllegalTransition / StaleState 属性（M0-2）
  - TECH_SPEC §7 全部异常类（SourceError / CreateError / GateError /
    PublishError / BudgetExceeded）继承 PipelineError
  - BudgetExceeded 携带 used_usd / limit_usd 属性
"""
from __future__ import annotations

import pytest

from pipeline.utils.errors import (
    BudgetExceeded,
    CreateError,
    GateError,
    IllegalTransition,
    PipelineError,
    PublishError,
    SourceError,
    StaleState,
)


# ── 全部异常继承 PipelineError ───────────────────────────

ALL_EXCEPTIONS = [
    IllegalTransition,
    StaleState,
    SourceError,
    CreateError,
    GateError,
    PublishError,
    BudgetExceeded,
]


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_inherits_pipeline_error(exc_cls):
    assert issubclass(exc_cls, PipelineError)


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_is_exception_subclass(exc_cls):
    assert issubclass(exc_cls, Exception)


# ── IllegalTransition / StaleState 属性（M0-2 仍生效） ────

def test_illegal_transition_attributes():
    e = IllegalTransition("topics", "raw", "consumed")
    assert e.table == "topics"
    assert e.from_status == "raw"
    assert e.to_status == "consumed"
    assert "topics" in str(e)
    assert "raw" in str(e)
    assert "consumed" in str(e)


def test_stale_state_attributes_with_actual():
    e = StaleState("topics", "t_x", "raw", "consumed")
    assert e.table == "topics"
    assert e.row_id == "t_x"
    assert e.expected_status == "raw"
    assert e.actual_status == "consumed"


def test_stale_state_attributes_no_actual():
    """行不存在时 actual=None，str 不含 actual= 段。"""
    e = StaleState("topics", "t_x", "raw", None)
    assert e.actual_status is None
    assert "actual=" not in str(e)


# ── SourceError / CreateError / GateError / PublishError ─

def test_source_error_is_pipeline_error():
    e = SourceError("rss:hn timeout")
    assert isinstance(e, PipelineError)
    assert "rss:hn timeout" in str(e)


def test_create_error_is_pipeline_error():
    e = CreateError("LLM call failed: 500")
    assert isinstance(e, PipelineError)


def test_gate_error_is_pipeline_error():
    e = GateError("anchors/ directory missing")
    assert isinstance(e, PipelineError)


def test_publish_error_is_pipeline_error():
    e = PublishError("cookie expired for xiaohongshu main")
    assert isinstance(e, PipelineError)


# ── BudgetExceeded：M0-3 新增，HARD_PARTS §4 ──────────────

def test_budget_exceeded_attributes():
    e = BudgetExceeded(stage="score", used_usd=82.5, limit_usd=80.0)
    assert e.stage == "score"
    assert e.used_usd == 82.5
    assert e.limit_usd == 80.0
    assert isinstance(e, PipelineError)


def test_budget_exceeded_str_contains_amounts():
    e = BudgetExceeded(stage="create", used_usd=120.0, limit_usd=80.0)
    msg = str(e)
    assert "stage=create" in msg
    assert "120" in msg or "used" in msg
    assert "80" in msg or "limit" in msg


def test_budget_exceeded_catchable_as_pipeline_error():
    """编排层 except PipelineError 必须能 catch BudgetExceeded。"""
    try:
        raise BudgetExceeded("gate", 100.0, 80.0)
    except PipelineError as e:
        assert e.stage == "gate"