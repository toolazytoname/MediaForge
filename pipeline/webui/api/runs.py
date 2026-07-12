"""M10-5 runs router（首期只读 + 白名单常量）。

GET /api/v1/runs — 内存运行历史（P1 仅返回空；P2 runner_bridge 启用后
  真正记录 stage/exit_code/timing）
GET /api/v1/runs/{run_id} — 单条详情（P1 返回 404）
POST /api/v1/runs/{stage} — 触发后台执行（P2 才实现；P1 拒绝）

U7-7：`update_run_message(run_id, message)` 写入进度消息，前端轮询拿到。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

# 允许通过 UI 一键触发的 stage 白名单（发布排除）
STAGE_WHITELIST = frozenset({
    "ingest", "score", "create", "gate", "derivative",
    "review", "schedule", "collect", "generate-images",
})

router = APIRouter(tags=["runs"])


# P1 内存占位（不真正记录）
_RUN_HISTORY: list[dict[str, Any]] = []

# M10-12：preview/dry-run 后台运行注册表（独立字典，
# 不并入 STAGE_WHITELIST，因为 preview 不属于通用一键触发阶段）。
_RUNS: dict[str, dict[str, Any]] = {}


def register_run(run_id: str, **fields: Any) -> None:
    """在内存 run registry 写入一条 run 记录（首次创建时状态为 queued）。"""
    _RUNS[run_id] = {"run_id": run_id, **fields}


def get_run_record(run_id: str) -> dict[str, Any] | None:
    """查询内存 run registry；无则返 None（router 把它映射为 404）。"""
    return _RUNS.get(run_id)


def update_run_message(run_id: str, message: str) -> None:
    """更新 run 的最新进度消息（前端轮询拿到）。无 record 时静默跳过。

    U7-7：login_bridge 通过 logging.Handler 子类捕获 pipeline.publishers.login
    logger 的每条日志，调本函数把最新 message 写进 runs registry；前端
    1.5s 轮询 `GET /runs/{run_id}` 拿最新 message 实现实时进度推送。

    Args:
        run_id: 来自 `register_run` 的 run 标识。
        message: 用户可见的进度文本（已国际化/已 log_event 格式化）。

    Notes:
        - 不存在的 run_id **静默跳过**，避免 background task 被异常中断
          （登录 run 可能被 cancel / registry 被清理）。
        - `message_at` 是 ISO8601 UTC 时间戳，前端可用来判断进度是否卡死。
        - 连续调用覆盖前值（只保留最新 message；需要历史请走日志文件）。
    """
    rec = _RUNS.get(run_id)
    if rec is None:
        return
    rec["message"] = message
    rec["message_at"] = datetime.now(timezone.utc).isoformat()


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    """运行历史。P1 仅返回空（runner_bridge 留 P2）。"""
    return {
        "items": _RUN_HISTORY,
        "stage_whitelist": sorted(STAGE_WHITELIST),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """单条详情。P1 永远 404——无持久化。

    M10-12 升级：preview 端点使用同一内存 registry（`register_run`/`get_run_record`），
    真正存在的 run 仍可被前端轮询拿到结果。
    """
    record = get_run_record(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": {
            "code": "run_not_found", "message": f"run {run_id} not found"
        }})
    return record


@router.post("/runs/{stage}")
def trigger_run(stage: str) -> dict[str, Any]:
    """P1 拒绝所有触发；P2 启用。"""
    if stage not in STAGE_WHITELIST:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "stage_not_whitelisted",
            "message": f"stage {stage!r} not in whitelist; publish is excluded",
        }})
    # publish 已通过白名单过滤——若调用方传 publish（理论上不该），同样拒绝
    raise HTTPException(status_code=501, detail={"error": {
        "code": "not_implemented",
        "message": "runner_bridge 留 P2 启用；M10 P1 不暴露触发端点",
    }})
