"""M10-3 serialize.py 单元测试。

覆盖：
  - topic_dict / content_dict / pub_dict / metric_dict 字段 1:1
  - formats / inline_images tuple 转 list
  - gate_scores dict | None 透传（None → JSON null）
  - metric_dict 默认丢 raw；include_raw=True 时带回
  - list_content_files 有/无目录 / 已知路径枚举
  - content_image_urls cover + inline URL 拼装
  - write_canonical_jailed 越狱防护 + tmp→rename 原子写
"""
from __future__ import annotations

import json

import pytest

from pipeline.models import (
    Content, ContentStatus,
    Metric,
    Publication, PublicationStatus,
    Topic, TopicStatus,
)
from pipeline.webui.serialize import (
    content_dict,
    content_image_urls,
    content_output_url_prefix,
    list_content_files,
    metric_dict,
    pub_dict,
    topic_dict,
    write_canonical_jailed,
)


# ── Fixtures & helpers ──────────────────────────────────────


def _topic(**kw) -> Topic:
    base = dict(
        id="t_001", source="rss:hn", title="T",
        url="https://example.com", summary="summary",
        content_hash="h1", pillar="ai_daily", score=7.5,
        score_reason="good", status=TopicStatus.SCORED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Topic(**base)


def _content(**kw) -> Content:
    base = dict(
        id="c_001", topic_id="t_001", pillar="ai_daily",
        title="C", canonical_path="output/2026-07-05/c_001/canonical.md",
        formats=("toutiao", "xiaohongshu", "x"),
        gate_score_total=27.0, gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="通过", status=ContentStatus.GATED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
        cover_path=None, inline_images=(),
    )
    base.update(kw)
    return Content(**base)


def _pub(**kw) -> Publication:
    base = dict(
        id="p_001", content_id="c_001", platform="x",
        account_id="main",
        scheduled_at="2026-07-05T10:00:00+00:00",
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=PublicationStatus.QUEUED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Publication(**base)


def _metric(**kw) -> Metric:
    base = dict(
        publication_id="p_001",
        collected_at="2026-07-05T12:00:00+00:00",
        views=100, likes=10, comments=2, shares=1,
        followers_delta=0, raw='{"trace": "x"}',
    )
    base.update(kw)
    return Metric(**base)


# ── topic_dict ─────────────────────────────────────────────


class TestTopicDict:
    def test_field_mapping(self):
        d = topic_dict(_topic())
        assert d["id"] == "t_001"
        assert d["source"] == "rss:hn"
        assert d["score"] == 7.5
        assert d["status"] == TopicStatus.SCORED

    def test_all_keys_present(self):
        d = topic_dict(_topic())
        t = _topic()
        for f in t.__dataclass_fields__:
            assert f in d

    def test_json_roundtrip(self):
        d = topic_dict(_topic())
        # tuple/dict/None 都应能 JSON 序列化
        out = json.dumps(d)
        assert isinstance(out, str)
        restored = json.loads(out)
        assert restored["id"] == d["id"]


# ── content_dict ───────────────────────────────────────────


class TestContentDict:
    def test_tuple_fields_become_list(self):
        d = content_dict(_content())
        assert d["formats"] == ["toutiao", "xiaohongshu", "x"]
        assert isinstance(d["formats"], list)
        assert isinstance(d["inline_images"], list)

    def test_gate_scores_preserved(self):
        d = content_dict(_content())
        assert d["gate_scores"] == {"info": 9, "fun": 9, "view": 9}

    def test_gate_scores_none_becomes_null(self):
        d = content_dict(_content(gate_scores=None))
        assert d["gate_scores"] is None
        assert json.dumps(d)  # None → null 合法

    def test_all_keys_present(self):
        d = content_dict(_content())
        c = _content()
        for f in c.__dataclass_fields__:
            assert f in d


# ── pub_dict ───────────────────────────────────────────────


class TestPubDict:
    def test_field_mapping(self):
        d = pub_dict(_pub())
        assert d["id"] == "p_001"
        assert d["platform"] == "x"
        assert d["retry_count"] == 0

    def test_all_keys_present(self):
        d = pub_dict(_pub())
        p = _pub()
        for f in p.__dataclass_fields__:
            assert f in d


# ── metric_dict ────────────────────────────────────────────


class TestMetricDict:
    def test_default_drops_raw(self):
        d = metric_dict(_metric())
        assert "raw" not in d
        assert d["views"] == 100

    def test_include_raw_brings_back(self):
        d = metric_dict(_metric(), include_raw=True)
        assert d["raw"] == '{"trace": "x"}'

    def test_none_ints_preserved(self):
        d = metric_dict(_metric(views=None, likes=None))
        assert d["views"] is None
        assert d["likes"] is None


# ── list_content_files ──────────────────────────────────────


class TestListContentFiles:
    def test_directory_exists_marks_known_files(self, tmp_path, monkeypatch):
        # 把 content 的 canonical_path 指向 tmp_path 下
        c = _content(canonical_path=str(tmp_path / "canonical.md"))
        # 真正造几个文件
        (tmp_path / "toutiao.md").write_text("toutiao body", encoding="utf-8")
        (tmp_path / "xiaohongshu").mkdir()
        (tmp_path / "xiaohongshu" / "slides.json").write_text("{}", encoding="utf-8")
        out = list_content_files(c)
        # 已知路径全部返回
        assert len(out) >= 15
        toutiao = next(f for f in out if f["path"] == "toutiao.md")
        assert toutiao["exists"] is True
        assert toutiao["size"] == len("toutiao body")
        assert toutiao["kind"] == "text"
        assert toutiao["platform"] == "toutiao"
        slides = next(f for f in out if f["path"] == "xiaohongshu/slides.json")
        assert slides["exists"] is True
        # cover 不存在
        cover = next(f for f in out if f["path"] == "cover.png")
        assert cover["exists"] is False

    def test_directory_missing_returns_all_known_as_nonexistent(self, tmp_path):
        c = _content(canonical_path=str(tmp_path / "missing" / "canonical.md"))
        out = list_content_files(c)
        assert all(f["exists"] is False for f in out)
        assert all(f["size"] == 0 for f in out)
        assert len(out) >= 15

    def test_invalid_canonical_path_returns_empty_known(self):
        # canonical_path 不以 canonical.md 结尾 → 无法反推目录
        c = _content(canonical_path="output/random.txt")
        out = list_content_files(c)
        assert all(f["exists"] is False for f in out)


# ── content_image_urls ──────────────────────────────────────


class TestContentImageUrls:
    def test_cover_path_with_output_prefix(self):
        c = _content(cover_path="output/2026-07-05/c_001/cover.png")
        urls = content_image_urls(c)
        assert urls["cover"] == "/output/2026-07-05/c_001/cover.png"

    def test_cover_path_without_prefix(self):
        c = _content(cover_path="2026-07-05/c_001/cover.png")
        urls = content_image_urls(c)
        assert urls["cover"] == "/output/2026-07-05/c_001/cover.png"

    def test_no_cover(self):
        c = _content(cover_path=None)
        urls = content_image_urls(c)
        assert urls["cover"] is None

    def test_inline_images(self):
        c = _content(inline_images=(
            "output/2026-07-05/c_001/images/inline-1.png",
            "output/2026-07-05/c_001/images/inline-2.png",
        ))
        urls = content_image_urls(c)
        assert urls["inline"] == [
            "/output/2026-07-05/c_001/images/inline-1.png",
            "/output/2026-07-05/c_001/images/inline-2.png",
        ]

    def test_empty_inline(self):
        c = _content(inline_images=())
        urls = content_image_urls(c)
        assert urls["inline"] == []


class TestContentOutputUrlPrefix:
    def test_normal_canonical_path(self):
        c = _content(canonical_path="output/2026-07-05/c_001/canonical.md")
        assert content_output_url_prefix(c) == "/output/2026-07-05/c_001/"

    def test_non_canonical_filename_returns_empty(self):
        c = _content(canonical_path="output/2026-07-05/c_001/other.md")
        assert content_output_url_prefix(c) == ""


# ── write_canonical_jailed ─────────────────────────────────


class TestWriteCanonicalJailed:
    def test_atomic_write_creates_file(self, tmp_path):
        c = _content(canonical_path=str(tmp_path / "canonical.md"))
        n = write_canonical_jailed(c, "# Hello")
        assert n == len("# Hello")
        assert (tmp_path / "canonical.md").read_text(encoding="utf-8") == "# Hello"

    def test_overwrite_existing(self, tmp_path):
        c = _content(canonical_path=str(tmp_path / "canonical.md"))
        write_canonical_jailed(c, "old")
        write_canonical_jailed(c, "new")
        assert (tmp_path / "canonical.md").read_text(encoding="utf-8") == "new"

    def test_creates_parent_dir(self, tmp_path):
        target = tmp_path / "subdir" / "canonical.md"
        c = _content(canonical_path=str(target))
        write_canonical_jailed(c, "x")
        assert target.read_text(encoding="utf-8") == "x"

    def test_rejects_non_string(self, tmp_path):
        c = _content(canonical_path=str(tmp_path / "canonical.md"))
        with pytest.raises(TypeError, match="must be str"):
            write_canonical_jailed(c, b"bytes")  # type: ignore

    def test_rejects_path_escape_with_dotdot(self, tmp_path):
        # 直接测 _safe_resolve：传显式 base_dir，路径含 .. 越狱
        from pipeline.webui.serialize import _safe_resolve
        # 路径含 .. 解析后跳出 base_dir
        escape_path = str(tmp_path / ".." / "evil" / "canonical.md")
        result = _safe_resolve(escape_path, tmp_path)
        assert result is None

    def test_rejects_empty_path(self, tmp_path):
        from pipeline.webui.serialize import _safe_resolve
        assert _safe_resolve("", tmp_path) is None

    def test_safe_resolve_accepts_inside(self, tmp_path):
        from pipeline.webui.serialize import _safe_resolve
        target = str(tmp_path / "canonical.md")
        result = _safe_resolve(target, tmp_path)
        assert result is not None
        assert result.name == "canonical.md"

    def test_safe_resolve_rejects_nul(self, tmp_path):
        from pipeline.webui.serialize import _safe_resolve
        assert _safe_resolve("/tmp/\x00evil", tmp_path) is None
