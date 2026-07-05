"""creators/llm.py::complete() 单元测试（TECH_SPEC §5.3 + HARD_PARTS §4）。

M1-3 阶段：provider=MockProvider（不触真实 key；provider 真实接 MiniMax/Anthropic
推迟到 M1-4，等用户在 TASKS.md DECISION NEEDED 上拍板）。

覆盖：
  - tier→model 映射（cheap/creative/critical）
  - llm_calls 行写入（input_tokens / output_tokens / cost_usd > 0）
  - 预算超限抛 BudgetExceeded（stage='gate' 永不跳过）
  - 429/5xx 重试（指数退避 ×3 → 第 4 次失败）
  - 其他异常不重试
  - prompt + response 落盘 logs/llm/
  - 默认 provider 可注入替换
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
    MockProvider,
    complete,
    set_provider,
)
from pipeline.utils.errors import BudgetExceeded


# ── helpers ──────────────────────────────────────────────

class FakeProvider:
    """可控的 fake provider：可预设返回文本、token、异常、调用次数。"""

    def __init__(
        self,
        text: str = "ok",
        input_tokens: int = 100,
        output_tokens: int = 50,
        exception: Exception | None = None,
        fail_times: int = 0,
    ) -> None:
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self._exc = exception
        self._fail_times = fail_times
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append(
            {"prompt": prompt, "model": model, "max_tokens": max_tokens}
        )
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._exc or RuntimeError("fail")
        from pipeline.creators.llm import CompletionResult
        return CompletionResult(
            text=self.text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


@pytest.fixture(autouse=True)
def reset_provider():
    """每个 test 后重置 module-level provider 为 MockProvider。"""
    set_provider(MockProvider())
    yield
    set_provider(MockProvider())


# ── tier→model ───────────────────────────────────────────

def test_default_tier_is_creative(conn) -> None:
    """默认 model_tier='creative'。"""
    fake = FakeProvider()
    set_provider(fake)
    complete("hi", stage="test", ref_id="r1", conn=conn)

    assert fake.calls[0]["model"] == "claude-sonnet-5"


def test_cheap_tier_uses_cheap_model(conn) -> None:
    fake = FakeProvider()
    set_provider(fake)
    complete("hi", stage="score", model_tier="cheap", conn=conn)

    assert fake.calls[0]["model"] == "claude-haiku-4-5-20251001"


def test_critical_tier_uses_critical_model(conn) -> None:
    fake = FakeProvider()
    set_provider(fake)
    complete("hi", stage="gate", model_tier="critical", conn=conn)

    assert fake.calls[0]["model"] == "claude-sonnet-5"


def test_unknown_tier_raises_value_error(conn) -> None:
    """未知 tier 立即抛 ValueError（fail-fast）。"""
    with pytest.raises(ValueError):
        complete("hi", stage="test", model_tier="bogus", conn=conn)


# ── llm_calls 审计 ──────────────────────────────────────

def test_records_llm_call_row(conn) -> None:
    """每次调用写一条 llm_calls 行：tokens + cost > 0。"""
    fake = FakeProvider(input_tokens=200, output_tokens=100)
    set_provider(fake)
    complete("hello world", stage="test", ref_id="abc", conn=conn)

    rows = conn.execute("SELECT * FROM llm_calls").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "test"
    assert row["ref_id"] == "abc"
    assert row["model"] == "claude-sonnet-5"
    assert row["input_tokens"] == 200
    assert row["output_tokens"] == 100
    assert row["cost_usd"] > 0


def test_cost_matches_pricing(conn) -> None:
    """cost = input_tokens * input_price + output_tokens * output_price。"""
    from pipeline.creators.llm import MODEL_PRICES

    fake = FakeProvider(input_tokens=1_000_000, output_tokens=0)
    set_provider(fake)
    complete("x", stage="test", ref_id=None, conn=conn)

    row = conn.execute("SELECT cost_usd FROM llm_calls").fetchone()
    price = MODEL_PRICES["claude-sonnet-5"]
    expected = price["input"] * 1.0  # 1M input tokens
    assert abs(row["cost_usd"] - expected) < 1e-6


# ── 预算 ────────────────────────────────────────────────

def test_budget_exceeded_raises(conn) -> None:
    """月度成本超 limit → BudgetExceeded。"""
    # 配置极小预算；制造一次大调用把它打爆
    fake = FakeProvider(input_tokens=10_000_000, output_tokens=10_000_000)
    set_provider(fake)

    with patch.object(llm_mod, "_monthly_used_usd", return_value=4.99):
        with patch.object(llm_mod, "BUDGET_LIMIT_USD", 5.0):
            with pytest.raises(BudgetExceeded) as ei:
                complete("x", stage="create", ref_id=None, conn=conn)
    assert ei.value.stage == "create"


def test_gate_stage_bypasses_budget(conn) -> None:
    """TECH_SPEC §5.3：stage='gate' 时门禁永不跳过。"""
    fake = FakeProvider(input_tokens=10_000_000, output_tokens=10_000_000)
    set_provider(fake)

    with patch.object(llm_mod, "_monthly_used_usd", return_value=10.0):
        with patch.object(llm_mod, "BUDGET_LIMIT_USD", 5.0):
            # 应该不抛
            text = complete("x", stage="gate", ref_id=None, conn=conn)
    assert text == "ok"


def test_budget_check_uses_estimate_not_actual(conn) -> None:
    """预算检查用估算（避免事后才知道超），但实际仍记账。"""
    fake = FakeProvider(input_tokens=10, output_tokens=5)
    set_provider(fake)

    # 估算已超 → 即使实际 cost 很小也不调用
    with patch.object(llm_mod, "_estimate_cost_usd", return_value=100.0):
        with patch.object(llm_mod, "BUDGET_LIMIT_USD", 5.0):
            with pytest.raises(BudgetExceeded):
                complete("x", stage="create", ref_id=None, conn=conn)
    # provider 未被调用
    assert fake.calls == []


# ── 重试 ────────────────────────────────────────────────

def test_retry_on_retryable_exception(conn, monkeypatch) -> None:
    """429/5xx 类异常自动重试。"""
    fake = FakeProvider(fail_times=2)  # 前 2 次失败，第 3 次成功
    fake._exc = llm_mod.RetryableError("429 rate limit")
    set_provider(fake)

    # 加速：把 backoff 秒数 patch 成 0
    monkeypatch.setattr(llm_mod, "_RETRY_BASE_SLEEP_S", 0)

    text = complete("x", stage="test", ref_id=None, conn=conn)

    assert text == "ok"
    assert len(fake.calls) == 3  # 失败 2 次 + 成功 1 次


def test_no_retry_on_non_retryable(conn, monkeypatch) -> None:
    """非重试异常立即抛。"""
    fake = FakeProvider(fail_times=99, exception=ValueError("bad input"))
    set_provider(fake)

    with pytest.raises(ValueError):
        complete("x", stage="test", ref_id=None, conn=conn)
    assert len(fake.calls) == 1


def test_retry_exhausted_raises_last_exception(conn, monkeypatch) -> None:
    """重试用尽后抛最后异常。"""
    fake = FakeProvider(fail_times=99)  # 永远失败
    fake._exc = llm_mod.RetryableError("429")
    set_provider(fake)
    monkeypatch.setattr(llm_mod, "_RETRY_BASE_SLEEP_S", 0)

    with pytest.raises(llm_mod.RetryableError):
        complete("x", stage="test", ref_id=None, conn=conn)
    # 默认 3 次
    assert len(fake.calls) == 3


# ── 日志落盘 ─────────────────────────────────────────────

def test_prompt_and_response_dumped_to_log(conn, tmp_path, monkeypatch) -> None:
    """prompt + response 落到 logs/llm/ 下。"""
    fake = FakeProvider(text="the answer")
    set_provider(fake)

    log_dir = tmp_path / "llm_logs"
    monkeypatch.setattr(llm_mod, "_LOG_DIR", log_dir)

    complete(
        "the question", stage="test", ref_id="xyz", conn=conn
    )

    files = list(log_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text("utf-8"))
    assert payload["prompt"] == "the question"
    assert payload["response"] == "the answer"
    assert payload["stage"] == "test"
    assert payload["ref_id"] == "xyz"


# ── 鉴权导入护栏 ───────────────────────────────────────

def test_anthropic_import_only_in_llm_module() -> None:
    """HARD_PARTS §4 护栏：import anthropic 只允许在 creators/llm.py。"""
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "import anthropic", "pipeline/"],
        capture_output=True, text=True, check=False,
    )
    lines = [
        l for l in result.stdout.splitlines()
        if "creators/llm.py" not in l
    ]
    assert lines == [], (
        f"anthropic import leaked outside creators/llm.py: {lines}"
    )


# ── 简单调用形状 ───────────────────────────────────────

def test_returns_provider_text(conn) -> None:
    """complete() 返回 provider 的 text。"""
    fake = FakeProvider(text="specific response")
    set_provider(fake)
    text = complete("hi", stage="test", ref_id=None, conn=conn)
    assert text == "specific response"