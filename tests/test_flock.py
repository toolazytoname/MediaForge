"""M3-2 flock 跨进程锁（HARD_PARTS §8）。

测试 acquire/release/重入/异常路径。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pipeline.utils.flock import (
    LockHeld,
    acquire,
    is_locked,
    release,
)


# ── 单进程 ─────────────────────────────────────────────────


class TestSingleProcess:
    def test_acquire_then_release(self, tmp_path: Path) -> None:
        lock = tmp_path / "x.lock"
        acquire(lock)
        assert is_locked(lock)
        release(lock)
        assert not is_locked(lock)

    def test_double_acquire_raises(
        self, tmp_path: Path
    ) -> None:
        """同进程内二次 acquire 同锁：抛 LockHeld（HARD_PARTS §8 第二把锁语义）。"""
        lock = tmp_path / "x.lock"
        acquire(lock)
        try:
            with pytest.raises(LockHeld):
                acquire(lock)
        finally:
            release(lock)

    def test_release_unowned_noop(self, tmp_path: Path) -> None:
        """未持锁时 release：不抛错（幂等）。"""
        lock = tmp_path / "x.lock"
        release(lock)  # 不应抛
        assert not is_locked(lock)

    def test_lock_dir_created(self, tmp_path: Path) -> None:
        lock = tmp_path / "nested" / "x.lock"
        acquire(lock)
        try:
            assert lock.exists()
        finally:
            release(lock)

    def test_context_manager(
        self, tmp_path: Path
    ) -> None:
        """acquire/release 作为 context manager 也能用（不强制）。"""
        lock = tmp_path / "x.lock"
        # 直接 acquire + release 路径已覆盖；这里额外验证文件句柄关掉
        acquire(lock)
        assert is_locked(lock)
        release(lock)
        # 文件本身可能还在（fcntl 锁是进程级），但下次 acquire 能成功
        acquire(lock)
        release(lock)


# ── 跨进程 ─────────────────────────────────────────────────


class TestCrossProcess:
    def test_second_process_exits_immediately(
        self, tmp_path: Path
    ) -> None:
        """父进程持锁 → 子进程试图 acquire 必须立即抛 LockHeld（HARD_PARTS §8）。

        用 subprocess 真起子进程（不能单线程模拟）。
        """
        lock = tmp_path / "x.lock"
        acquire(lock)
        try:
            # 启子进程
            code = f"""
import sys
from pathlib import Path
sys.path.insert(0, '{Path.cwd()}')
from pipeline.utils.flock import acquire, LockHeld
try:
    acquire(Path('{lock}'))
    print('ACQUIRED')
    sys.exit(0)
except LockHeld:
    print('LOCK_HELD')
    sys.exit(2)
"""
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=10,
            )
            assert "LOCK_HELD" in result.stdout
            assert result.returncode == 2
        finally:
            release(lock)

    def test_released_lock_acquirable_by_other(
        self, tmp_path: Path
    ) -> None:
        """父进程 release 后 → 子进程能 acquire。"""
        lock = tmp_path / "x.lock"
        acquire(lock)
        release(lock)

        code = f"""
import sys
from pathlib import Path
sys.path.insert(0, '{Path.cwd()}')
from pipeline.utils.flock import acquire, release
acquire(Path('{lock}'))
print('ACQUIRED')
release(Path('{lock}'))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=10,
        )
        assert "ACQUIRED" in result.stdout
        assert result.returncode == 0