"""数据模型与状态机契约。

本文件是 TECH_SPEC §4 的代码化，是全系统契约的核心。
实现任务（M0-2 起）只能新增辅助函数，不得修改已有字段与转移表；
发现契约问题 → 在 docs/TASKS.md 记录，停止修改。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TopicStatus(str, Enum):
    RAW = "raw"
    SCORED = "scored"
    SELECTED = "selected"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class ContentStatus(str, Enum):
    DRAFT = "draft"
    GATED = "gated"
    APPROVED = "approved"
    REJECTED_BY_HUMAN = "rejected_by_human"
    DISCARDED = "discarded"
    FAILED = "failed"
    DONE = "done"


class PublicationStatus(str, Enum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 合法状态转移表。db.transition() 强制校验，非法转移抛 IllegalTransition。
# "failed → queued" 仅允许由 reset 命令 / UI retry 触发。
TOPIC_TRANSITIONS: dict[str, set[str]] = {
    TopicStatus.RAW: {TopicStatus.SCORED, TopicStatus.REJECTED},
    TopicStatus.SCORED: {TopicStatus.SELECTED, TopicStatus.REJECTED},
    TopicStatus.SELECTED: {TopicStatus.CONSUMED},
}

CONTENT_TRANSITIONS: dict[str, set[str]] = {
    ContentStatus.DRAFT: {ContentStatus.GATED, ContentStatus.DISCARDED, ContentStatus.FAILED},
    ContentStatus.GATED: {ContentStatus.APPROVED, ContentStatus.REJECTED_BY_HUMAN},
    ContentStatus.APPROVED: {ContentStatus.DONE},
}

PUBLICATION_TRANSITIONS: dict[str, set[str]] = {
    PublicationStatus.QUEUED: {PublicationStatus.PUBLISHING, PublicationStatus.CANCELLED},
    PublicationStatus.PUBLISHING: {PublicationStatus.PUBLISHED, PublicationStatus.FAILED},
    PublicationStatus.FAILED: {PublicationStatus.QUEUED},
}


@dataclass(frozen=True)
class Topic:
    id: str                      # 't_' + 8hex（utils.ids.new_id）
    source: str                  # 'rss:hn' 等
    title: str
    url: str | None
    summary: str | None
    content_hash: str            # sha256(normalize(title) + domain)，UNIQUE
    pillar: str | None           # score 阶段填
    score: float | None
    score_reason: str | None
    status: str
    created_at: str              # ISO8601 UTC
    updated_at: str


@dataclass(frozen=True)
class Content:
    id: str                      # 'c_' + 8hex
    topic_id: str                # UNIQUE，Topic 1:1 Content
    pillar: str
    title: str
    canonical_path: str          # output/ 相对路径
    formats: tuple[str, ...]     # 已生成的派生格式平台名
    gate_score_total: float | None
    gate_scores: dict | None     # {"info": n, "fun": n, "view": n}
    gate_verdict: str | None
    status: str
    created_at: str
    updated_at: str
    # M-x：封面图 + 文中插图（仅对最终要发布的内容生成，详见 HARD_PARTS §10.x）
    cover_path: str | None = None    # output/.../cover.png 相对路径；None = 未生成
    inline_images: tuple[str, ...] = ()  # output/.../images/inline-N.png 路径列表


@dataclass(frozen=True)
class Publication:
    id: str                      # 'p_' + 8hex
    content_id: str
    platform: str                # 'toutiao'|'xiaohongshu'|'x'|...
    account_id: str              # config 账号别名
    scheduled_at: str            # ISO8601 UTC
    published_at: str | None
    platform_post_id: str | None
    platform_url: str | None
    error: str | None
    retry_count: int
    status: str
    created_at: str
    updated_at: str
    # 数据库层 UNIQUE(content_id, platform, account_id) —— 防重复发布最后防线


@dataclass(frozen=True)
class Metric:
    publication_id: str
    collected_at: str
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    followers_delta: int | None
    raw: str | None              # 平台原始返回 JSON
