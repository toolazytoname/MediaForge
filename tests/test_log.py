"""utils/log.py 单元测试。

覆盖：
  - JSON 行格式含 ts/level/stage/ref_id/msg
  - 不带 stage/ref_id 调用时填默认值（stage='-'、ref_id=None）
  - log_dir 自动创建
  - 中文消息 ensure_ascii=False
  - 异常栈序列化为 exc 字段
  - 同一 (name, log_dir) 复用缓存 logger
"""
from __future__ import annotations

import json
import logging

import pytest

from pipeline.utils.log import (
    JsonLineFormatter,
    get_logger,
    log_event,
)


def _last_log_line(log_path) -> dict:
    """读日志文件最后一行非空，解析 JSON。"""
    lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert lines, "no log lines emitted"
    return json.loads(lines[-1])


# ── Formatter 直接单元测试 ────────────────────────────────

def _make_record(msg: str, **extras) -> logging.LogRecord:
    """构造 LogRecord 用于直接测 formatter。"""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return record


def test_formatter_required_fields():
    fmt = JsonLineFormatter()
    rec = _make_record("hello", stage="score", ref_id="t_abc")
    payload = json.loads(fmt.format(rec))
    assert set(payload.keys()) >= {"ts", "level", "stage", "ref_id", "msg"}
    assert payload["ts"].endswith("+00:00")
    assert payload["level"] == "INFO"
    assert payload["stage"] == "score"
    assert payload["ref_id"] == "t_abc"
    assert payload["msg"] == "hello"


def test_formatter_defaults_when_no_extras():
    fmt = JsonLineFormatter()
    rec = _make_record("naked")
    payload = json.loads(fmt.format(rec))
    assert payload["stage"] == "-"
    assert payload["ref_id"] is None


def test_formatter_chinese_preserved():
    fmt = JsonLineFormatter()
    rec = _make_record("门禁通过", stage="gate")
    line = fmt.format(rec)
    assert "门禁通过" in line  # ensure_ascii=False


def test_formatter_exception_serialized():
    fmt = JsonLineFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord(
            name="t", level=logging.ERROR, pathname="x",
            lineno=1, msg="failed", args=None,
            exc_info=sys.exc_info(),
        )
    payload = json.loads(fmt.format(rec))
    assert payload["level"] == "ERROR"
    assert "boom" in payload["exc"]
    assert "Traceback" in payload["exc"]


# ── get_logger + log_event 集成 ──────────────────────────

def test_get_logger_creates_log_dir(tmp_path):
    log_dir = tmp_path / "auto_logs"
    assert not log_dir.exists()
    logger = get_logger("auto_dir", log_dir=log_dir)
    assert log_dir.is_dir()
    assert (log_dir / "pipeline.log").exists() or logger.handlers


def test_log_event_writes_valid_json(tmp_path):
    logger = get_logger("json_write", log_dir=tmp_path)
    log_event(logger, logging.INFO, "first event",
              stage="ingest", ref_id="t_x1")
    log_event(logger, logging.INFO, "second event",
              stage="gate", ref_id="c_y2")

    log_file = tmp_path / "pipeline.log"
    lines = [
        ln for ln in log_file.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) >= 2
    payloads = [json.loads(ln) for ln in lines]
    msgs = [p["msg"] for p in payloads]
    assert "first event" in msgs and "second event" in msgs
    last_with_ref = next(p for p in payloads if p["ref_id"] == "c_y2")
    assert last_with_ref["stage"] == "gate"


def test_logger_cached_per_log_dir(tmp_path):
    a1 = get_logger("cache_test", log_dir=tmp_path / "a")
    a2 = get_logger("cache_test", log_dir=tmp_path / "a")
    assert a1 is a2
    b = get_logger("cache_test", log_dir=tmp_path / "b")
    assert a1 is not b


def test_log_event_default_stage_and_ref_id(tmp_path):
    logger = get_logger("defaults", log_dir=tmp_path)
    log_event(logger, logging.WARNING, "no context")
    payload = _last_log_line(tmp_path / "pipeline.log")
    assert payload["stage"] == "-"
    assert payload["ref_id"] is None
    assert payload["level"] == "WARNING"


def test_log_file_uses_timed_rotating_handler(tmp_path):
    """确认 logger 装了 TimedRotatingFileHandler（TECH_SPEC §8 每天轮转）。"""
    logger = get_logger("rotation", log_dir=tmp_path)
    rotators = [
        h for h in logger.handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert rotators, "no TimedRotatingFileHandler attached"
    h = rotators[0]
    assert h.when == "MIDNIGHT"  # logging normalizes to uppercase
    assert h.backupCount == 30