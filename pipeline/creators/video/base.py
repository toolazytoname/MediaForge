"""视频引擎契约（TECH_SPEC §5.6）。

默认引擎 mpt（MoneyPrinterTurbo HTTP API）。扩展引擎（openmontage/aigcpanel 数字人）
按 M5-3 评估结论接入——实现本接口即可，不动编排层。
引擎初始化失败必须降级（工厂函数捕获），不得影响其他引擎与图文链路。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class VideoRequest:
    content_id: str
    script: str                  # 我方 LLM 产出的口播稿；不让引擎自己写文案
    duration_s: int
    aspect: str                  # '9:16' | '16:9'
    style: dict = field(default_factory=dict)   # 引擎特有参数（音色/模板/形象等）


@dataclass(frozen=True)
class VideoJobStatus:
    state: str                   # 'pending'|'running'|'done'|'failed'
    progress: float | None       # 0-1，引擎不支持则 None
    error: str | None


class VideoEngine(ABC):
    name: str                    # 'mpt' | 'openmontage' | 'aigcpanel'

    @abstractmethod
    def submit(self, req: VideoRequest) -> str:
        """提交生成任务，返回引擎内部 job_id。失败抛 CreateError。"""

    @abstractmethod
    def poll(self, job_id: str) -> VideoJobStatus:
        """查询任务状态。"""

    @abstractmethod
    def fetch(self, job_id: str, dest: Path) -> Path:
        """下载成品 mp4 到 dest，返回最终文件路径。"""
