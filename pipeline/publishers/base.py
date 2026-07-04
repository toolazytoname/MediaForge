"""发布适配器契约（TECH_SPEC §5.2）。

发布是全系统唯一不可逆操作。实现前必读 HARD_PARTS §1（防重复发布）与 §2（风控）。
适配器不修改内容、不管理状态——状态机与三重锁由编排层负责。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PostBundle:
    content_id: str
    title: str
    body_path: Path              # 平台格式化后的正文文件
    media_paths: tuple[Path, ...]
    tags: tuple[str, ...]
    extra: dict = field(default_factory=dict)   # 平台特有字段


@dataclass(frozen=True)
class PublishResult:
    platform_post_id: str | None
    url: str | None
    raw_response: str


@dataclass(frozen=True)
class AccountConfig:
    id: str
    credentials_path: Path       # secrets/ 下的凭据/cookie 文件


class PublishError(Exception):
    """发布失败。编排层负责标记 failed、退避重试（最多 2 次）与告警。"""


class LoginExpired(PublishError):
    """登录态失效。编排层收到后停止该平台所有任务并告警，不得反复撞。"""


class PublisherAdapter(ABC):
    platform: str                # 'toutiao'|'xiaohongshu'|'x'|...

    @abstractmethod
    def validate(self, bundle: PostBundle) -> list[str]:
        """本地校验平台格式要求（字数/图片数/尺寸）。返回问题列表，空=通过。
        不做网络请求。"""

    @abstractmethod
    def publish(self, bundle: PostBundle, account: AccountConfig,
                dry_run: bool) -> PublishResult:
        """执行发布。dry_run=True 走完全流程但最后一步只打日志返回模拟结果。
        必须可安全中断：重复调用的防护由编排层 publishing 状态锁保证，
        但实现内部也不得有"半发布"中间态残留。"""
