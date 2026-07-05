"""config.py 校验测试（TECH_SPEC §6 契约）。

覆盖：
  - config.example.yaml 加载成功
  - 字段类型错误 → ValueError，错误信息含字段路径
  - 必填字段缺失 → ValueError
  - 多余字段 → ValueError（extra=forbid 拒绝）
  - 枚举字段 Literal 校验
  - Discriminated union（sources by type, platforms by kind）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.config import (
    AppConfig,
    load_config,
)


# ── example yaml ──────────────────────────────────────────

def test_load_example_yaml_succeeds():
    """仓库自带的 config.example.yaml 必须能加载（验收硬指标）。"""
    c = load_config("config.example.yaml")
    assert isinstance(c, AppConfig)


def test_example_yaml_top_level_fields():
    c = load_config("config.example.yaml")
    assert c.timezone == "Asia/Shanghai"
    assert {p.id for p in c.pillars} == {"ai_daily", "oss_review"}
    assert {s.name for s in c.sources} >= {
        "rss:hn", "rss:github_trending", "dailyhot",
    }
    assert c.llm.tiers.cheap == "claude-haiku-4-5-20251001"
    assert c.budget.monthly_usd == 80.0
    assert c.gate.threshold_total == 24
    assert c.gate.threshold_each == 6
    assert c.publish.enabled is False
    assert c.video.engine == "mpt"


def test_example_yaml_sources_discriminated_by_type():
    c = load_config("config.example.yaml")
    rss = next(s for s in c.sources if s.name == "rss:hn")
    dailyhot = next(s for s in c.sources if s.name == "dailyhot")
    assert rss.type == "rss"
    assert hasattr(rss, "url")
    assert dailyhot.type == "dailyhot"
    assert hasattr(dailyhot, "base_url")
    assert "zhihu" in dailyhot.boards


def test_example_yaml_platforms_discriminated_by_kind():
    c = load_config("config.example.yaml")
    assert c.platforms.x.kind == "api"
    assert c.platforms.toutiao.kind == "playwright"
    assert c.platforms.xiaohongshu.kind == "playwright"
    # api 平台用 credentials，playwright 用 cookies
    assert c.platforms.x.accounts[0].credentials.endswith("x_main.json")
    assert c.platforms.toutiao.accounts[0].cookies.endswith("toutiao_main.json")


# ── 错误信息含字段路径（验收硬指标） ──────────────────────

def test_wrong_type_for_gate_threshold_total_raises_with_field_path(tmp_path):
    """把 gate.threshold_total 改成字符串 → ValueError，且错误信息含 'gate.threshold_total'。"""
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
pillars:
  - id: ai_daily
    name: X
    description: Y
    scoring_hint: Z
llm:
  tiers: {cheap: a, creative: b, critical: c}
gate:
  threshold_total: "twenty-four"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "gate.threshold_total" in msg
    assert "invalid config" in msg


def test_missing_required_field_raises(tmp_path):
    """缺 pillars 必填字段 → ValueError 指明路径。"""
    p = tmp_path / "missing.yaml"
    p.write_text(
        """
llm:
  tiers: {cheap: a, creative: b, critical: c}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    assert "pillars" in str(exc_info.value)


def test_extra_field_rejected(tmp_path):
    """未在模型中定义的字段 → ValueError（extra=forbid）。"""
    p = tmp_path / "extra.yaml"
    p.write_text(
        """
pillars:
  - id: x
    name: y
    description: z
    scoring_hint: w
unknown_top_level: 1
llm:
  tiers: {cheap: a, creative: b, critical: c}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    assert "unknown_top_level" in str(exc_info.value)


def test_pillar_missing_field_raises(tmp_path):
    """Pillar 缺 scoring_hint → ValueError 指明路径含 'pillars.0.scoring_hint'。"""
    p = tmp_path / "no_hint.yaml"
    p.write_text(
        """
pillars:
  - id: x
    name: y
    description: z
llm:
  tiers: {cheap: a, creative: b, critical: c}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    assert "scoring_hint" in str(exc_info.value)


# ── 枚举 / Literal 校验 ──────────────────────────────────

def test_invalid_video_engine_rejected(tmp_path):
    p = tmp_path / "bad_video.yaml"
    p.write_text(
        """
pillars:
  - {id: x, name: y, description: z, scoring_hint: w}
llm:
  tiers: {cheap: a, creative: b, critical: c}
video:
  engine: "not-a-real-engine"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    assert "video.engine" in str(exc_info.value)


def test_invalid_image_gen_provider_rejected(tmp_path):
    p = tmp_path / "bad_provider.yaml"
    p.write_text(
        """
pillars:
  - {id: x, name: y, description: z, scoring_hint: w}
llm:
  tiers: {cheap: a, creative: b, critical: c}
image_gen:
  provider: "midjourney"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    assert "image_gen.provider" in str(exc_info.value)


def test_invalid_source_type_rejected(tmp_path):
    p = tmp_path / "bad_source.yaml"
    p.write_text(
        """
pillars:
  - {id: x, name: y, description: z, scoring_hint: w}
llm:
  tiers: {cheap: a, creative: b, critical: c}
sources:
  - name: foo
    type: "reddit"
    url: https://reddit.com
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    # discriminator failure: surfaces at sources.0
    assert "sources" in str(exc_info.value)


# ── 文件缺失 / 空文件 ───────────────────────────────────

def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "no_such.yaml")


def test_empty_yaml_raises(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_config(p)


# ── 默认值兜底 ───────────────────────────────────────────

def test_minimal_config_uses_defaults(tmp_path):
    """最小配置（仅必填）能加载，其余走默认值。"""
    p = tmp_path / "min.yaml"
    p.write_text(
        """
pillars:
  - {id: x, name: y, description: z, scoring_hint: w}
llm:
  tiers: {cheap: a, creative: b, critical: c}
""",
        encoding="utf-8",
    )
    c = load_config(p)
    assert c.timezone == "Asia/Shanghai"
    assert c.topics.daily_quota == 5
    assert c.gate.threshold_total == 24
    assert c.publish.enabled is False
    assert c.publish.allowed_platforms == []
    assert c.video.engine == "mpt"
    assert c.image_gen.provider == "none"
    assert c.render.engine == "template"