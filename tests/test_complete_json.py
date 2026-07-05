"""complete_json 通用助手测试（M2-2.5 JSON 自动重试）。

覆盖：
  - 首次返回有效 JSON → 不重试
  - 首次坏 JSON + 二次有效 → 重试一次成功
  - 两次都坏 → 抛原异常
  - 非 JSON 类异常（如 BudgetExceeded / ValueError）不被吞
  - max_retries=0 立即上抛
  - parse 函数 raise CreateError/GateError 也能被捕获重试
  - 真实冒烟：MiniMax 返回坏 JSON 时自动重试（占位测试）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    RetryableError,
    complete_json,
    set_provider,
)
from pipeline.utils.errors import (
    BudgetExceeded,
    CreateError,
    GateError,
    IllegalTransition,
)


class ScriptedProvider(LLMProvider):
    """按调用顺序返回不同 response 的脚本 provider。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append(prompt)
        if not self._responses:
            raise RetryableError("no scripted response")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=100, output_tokens=50,
        )


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


@pytest.fixture(autouse=True)
def reset_provider(tmp_path):
    set_provider(ScriptedProvider([]))
    conn = _open_db(tmp_path)
    llm_mod.init_db_conn(conn)
    yield
    llm_mod.init_db_conn(None)  # type: ignore[arg-type]
    conn.close()
    set_provider(ScriptedProvider([]))


def _parse_json_obj(text: str) -> dict:
    """用于测试的简单 parse：解析为 dict，失败抛 ValueError。"""
    return json.loads(text)


# ── 首次即成功 ─────────────────────────────────────────────

def test_complete_json_first_attempt_succeeds() -> None:
    """首次返回有效 JSON → parse 一次成功，不触发第二次 LLM 调用。"""
    set_provider(ScriptedProvider([json.dumps({"k": "v"})]))

    result = complete_json(
        "prompt",
        stage="test",
        parse=_parse_json_obj,
        model_tier="creative",
        max_tokens=100,
    )
    assert result == {"k": "v"}
    # 只调了 1 次
    assert len(llm_mod._PROVIDER.calls) == 1  # type: ignore[attr-defined]


# ── 首次坏 → 二次好 ───────────────────────────────────────

def test_complete_json_retries_on_bad_json() -> None:
    """首次坏 JSON → 第二次调 LLM（fixup prompt）→ 二次成功。"""
    set_provider(ScriptedProvider([
        "not json {",  # 第一次坏
        json.dumps({"k": "v"}),  # 第二次好
    ]))

    result = complete_json(
        "prompt",
        stage="test",
        parse=_parse_json_obj,
        model_tier="creative",
        max_tokens=100,
    )
    assert result == {"k": "v"}
    # 调了 2 次
    assert len(llm_mod._PROVIDER.calls) == 2  # type: ignore[attr-defined]
    # 第二次 prompt 包含「系统提示」（fixup 模板）
    second_prompt = llm_mod._PROVIDER.calls[1]  # type: ignore[attr-defined]
    assert "系统提示" in second_prompt
    assert "not json" in second_prompt  # 上次坏输出被反馈
    assert "JSONDecodeError" in second_prompt


# ── 两次都坏 ───────────────────────────────────────────────

def test_complete_json_propagates_last_error_after_retry_exhausted() -> None:
    """两次都坏 → 抛最后一次的异常。"""
    set_provider(ScriptedProvider([
        "garbage 1",
        "garbage 2",
    ]))

    with pytest.raises(json.JSONDecodeError):
        complete_json(
            "prompt",
            stage="test",
            parse=_parse_json_obj,
            model_tier="creative",
            max_tokens=100,
            max_retries=1,
        )
    # 调了 2 次
    assert len(llm_mod._PROVIDER.calls) == 2  # type: ignore[attr-defined]


# ── max_retries=0 ─────────────────────────────────────────

def test_complete_json_no_retry_when_max_retries_zero() -> None:
    """max_retries=0 → 首次坏直接抛。"""
    set_provider(ScriptedProvider(["bad"]))

    with pytest.raises(json.JSONDecodeError):
        complete_json(
            "prompt",
            stage="test",
            parse=_parse_json_obj,
            model_tier="creative",
            max_tokens=100,
            max_retries=0,
        )
    assert len(llm_mod._PROVIDER.calls) == 1  # type: ignore[attr-defined]


# ── parse 抛 PipelineError ─────────────────────────────────

def test_complete_json_catches_create_error_and_retries() -> None:
    """parse 抛 CreateError → complete_json 视为可重试。"""
    def parse_strict(text: str) -> dict:
        obj = json.loads(text)
        if "missing_key" not in obj:
            raise CreateError("missing required key")
        return obj

    set_provider(ScriptedProvider([
        json.dumps({"wrong_key": "x"}),  # 缺 key → CreateError
        json.dumps({"missing_key": "y"}),  # OK
    ]))

    result = complete_json(
        "prompt",
        stage="test",
        parse=parse_strict,
        model_tier="creative",
        max_tokens=100,
    )
    assert result == {"missing_key": "y"}


def test_complete_json_catches_gate_error_and_retries() -> None:
    """parse 抛 GateError → 同样重试。"""
    def parse_strict(text: str) -> dict:
        obj = json.loads(text)
        if "ok" not in obj:
            raise GateError("missing ok")
        return obj

    set_provider(ScriptedProvider([
        json.dumps({"no": "x"}),
        json.dumps({"ok": "y"}),
    ]))

    result = complete_json(
        "prompt", stage="test", parse=parse_strict,
        model_tier="creative", max_tokens=100,
    )
    assert result == {"ok": "y"}


# ── 不应被捕获的异常 ──────────────────────────────────────

def test_complete_json_propagates_budget_exceeded() -> None:
    """BudgetExceeded 是系统性错误 → 必须原样上抛，不重试。"""
    set_provider(ScriptedProvider([]))  # 不会触发；改成 raise BudgetExceeded

    class BudgetProvider(LLMProvider):
        def call(self, prompt, model, max_tokens):
            raise BudgetExceeded(stage="test", used_usd=100.0, limit_usd=5.0)

    set_provider(BudgetProvider())

    with pytest.raises(BudgetExceeded):
        complete_json(
            "prompt", stage="test", parse=_parse_json_obj,
            model_tier="creative", max_tokens=100,
        )


def test_complete_json_propagates_retryable_error() -> None:
    """RetryableError（网络瞬时）→ wrapper 重试 3 次后上抛，不被 complete_json 干预。"""
    class FailProvider(LLMProvider):
        def __init__(self):
            self.calls = 0
        def call(self, prompt, model, max_tokens):
            self.calls += 1
            raise RetryableError("network fail")

    p = FailProvider()
    set_provider(p)

    with pytest.raises(RetryableError):
        complete_json(
            "prompt", stage="test", parse=_parse_json_obj,
            model_tier="creative", max_tokens=100,
        )
    # complete() 内部 _call_with_retry 调 3 次
    assert p.calls == 3


def test_complete_json_propagates_value_error_from_parse_when_not_recoverable() -> None:
    """parse 抛 ValueError 但 max_retries=0 → 立即抛。"""
    def parse_strict(text: str) -> dict:
        raise ValueError("always fail")

    set_provider(ScriptedProvider(["any text"]))

    with pytest.raises(ValueError):
        complete_json(
            "prompt", stage="test", parse=parse_strict,
            model_tier="creative", max_tokens=100,
            max_retries=0,
        )


# ── fixup prompt 内容检查 ─────────────────────────────────

def test_complete_json_fixup_prompt_includes_original() -> None:
    """fixup prompt = 原始 prompt + 错误反馈 + malformed 输出。"""
    set_provider(ScriptedProvider([
        "{bad json",
        json.dumps({"k": "v"}),
    ]))

    complete_json(
        "ORIGINAL_PROMPT_TEXT",
        stage="test",
        parse=_parse_json_obj,
        model_tier="creative",
        max_tokens=100,
    )

    second_prompt = llm_mod._PROVIDER.calls[1]  # type: ignore[attr-defined]
    assert "ORIGINAL_PROMPT_TEXT" in second_prompt  # 原始 prompt
    assert "{bad json" in second_prompt  # malformed 输出
    assert "错误" in second_prompt  # 错误类型
    assert "JSON" in second_prompt  # 提示要 JSON


# ── stage 命名 ────────────────────────────────────────────

def test_complete_json_retry_uses_retry_stage_name() -> None:
    """第二次调用的 stage 名带 _retry 后缀（便于审计 llm_calls）。"""
    set_provider(ScriptedProvider([
        "bad",
        json.dumps({"k": "v"}),
    ]))

    # 通过 patch llm_calls 记录器来验证 stage 名
    with patch("pipeline.creators.llm._record_llm_call") as mock_record:
        complete_json(
            "prompt", stage="my_stage",
            parse=_parse_json_obj,
            model_tier="creative", max_tokens=100,
        )

    # 两次调用，第一次 stage=my_stage，第二次 stage=my_stage_retry
    stages = [call.kwargs["stage"] for call in mock_record.call_args_list]
    assert "my_stage" in stages
    assert "my_stage_retry" in stages