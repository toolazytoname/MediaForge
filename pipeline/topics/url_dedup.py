"""跨源 URL 去重（防同一新闻多源转载）。

借鉴 Horizon src/orchestrator.py::merge_cross_source_duplicates 的设计：
同 URL 多次出现 → 选 content 最长的作 primary（代表），其余作 duplicate。

与 `dedup.py::content_hash` 正交（不替换）：
  - content_hash：同 normalize_title + 同域名 → "同标题转载"
  - merge_by_url：同 URL 但 title 不同 → "同事件转载"（如中英 / 简繁）

合并时机：score 编排层（`runner.py::score_all`），fetch 后、AI 评分前。
代表条进入 score；duplicate 不参与评分也不参与 selector（避免同主题
多次占用 daily_quota）。

已知限制（保留到下一个 task）：
  - 本模块只在内存中合并；DB 中重复条目仍保留 raw 状态
  - 下次 cron score 时会再合并一次（同样的 duplicates 列表）
  - LLM 浪费：每条 dup 多消耗一次 score 调用（cheap 档，单价低）
  - 彻底解决需要 topics 表加 `merged_into_topic_id` 字段（动契约，TODO）

契约：不动 SourceAdapter / RawItem / TECH_SPEC §3 schema / models.Topic。
纯函数模块，不接触 DB、不接触网络。
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from pipeline.models import Topic


_WWW_PREFIX = "www."


def normalize_url(url: str | None) -> str:
    """归一化 URL 用于去重 key：剥 www./trailing slash、host 小写，保留 query。

    Args:
        url: 原始 URL（可能 None / 空 / 非法）

    Returns:
        归一化后的字符串；空串表示"无 key"（该 URL 不参与合并）

    Notes:
        - fragment (#section) 剥除（前端跳转锚点，不影响内容）
        - query (?utm=...) 保留（utm 参数让两 URL 实际不同——保守不去重）
        - 无 hostname（malformed URL）→ 空串
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host:
        # malformed：无 hostname（如 "://broken"、"not-a-url"）→ 不参与合并
        return ""
    if host.startswith(_WWW_PREFIX):
        host = host[len(_WWW_PREFIX):]
    # path 去末尾 /；fragment 剥除；query 保留
    path = parsed.path.rstrip("/")
    suffix = "?" + parsed.query if parsed.query else ""
    return f"{host}{path}{suffix}"


def merge_by_url(
    topics: list[Topic],
) -> tuple[list[Topic], list[Topic]]:
    """按 URL 合并 topics。

    Args:
        topics: 待合并的 topic 列表

    Returns:
        (representatives, duplicates):
          - representatives：合并后的 topic 列表
            （同 URL 多条只保留一条代表；无 URL 全部保留）
            顺序按输入中"代表条"的首次出现位置
          - duplicates：被合并掉的 topic 列表
            （同 URL 但不是代表条的全部进入此处；用于日志/统计）

    合并规则：
      1. 无 URL 的 topic 不参与合并（无 key），全部进 representatives
      2. URL normalize 后分组
      3. 同 URL 多条 → 选 title + summary 长度和最大的作代表
      4. URL 不同 / URL 为空 → 各自进 representatives
    """
    if not topics:
        return [], []

    groups: dict[str, list[Topic]] = defaultdict(list)
    no_url: list[Topic] = []

    for t in topics:
        key = normalize_url(t.url)
        if not key:
            no_url.append(t)
        else:
            groups[key].append(t)

    representatives: list[Topic] = list(no_url)
    duplicates: list[Topic] = []

    for group in groups.values():
        if len(group) == 1:
            representatives.append(group[0])
            continue
        # 同 URL 多条 → 选 content 最长的
        primary = max(
            group,
            key=lambda t: len((t.title or "") + (t.summary or "")),
        )
        representatives.append(primary)
        for t in group:
            if t.id != primary.id:
                duplicates.append(t)

    return representatives, duplicates