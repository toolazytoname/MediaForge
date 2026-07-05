"""结构化 JSON lines 日志（TECH_SPEC §8 + HARD_PARTS §9）。

每条日志一行 JSON，固定字段：
  ts      ISO8601 UTC（datetime.now(tz=utc).isoformat()）
  level   DEBUG/INFO/WARNING/ERROR/CRITICAL
  stage   编排阶段标签，如 'ingest' / 'score' / 'gate' / 'publish'
  ref_id  关联记录 id，可空（无关联时记 null）
  msg     用户消息

默认输出：
  - logs/pipeline.log  按天轮转（TimedRotatingFileHandler），保留 30 天
  - stderr            JSON 同样格式，方便本地观察

测试隔离：get_logger(name, log_dir=tmp_path) 按 (name, log_dir) 缓存，
不同 log_dir 各自独立。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOGGER_CACHE: dict[tuple[str, str], logging.Logger] = {}
_FORMAT_VERSION = 1


class JsonLineFormatter(logging.Formatter):
    """JSON lines 格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "stage": getattr(record, "stage", "-"),
            "ref_id": getattr(record, "ref_id", None),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_logger(name: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"{name}@{log_dir}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = JsonLineFormatter()

    fh = logging.handlers.TimedRotatingFileHandler(
        log_dir / "pipeline.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


def get_logger(
    name: str = "pipeline",
    log_dir: str | Path = "logs",
) -> logging.Logger:
    """取（或创建并缓存）JSON 日志 logger。

    缓存 key = (name, str(log_dir.resolve()))，所以同一 (name, dir) 共享 handler，
    不同 dir 各自独立——测试用 tmp_path 时互不污染。
    """
    resolved = Path(log_dir).resolve()
    key = (name, str(resolved))
    cached = _LOGGER_CACHE.get(key)
    if cached is not None:
        return cached

    logger = _build_logger(name, resolved)
    _LOGGER_CACHE[key] = logger
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    msg: str,
    *,
    stage: str = "-",
    ref_id: str | None = None,
) -> None:
    """便捷日志调用——不必每次手写 extra={...}。"""
    logger.log(level, msg, extra={"stage": stage, "ref_id": ref_id})