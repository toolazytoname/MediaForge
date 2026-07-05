"""错误类型（TECH_SPEC §7 + M0-2 状态机专用）。

所有 MediaForge 业务异常继承 PipelineError。M0-2 已含 IllegalTransition /
StaleState，M0-3 补 SourceError / CreateError / GateError / PublishError /
BudgetExceeded。

编排层原则（TECH_SPEC §8）：单条失败不阻断批次——捕到异常、标记该条 failed、
继续下一条；系统性失败（数据库损坏、预算超限、config 无效）立即退出。
"""
from __future__ import annotations


class PipelineError(Exception):
    """所有 MediaForge 业务异常的基类。"""


# ── M0-2: 状态机 ──────────────────────────────────────────

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


# ── M0-3: TECH_SPEC §7 ──────────────────────────────────

class SourceError(PipelineError):
    """数据源抓取失败（网络异常、解析失败、cookie 过期）。

    编排层捕获、跳过该源、继续其他源（HARD_PARTS §8）。
    """


class CreateError(PipelineError):
    """创作管道失败（LLM 调用失败、文件写入失败、渲染失败）。

    对应一条内容标记 failed，不影响其他内容。
    """


class GateError(PipelineError):
    """质量门禁结构性异常（区别于"内容被丢弃"）。

    内容被门禁判定为不达标是正常流程（标记 discarded），不应抛 GateError；
    抛此异常意味着门禁本身崩溃（配置文件错、锚点缺失、调用超时）。
    """


class PublishError(PipelineError):
    """发布失败（平台调用异常、cookie 失效、频控触发、超时）。

    对应一条 publication 标记 failed，编排层根据 HARD_PARTS §1 决定重试或告警。
    """


class BudgetExceeded(PipelineError):
    """LLM 月度预算超限（HARD_PARTS §4）。

    编排层收到后停止当日流水线 + IM 告警。stage='gate' 时门禁永不跳过。
    """

    def __init__(
        self,
        stage: str,
        used_usd: float,
        limit_usd: float,
    ) -> None:
        super().__init__(
            f"budget exceeded: stage={stage} "
            f"used=${used_usd:.2f} limit=${limit_usd:.2f}"
        )
        self.stage = stage
        self.used_usd = used_usd
        self.limit_usd = limit_usd