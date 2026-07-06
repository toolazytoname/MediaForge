"""域名安全校验（防数据投毒）。

借鉴 Horizon `src/orchestrator.py`（merge_cross_source_duplicates）+
sansan0/TrendRadar `trendradar/crawler/fetcher.py::_check_domain_safety` 的设计：
当 source 声明"返回 URL 的预期域名"时，丢弃 URL 不匹配的条目，
防止 API 被投毒 / 中间人劫持 / 自部署实例被入侵时假数据进库。

设计动机（CLAUDE.md 工作约定 2：契约不可变）：
  - SourceAdapter / RawItem 是 TECH_SPEC §5.1 锁定的契约，不可加字段
  - config.pydantic 模型 extra="forbid"，不可加 expected_domain
  - 解决：side-channel 数据映射 `KNOWN_DOMAIN_RULES`（source_name → 预期域名）
  - RSS 类源默认不在表里 → 不校验（feed items 合法链到任意站）
  - DailyHotApi 等 board 类源落地时填表即生效（board 名 → 平台域名）

本模块是纯函数：不接触 DB、不接触网络、不接触配置。
"""
from __future__ import annotations

from urllib.parse import urlparse

from pipeline.sources.base import RawItem


# ── 默认规则表（side-channel 数据） ──────────────────────────
# 改这个 dict 等价于"开启某个 source 的 URL 域名校验"。
# 不在表里的 source_name 一律不校验（fail-open：宁可放过、不可误杀）。
#
# 未来 dailyhot adapter 落地时在此添加：
#   "dailyhot:baidu": "baidu.com",
#   "dailyhot:weibo": "weibo.com",
#   ...
#
KNOWN_DOMAIN_RULES: dict[str, str] = {
    # 当前无启用项（RSS 源 items 合法链到任意第三方域，不强制）
}


_WWW_PREFIX = "www."


def resolve_expected_domain(source_name: str) -> str | None:
    """查 source_name 对应的预期域名；未登记返回 None（不校验）。

    Args:
        source_name: SourceAdapter.name（如 "rss:hn"、"dailyhot:baidu"）

    Returns:
        预期域名（含端口可缺省）；None = 不校验
    """
    return KNOWN_DOMAIN_RULES.get(source_name)


def check_url(url: str | None, expected_domain: str | None) -> str | None:
    """校验单个 URL 是否符合预期域名。

    Args:
        url: 条目 URL（可能 None/空）
        expected_domain: 预期域名（None 表示不校验）

    Returns:
        None = 通过；str = 拒绝原因（"non_https" / "invalid_url" /
        "domain_mismatch:<host>!=<expected>"）
    """
    if expected_domain is None:
        return None
    if not url:
        # 无 URL 可校验 → 放行（让下游处理 None URL 场景）
        return None

    try:
        parsed = urlparse(url)
    except ValueError:
        return "invalid_url"

    # 必须 https（避免明文 HTTP 链路劫持风险）
    if parsed.scheme != "https":
        return "non_https"

    host = (parsed.hostname or "").lower()
    if host.startswith(_WWW_PREFIX):
        host = host[len(_WWW_PREFIX):]

    expected = expected_domain.lower()
    # 精确匹配 OR 后缀匹配（允许子域：m.example.com 也算 example.com）
    if host != expected and not host.endswith("." + expected):
        return f"domain_mismatch:{host}!={expected}"

    return None


def validate_items(
    items: list[RawItem],
    expected_domain: str | None,
) -> tuple[list[RawItem], int, list[tuple[str, str]]]:
    """批量校验条目 URL 域名。

    Args:
        items: 源 fetch 出的条目列表
        expected_domain: 预期域名（None 表示全部放行）

    Returns:
        (kept_items, dropped_count, dropped_reasons)
          - kept_items: 通过校验的子集（保持输入顺序）
          - dropped_count: 被丢弃的条目数
          - dropped_reasons: [(title, reason), ...] 按输入顺序，便于日志
    """
    if expected_domain is None:
        return list(items), 0, []

    kept: list[RawItem] = []
    reasons: list[tuple[str, str]] = []
    for item in items:
        reason = check_url(item.url, expected_domain)
        if reason is None:
            kept.append(item)
        else:
            reasons.append((item.title, reason))
    return kept, len(reasons), reasons