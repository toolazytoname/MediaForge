"""LLM 统一入口（TECH_SPEC §5.3 + HARD_PARTS §4 成本失控防护）。

职责：
  1. model_tier → 具体模型（来自 config.llm.tiers）
  2. 每次调用记录 llm_calls 表（tokens + 成本）
  3. 月度成本超 budget.monthly_usd → 抛 BudgetExceeded
     （stage='gate' 时门禁永不跳过——内容质量是底线）
  4. 429 / 5xx 类异常指数退避重试 3 次
  5. prompt 与响应存 logs/llm/ 供调试（文件名=ref_id+stage+时间戳）

M1-3 阶段：默认 provider=MockProvider（不触真实 key）；
provider 真实接 MiniMax / Anthropic 由用户在 TASKS.md 的 DECISION NEEDED
上拍板后接入（M1-4 score 阶段首次真用）。

所有模块禁止直接 import anthropic——CI 守门（tests/test_creators_llm.py
::test_anthropic_import_only_in_llm_module）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.utils.errors import BudgetExceeded


# ── 价格表（USD / 百万 token）────────────────────────────
# 占位用——Anthropic Sonnet 4.x / Haiku 4.x 牌价写在此。
# MiniMax 等其他 provider 价格表 M1-4 决定 provider 后补。

MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}

# 月度预算硬顶（USD）。生产从 config.budget.monthly_usd 读；测试可 patch。
BUDGET_LIMIT_USD: float = 80.0

# 重试退避基数（秒）。第 n 次退避 = base * 2^(n-1)。
_RETRY_BASE_SLEEP_S: float = 1.0
_RETRY_MAX_ATTEMPTS: int = 3

# prompt / response 落盘目录。测试可 patch。
_LOG_DIR: Path = Path("logs/llm")


# ── Provider 抽象 ───────────────────────────────────────

@dataclass(frozen=True)
class CompletionResult:
    """provider 一次调用的原始结果（不含成本，成本由 wrapper 计算）。"""
    text: str
    input_tokens: int
    output_tokens: int


class RetryableError(Exception):
    """429 / 5xx / 网络瞬时错误——应触发重试。"""


class LLMProvider(ABC):
    """provider 抽象。M1-3 仅 MockProvider；真实 provider 待 DECISION 拍板后补。"""

    @abstractmethod
    def call(
        self, prompt: str, model: str, max_tokens: int
    ) -> CompletionResult:
        """执行一次 LLM 调用。RetryableError → wrapper 重试；其他异常立即抛。"""


class MockProvider(LLMProvider):
    """默认 provider：返回固定文本 + token。用于无 key 环境的开发与测试。"""

    DEFAULT_TEXT = "OK"
    DEFAULT_INPUT_TOKENS = 10
    DEFAULT_OUTPUT_TOKENS = 5

    def __init__(
        self,
        text: str = DEFAULT_TEXT,
        input_tokens: int = DEFAULT_INPUT_TOKENS,
        output_tokens: int = DEFAULT_OUTPUT_TOKENS,
    ) -> None:
        self._text = text
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    def call(
        self, prompt: str, model: str, max_tokens: int
    ) -> CompletionResult:
        return CompletionResult(
            text=self._text,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


# ── Module-level state（测试 / CLI 注入）────────────────

_PROVIDER: LLMProvider = MockProvider()
_DB_CONN: sqlite3.Connection | None = None
# tier 映射：cheat 模式下硬编码默认；CLI 可通过 set_tier_map() 覆盖
_TIER_MAP: dict[str, str] = {
    "cheap": "claude-haiku-4-5-20251001",
    "creative": "claude-sonnet-5",
    "critical": "claude-sonnet-5",
}


def set_provider(provider: LLMProvider) -> None:
    """注入 provider（测试 / CLI 启动时调用）。"""
    global _PROVIDER
    _PROVIDER = provider


def init_db_conn(conn: sqlite3.Connection) -> None:
    """注入 DB 连接（CLI 启动时调用，complete() 写 llm_calls）。"""
    global _DB_CONN
    _DB_CONN = conn


def set_tier_map(tiers: dict[str, str]) -> None:
    """注入 tier→model 映射（CLI 启动时从 config.llm.tiers 同步）。"""
    global _TIER_MAP
    _TIER_MAP = dict(tiers)


def set_budget_limit(usd: float) -> None:
    """注入月度预算硬顶。"""
    global BUDGET_LIMIT_USD
    BUDGET_LIMIT_USD = float(usd)


def _require_conn(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    """取 DB 连接：优先用入参，否则用 module-level。"""
    if conn is not None:
        return conn
    if _DB_CONN is None:
        raise RuntimeError(
            "llm.complete: DB connection not initialized; "
            "call init_db_conn(conn) or pass conn= kwarg"
        )
    return _DB_CONN


# ── 价格 / 估算 / 累计 ────────────────────────────────

def _resolve_model(model_tier: str) -> str:
    if model_tier not in _TIER_MAP:
        raise ValueError(
            f"unknown model_tier: {model_tier!r}; "
            f"known: {sorted(_TIER_MAP)}"
        )
    return _TIER_MAP[model_tier]


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """按 MODEL_PRICES 计算实际成本（USD）。"""
    prices = MODEL_PRICES.get(model)
    if prices is None:
        # 未知 model 视为 0（避免阻塞；写日志告警由调用方负责）
        return 0.0
    in_cost = prices["input"] * input_tokens / 1_000_000
    out_cost = prices["output"] * output_tokens / 1_000_000
    return in_cost + out_cost


def _estimate_cost_usd(
    model: str, prompt: str, max_tokens: int
) -> float:
    """调用前估算：input 用 len(prompt)/4 启发式，output 按 max_tokens 上限。

    宁可高估拒绝（保守）；低估放过由调用后记账兜底。
    """
    est_input = max(1, len(prompt) // 4)
    return _cost_usd(model, est_input, max_tokens)


def _monthly_used_usd(conn: sqlite3.Connection, now: datetime) -> float:
    """查 llm_calls 表中当月累计成本。"""
    month_prefix = now.strftime("%Y-%m")
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_usd), 0) AS used
        FROM llm_calls
        WHERE substr(created_at, 1, 7) = ?
        """,
        (month_prefix,),
    ).fetchone()
    return float(row["used"])


# ── 审计 / 日志 ────────────────────────────────────────

def _record_llm_call(
    conn: sqlite3.Connection,
    *,
    stage: str,
    ref_id: str | None,
    model: str,
    result: CompletionResult,
    cost_usd: float,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls
            (stage, ref_id, model, input_tokens, output_tokens,
             cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stage, ref_id, model,
            result.input_tokens, result.output_tokens,
            cost_usd, now,
        ),
    )
    conn.commit()


def _dump_log(
    *,
    stage: str,
    ref_id: str | None,
    prompt: str,
    response: str,
    model: str,
    cost_usd: float,
    now: str,
) -> None:
    """prompt + response 落 logs/llm/，文件名带 ref_id + stage + ts。"""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (ref_id or "noref") + "_" + stage
    ts_safe = now.replace(":", "-").replace(".", "-")
    path = _LOG_DIR / f"{suffix}_{ts_safe}.json"
    payload: dict[str, Any] = {
        "stage": stage,
        "ref_id": ref_id,
        "model": model,
        "cost_usd": cost_usd,
        "prompt": prompt,
        "response": response,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 公开入口 ──────────────────────────────────────────

def complete(
    prompt: str,
    *,
    stage: str,
    ref_id: str | None = None,
    model_tier: str = "creative",
    max_tokens: int = 4096,
    conn: sqlite3.Connection | None = None,
) -> str:
    """统一 LLM 入口。返回文本（CompletionResult 中的 text 字段）。

    Args:
        prompt: 用户 prompt
        stage: 编排阶段（'score' / 'create' / 'gate' / ...）
        ref_id: 关联记录 id（topic/content/publication）
        model_tier: 'cheap' | 'creative' | 'critical'（来自 config.llm.tiers）
        max_tokens: 输出上限硬顶（防止 prompt bug 致天量输出）
        conn: 可选 DB 连接（测试用）；CLI 通常已通过 init_db_conn() 注入

    Returns:
        provider 返回的 text

    Raises:
        ValueError: 未知 model_tier
        BudgetExceeded: 月度成本超限（stage='gate' 跳过此检查）
        RetryableError: 重试 3 次后仍失败
    """
    db_conn = _require_conn(conn)
    model = _resolve_model(model_tier)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    # 预算检查（gate 阶段跳过——内容质量是底线）
    if stage != "gate":
        used = _monthly_used_usd(db_conn, now_dt)
        est = _estimate_cost_usd(model, prompt, max_tokens)
        if used + est > BUDGET_LIMIT_USD:
            raise BudgetExceeded(
                stage=stage, used_usd=used, limit_usd=BUDGET_LIMIT_USD
            )

    # 重试调用
    result = _call_with_retry(prompt, model, max_tokens)

    # 记账 + 落盘
    cost = _cost_usd(model, result.input_tokens, result.output_tokens)
    _record_llm_call(
        db_conn,
        stage=stage, ref_id=ref_id, model=model,
        result=result, cost_usd=cost, now=now_iso,
    )
    _dump_log(
        stage=stage, ref_id=ref_id, prompt=prompt,
        response=result.text, model=model, cost_usd=cost, now=now_iso,
    )

    return result.text


def _call_with_retry(
    prompt: str, model: str, max_tokens: int
) -> CompletionResult:
    """指数退避 ×3，RetryableError 重试，其他异常立即抛。"""
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            return _PROVIDER.call(prompt, model, max_tokens)
        except RetryableError as e:
            last_exc = e
            if attempt < _RETRY_MAX_ATTEMPTS:
                sleep_s = _RETRY_BASE_SLEEP_S * (2 ** (attempt - 1))
                time.sleep(sleep_s)
    # 用尽
    assert last_exc is not None
    raise last_exc