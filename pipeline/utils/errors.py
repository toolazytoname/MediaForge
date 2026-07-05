"""错误类型（TECH_SPEC §7 + M0-2 状态机专用）。

M0-2 阶段仅含状态机相关异常；M0-3 起扩展 SourceError / CreateError /
GateError / PublishError / BudgetExceeded，均继承 PipelineError。
"""
from __future__ import annotations


class PipelineError(Exception):
    """所有 MediaForge 业务异常的基类。"""


class IllegalTransition(PipelineError):
    """状态转移对不在合法表中（如 draft→approved 跳过 gated）。"""

    def __init__(self, table: str, from_status: str, to_status: str) -> None:
        super().__init__(
            f"illegal transition: {table} {from_status} -> {to_status}"
        )
        self.table = table
        self.from_status = from_status
        self.to_status = to_status


class StaleState(PipelineError):
    """乐观锁失败：UPDATE 时行的 status 已不再是期望的 from_status。"""

    def __init__(
        self,
        table: str,
        row_id: str,
        expected: str,
        actual: str | None,
    ) -> None:
        msg = f"stale state: {table} id={row_id} expected={expected}"
        if actual is not None:
            msg += f" actual={actual}"
        super().__init__(msg)
        self.table = table
        self.row_id = row_id
        self.expected_status = expected
        self.actual_status = actual
