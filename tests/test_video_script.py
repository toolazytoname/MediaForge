"""M5-1 口播稿派生测试。

覆盖契约（HARD_PARTS §6 决策 2）：
- canonical → LLM 调用 → 解析 JSON
- 解析失败 + 字段缺失 + 字段越界 → CreateError
- 防幻觉条款：prompt 模板含关键提示
- 关键词列表 / 时长 / hook_score 字段校验
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.utils.errors import CreateError
from pipeline.creators.video_script import (
    _parse_video_script,
    derive_video_script,
)


# ── _parse_video_script 纯函数 ─────────────────────────


def test_parse_valid_dict() -> None:
    raw = {
        "script": "这是口播稿正文",
        "keywords": ["AI", "video"],
        "duration_s": 75,
        "hook_score": 8,
    }
    out = _parse_video_script(raw)
    assert out["script"] == "这是口播稿正文"
    assert out["keywords"] == ["AI", "video"]
    assert out["duration_s"] == 75
    assert out["hook_score"] == 8


def test_parse_strips_whitespace() -> None:
    raw = {
        "script": "  前后带空格的脚本  ",
        "keywords": ["  spaced  ", ""],
        "duration_s": 75,
        "hook_score": 8,
    }
    out = _parse_video_script(raw)
    assert out["script"] == "前后带空格的脚本"
    assert "spaced" in out["keywords"]


def test_parse_rejects_non_dict() -> None:
    with pytest.raises(CreateError, match="not dict"):
        _parse_video_script("just a string")  # type: ignore[arg-type]


def test_parse_rejects_missing_script() -> None:
    with pytest.raises(CreateError, match="missing or empty"):
        _parse_video_script({
            "keywords": ["a"], "duration_s": 75, "hook_score": 8,
        })


def test_parse_rejects_empty_script() -> None:
    with pytest.raises(CreateError, match="missing or empty"):
        _parse_video_script({
            "script": "   ", "keywords": ["a"],
            "duration_s": 75, "hook_score": 8,
        })


def test_parse_rejects_non_list_keywords() -> None:
    with pytest.raises(CreateError, match="keywords"):
        _parse_video_script({
            "script": "ok",
            "keywords": "not a list",  # type: ignore[list-item]
            "duration_s": 75, "hook_score": 8,
        })


def test_parse_rejects_duration_out_of_range_low() -> None:
    with pytest.raises(CreateError, match="duration_s"):
        _parse_video_script({
            "script": "ok", "keywords": ["a"],
            "duration_s": 5, "hook_score": 8,
        })


def test_parse_rejects_duration_out_of_range_high() -> None:
    with pytest.raises(CreateError, match="duration_s"):
        _parse_video_script({
            "script": "ok", "keywords": ["a"],
            "duration_s": 200, "hook_score": 8,
        })


def test_parse_rejects_hook_score_out_of_range() -> None:
    with pytest.raises(CreateError, match="hook_score"):
        _parse_video_script({
            "script": "ok", "keywords": ["a"],
            "duration_s": 75, "hook_score": 15,
        })


# ── derive_video_script 集成测试（mock LLM） ───────────


def _fake_complete_json_ok(prompt, *, stage, ref_id, model_tier,
                            max_tokens, parse):
    return parse({
        "script": "这是口播稿",
        "keywords": ["AI", "tool"],
        "duration_s": 70,
        "hook_score": 8,
    })


def _make_prompt(tmp_path: Path) -> Path:
    """写一个最小合法 prompt 文件供测试用。"""
    p = tmp_path / "prompt.md"
    p.write_text(
        "# 视频口播稿\n\n{canonical_content}\n",
        encoding="utf-8",
    )
    return p


def test_derive_calls_llm_and_parses(tmp_path: Path) -> None:
    """正常路径：LLM 返回 → parse → dict。"""
    prompt_path = _make_prompt(tmp_path)
    with patch(
        "pipeline.creators.video_script.llm_mod.complete_json",
        _fake_complete_json_ok,
    ):
        out = derive_video_script(
            "canonical 全文...",
            ref_id="c_test",
            prompt_path=prompt_path,
        )
    assert out["script"] == "这是口播稿"
    assert out["duration_s"] == 70


def test_derive_propagates_create_error_from_parse(tmp_path: Path) -> None:
    """LLM 返回不合法 → parse 抛 CreateError → 透传。"""
    prompt_path = _make_prompt(tmp_path)

    def bad_llm(prompt, *, stage, ref_id, model_tier,
                max_tokens, parse):
        return parse({"script": "only this"})  # 缺 keywords/duration
    with patch(
        "pipeline.creators.video_script.llm_mod.complete_json",
        bad_llm,
    ):
        with pytest.raises(CreateError):
            derive_video_script(
                "x", ref_id="c",
                prompt_path=prompt_path,
            )


def test_prompt_contains_key_anti_hallucination_clause() -> None:
    """防幻觉条款必须在 prompt 里（M2-1 移植到 M5-1）。"""
    from pipeline.creators.video_script import _PROMPT_PATH
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "canonical" in text.lower()  # 必须引用 canonical 内容
    # 防幻觉关键词
    assert "不要复述" in text or "不写未明确" in text or "防幻觉" in text or "幻觉" in text


def test_prompt_contains_hook_front_requirement() -> None:
    """钩子前置是口播稿硬要求。"""
    from pipeline.creators.video_script import _PROMPT_PATH
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "钩子" in text or "hook" in text.lower()


def test_prompt_requires_english_keywords() -> None:
    """Pexels 中文搜索差 → 关键词必须英文。"""
    from pipeline.creators.video_script import _PROMPT_PATH
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "英文" in text or "english" in text.lower()