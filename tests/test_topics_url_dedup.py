"""跨源 URL 去重纯函数单元测试。

借鉴 Horizon src/orchestrator.py::merge_cross_source_duplicates 的设计：
同 URL 多次出现 → 选 content 最长的作代表，其余作 duplicate。

与现有 content_hash 去重（`dedup.py`）正交：
  - content_hash：同标题 + 同域 → 同一条新闻转载
  - merge_by_url：同 URL 但 title 不同（如中英转载） → 同事件转载

合并时机：score 编排层（`runner.py::score_all`），fetch 后、AI 评分前。
"""
from __future__ import annotations

from pipeline.models import Topic, TopicStatus
from pipeline.topics.url_dedup import merge_by_url, normalize_url


def _make_topic(
    id: str, title: str, url: str | None, summary: str = "",
) -> Topic:
    """构造 Topic 测试夹具（schema 字段尽量精简）。"""
    return Topic(
        id=id,
        source="rss:test",
        title=title,
        url=url,
        summary=summary,
        content_hash=f"h_{id}",
        pillar=None,
        score=None,
        score_reason=None,
        status=TopicStatus.RAW.value,
        created_at="2026-07-06T00:00:00+00:00",
        updated_at="2026-07-06T00:00:00+00:00",
    )


# ── normalize_url ────────────────────────────────────────────


def test_normalize_url_strips_www() -> None:
    """www. 前缀剥除（统一 example.com 与 www.example.com）。"""
    assert normalize_url("https://www.example.com/x") == "example.com/x"
    assert normalize_url("https://example.com/x") == "example.com/x"


def test_normalize_url_strips_fragment() -> None:
    """fragment (#section) 剥除。"""
    assert normalize_url("https://example.com/x#section") == "example.com/x"
    assert normalize_url("https://example.com/x#a") == "example.com/x"


def test_normalize_url_strips_trailing_slash() -> None:
    """path 末尾的 / 剥除。"""
    assert normalize_url("https://example.com/x/") == "example.com/x"
    assert normalize_url("https://example.com/x///") == "example.com/x"


def test_normalize_url_lowercase_host() -> None:
    """hostname 小写化。"""
    assert normalize_url("https://Example.COM/x") == "example.com/x"


def test_normalize_url_keeps_path_case() -> None:
    """path 大小写保留（URL path 是大小写敏感的）。"""
    assert normalize_url("https://example.com/X") == "example.com/X"
    assert normalize_url("https://example.com/X") != "example.com/x"


def test_normalize_url_none_or_empty() -> None:
    """None / 空串 → 空串（标记无 URL）。"""
    assert normalize_url(None) == ""
    assert normalize_url("") == ""


def test_normalize_url_invalid_url() -> None:
    """urlparse 失败 → 空串。"""
    assert normalize_url("://broken") == ""


def test_normalize_url_keeps_query() -> None:
    """query string 保留（utm 参数会让两 URL 实际不同，不应合并）。"""
    assert normalize_url("https://example.com/x?utm=a") == "example.com/x?utm=a"
    assert normalize_url("https://example.com/x?utm=a") != normalize_url(
        "https://example.com/x?utm=b"
    )


# ── merge_by_url 基础语义 ───────────────────────────────────


def test_merge_empty_input() -> None:
    """空列表 → 双空。"""
    reps, dups = merge_by_url([])
    assert reps == []
    assert dups == []


def test_merge_single_topic_returns_as_is() -> None:
    """单条 → 原样返回，无 duplicate。"""
    t = _make_topic("t1", "Title", "https://example.com/a")
    reps, dups = merge_by_url([t])
    assert reps == [t]
    assert dups == []


def test_merge_same_url_different_titles_keeps_longest() -> None:
    """同 URL 不同 title → 选 title+summary 最长的作代表。"""
    short = _make_topic("t1", "Short", "https://example.com/a")
    long = _make_topic(
        "t2", "This is a much longer headline for the same news",
        "https://example.com/a",
    )
    reps, dups = merge_by_url([short, long])
    assert len(reps) == 1
    assert reps[0].id == "t2"
    assert dups == [short]


def test_merge_same_url_prefers_longer_summary() -> None:
    """title 短但 summary 长 → summary 加权计入长度。"""
    a = _make_topic("t1", "Same", "https://example.com/a", summary="")
    b = _make_topic(
        "t2", "Same", "https://example.com/a", summary="x" * 5000,
    )
    reps, dups = merge_by_url([a, b])
    assert reps[0].id == "t2"
    assert dups == [a]


def test_merge_different_urls_returns_all() -> None:
    """不同 URL → 全部保留，无 duplicate。"""
    a = _make_topic("t1", "A", "https://a.com/1")
    b = _make_topic("t2", "B", "https://b.com/2")
    c = _make_topic("t3", "C", "https://c.com/3")
    reps, dups = merge_by_url([a, b, c])
    assert len(reps) == 3
    assert dups == []


def test_merge_no_url_topics_pass_through() -> None:
    """无 URL 的 topic 不参与合并（无 key 可合并）。"""
    a = _make_topic("t1", "No URL A", None)
    b = _make_topic("t2", "No URL B", None)
    reps, dups = merge_by_url([a, b])
    # 两条都保留（不去重）
    assert {r.id for r in reps} == {"t1", "t2"}
    assert dups == []


def test_merge_mixed_urls_no_urls_and_duplicates() -> None:
    """混合：URL 不同 / 无 URL / 同 URL 三类同时存在。"""
    same_url_a = _make_topic("t1", "Short", "https://example.com/a")
    same_url_b = _make_topic("t2", "Long long long title", "https://example.com/a")
    other_url = _make_topic("t3", "Other", "https://other.com/x")
    no_url = _make_topic("t4", "NoURL", None)

    reps, dups = merge_by_url(
        [same_url_a, same_url_b, other_url, no_url],
    )
    # reps: t2 (longest of same URL), t3 (other), t4 (no URL) = 3 条
    rep_ids = {r.id for r in reps}
    assert rep_ids == {"t2", "t3", "t4"}
    # dups: t1 (same URL as t2, shorter)
    assert [d.id for d in dups] == ["t1"]


def test_merge_normalizes_url_before_grouping() -> None:
    """URL normalize 后分组：www./fragment/trailing-slash 视为同 URL。"""
    a = _make_topic("t1", "A", "https://example.com/x/")
    b = _make_topic("t2", "B", "https://www.example.com/x#section")
    reps, dups = merge_by_url([a, b])
    assert len(reps) == 1
    assert len(dups) == 1


def test_merge_three_way_same_url_keeps_one_rep_two_dups() -> None:
    """同 URL 三条 → 1 代表 + 2 duplicate。"""
    short = _make_topic("t1", "x", "https://e.com/1")
    medium = _make_topic("t2", "xx", "https://e.com/1")
    longest = _make_topic("t3", "xxx", "https://e.com/1")
    reps, dups = merge_by_url([short, medium, longest])
    assert reps[0].id == "t3"
    assert {d.id for d in dups} == {"t1", "t2"}


def test_merge_preserves_input_order_in_reps() -> None:
    """representatives 列表保留代表条在输入中的首次出现位置。"""
    a = _make_topic("t1", "A-only", "https://a.com/")
    dup_x1 = _make_topic("t2", "X1", "https://x.com/")
    dup_x2 = _make_topic("t3", "X2", "https://x.com/")
    b = _make_topic("t4", "B-only", "https://b.com/")

    reps, dups = merge_by_url([a, dup_x1, dup_x2, b])
    # 顺序：a, dup_x1 (代表), b
    assert [r.id for r in reps] == ["t1", "t2", "t4"]
    assert [d.id for d in dups] == ["t3"]