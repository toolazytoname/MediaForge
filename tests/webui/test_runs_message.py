"""U7-7 runs registry 扩字段（message / message_at）测试。

覆盖：
  - update_run_message(run_id, message) 写 message + message_at
  - 不存在的 run_id 静默跳过（不抛异常）
  - 既有 GET /runs/{run_id} 行为不变（向后兼容）
  - register_run 仍正常工作（没 message 字段时 GET 正常返回）
"""
from __future__ import annotations

from datetime import datetime, timezone

from pipeline.webui.api.runs import (
    _RUNS,
    get_run_record,
    register_run,
    update_run_message,
)


def test_update_run_message_writes_message_and_message_at() -> None:
    """update_run_message 写 message 字段 + ISO8601 message_at。"""
    _RUNS.clear()
    register_run("r1", status="queued", platform="toutiao", account="main")
    update_run_message("r1", "请在浏览器里完成登录")
    rec = _RUNS["r1"]
    assert rec["message"] == "请在浏览器里完成登录"
    # message_at 必须 ISO8601
    assert "message_at" in rec
    parsed = datetime.fromisoformat(rec["message_at"])
    assert parsed.tzinfo is not None


def test_update_run_message_unknown_run_is_silent() -> None:
    """不存在的 run_id 必须静默跳过（不能抛异常破坏 background task）。"""
    _RUNS.clear()
    # 必须不抛
    update_run_message("nonexistent", "hello")
    assert "nonexistent" not in _RUNS


def test_update_run_message_overwrites_previous_message() -> None:
    """连续调用 update_run_message → 后者覆盖前者。"""
    _RUNS.clear()
    register_run("r2", status="running")
    update_run_message("r2", "first")
    update_run_message("r2", "second")
    assert _RUNS["r2"]["message"] == "second"
    assert "message_at" in _RUNS["r2"]


def test_register_run_without_message_field_returns_no_message() -> None:
    """旧 register_run 调用没 message 字段 → get_run_record 拿到的 record
    也没 message 字段（向后兼容：消费方必须容忍 message 缺失）。"""
    _RUNS.clear()
    register_run("r3", status="queued", platform="x", account="main")
    rec = get_run_record("r3")
    assert rec is not None
    assert "message" not in rec
    assert rec["status"] == "queued"


def test_register_run_preserves_explicit_message_field() -> None:
    """register_run 支持直接传 message（与 update_run_message 互通）。"""
    _RUNS.clear()
    register_run("r4", status="running", message="starting")
    rec = get_run_record("r4")
    assert rec is not None
    assert rec["message"] == "starting"


def test_message_at_is_recent_utc_isoformat() -> None:
    """message_at 必须是当前 UTC 的 ISO8601（误差 < 5 秒）。"""
    _RUNS.clear()
    register_run("r5", status="running")
    before = datetime.now(timezone.utc)
    update_run_message("r5", "test")
    after = datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(_RUNS["r5"]["message_at"])
    # 时间戳必须在 before / after 之间
    assert before <= parsed <= after