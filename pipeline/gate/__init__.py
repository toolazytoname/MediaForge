"""质量门禁包（M2-2）。

子模块：
  - anchors_loader：6 篇校准样例加载
  - decision：修订采纳规则（移植 TrendPublish qualityActionRank）
  - critic：批判轮（独立 LLM 会话）
  - scorer：独立评分会话（critical 档）
  - runner：编排（critic → 可选 rewrite → scorer → 状态转移）

入口函数：run_gate(conn, *, gate_cfg, anchors_dir=None, now=None) → GateRunResult
"""
from pipeline.gate.runner import (
    ContentGateOutcome,
    GateRunResult,
    run_gate,
)

__all__ = [
    "ContentGateOutcome",
    "GateRunResult",
    "run_gate",
]