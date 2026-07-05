"""M3-2 跨进程文件锁（HARD_PARTS §8 cron 重叠防护）。

设计：
  - 单进程内：模块级 `_HELD` 字典防止同进程重复锁
  - 跨进程：fcntl LOCK_EX | LOCK_NB（非阻塞；拿不到立即抛 LockHeld）
  - 平台：仅支持 Linux / macOS（fcntl）。Windows 未支持——本项目目标是
    macOS 自动化矩阵，不会在 Windows 上跑 cron；如未来要支持需补 msvcrt。

用法：
    from pipeline.utils.flock import acquire, release, LockHeld
    try:
        acquire(Path("locks/ingest.lock"))
    except LockHeld:
        print("另一轮还在跑，本次跳过")
        return 0
    try:
        # ... do work ...
    finally:
        release(Path("locks/ingest.lock"))

幂等：release 未持锁 → noop（不抛错）；acquire 同进程已持 → 抛 LockHeld。
"""
from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


class LockHeld(Exception):
    """锁被另一进程/同进程持有，acquire 失败。"""


# 模块级：当前进程持有的 (lock_path -> file_handle)
_HELD: dict[str, IO] = {}


def acquire(lock_path: Path | str) -> IO:
    """获取排他锁（非阻塞）。

    流程：
      1. 检查模块级 _HELD：同进程内重复锁 → LockHeld
      2. 创建父目录（如不存在）
      3. 打开文件（不存在则创建）
      4. fcntl LOCK_EX | LOCK_NB：失败抛 LockHeld
      5. 记录 _HELD[lock_path] → fd
      6. 返回 fd（release 需要）

    抛出：LockHeld
    """
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    key = str(p.resolve())

    if key in _HELD:
        raise LockHeld(f"same process already holds lock: {key}")

    fd = open(p, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        fd.close()
        raise LockHeld(f"lock held by another process: {key}")

    _HELD[key] = fd
    return fd


def release(lock_path: Path | str) -> None:
    """释放锁（幂等：未持锁 → noop，不抛错）。

    流程：
      1. _HELD 找不到 → 直接返回（幂等）
      2. fcntl LOCK_UN
      3. close fd
      4. 清 _HELD[key]
    """
    p = Path(lock_path)
    key = str(p.resolve())
    fd = _HELD.pop(key, None)
    if fd is None:
        return
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    except (OSError, AttributeError):
        pass
    try:
        fd.close()
    except Exception:
        pass


def is_locked(lock_path: Path | str) -> bool:
    """判断锁文件是否被本进程持有（不代表跨进程状态！）。"""
    p = Path(lock_path)
    return str(p.resolve()) in _HELD


__all__ = ["acquire", "release", "is_locked", "LockHeld"]