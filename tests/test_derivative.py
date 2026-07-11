"""M2-3 派生格式生成测试。

覆盖：
  - 三个 parse 函数（_parse_toutiao / _parse_xhs / _parse_x）字段校验
  - 三个派生函数（derive_toutiao / derive_xiaohongshu / derive_x）调 LLM + parse
  - derive_one 单条编排：写文件、更新 contents.formats、单平台失败隔离
  - run_derivative 批量：取 gated、循环派生
  - 文件格式合规：X thread 每条 ≤ 260 字符、小红书 slides schema
  - 防御性：JSON 围栏剥离、坏 JSON 重试、CreateError 单平台隔离
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.creators import derivative as deriv
from pipeline.creators import llm as llm_mod
from pipeline.creators.derivative import (
    DerivativeResult,
    ToutiaoOutput,
    XiaohongshuOutput,
    XiaohongshuSlide,
    XOutput,
    _parse_toutiao,
    _parse_xhs,
    _parse_x,
    _strip_fence,
    derive_one,
    derive_toutiao,
    derive_xiaohongshu,
    derive_x,
    run_derivative,
)
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.models import (
    Content,
    ContentStatus,
    Topic,
    TopicStatus,
)
from pipeline.sources.dedup import content_hash


# ── helpers ────────────────────────────────────────────────

class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[str], *, fail_remaining: bool = False):
        self._responses = list(responses)
        self._fail_remaining = fail_remaining
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if self._fail_remaining:
            raise llm_mod.RetryableError("mocked")
        if not self._responses:
            raise llm_mod.RetryableError("no scripted")
        return CompletionResult(text=self._responses.pop(0),
                                 input_tokens=200, output_tokens=300)


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _seed_gated_content(
    tmp_path, conn,
    *,
    content_id: str = "c_test0001",
    title: str = "Test Article",
    canonical_md: str | None = None,
) -> Content:
    """种 topic + gated content + 写 canonical.md。"""
    topic_id = f"t_{content_id[2:]}"
    topic = Topic(
        id=topic_id, source="rss:test", title=title, url=None, summary=None,
        content_hash=content_hash(title, None),
        pillar="ai", score=8.0, score_reason="ok",
        status=TopicStatus.CONSUMED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)

    out_dir = tmp_path / "output" / "2026-07-05" / content_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if canonical_md is None:
        canonical_md = "# Test\n\nThis is a long article about AI tools..."
    (out_dir / "canonical.md").write_text(canonical_md, encoding="utf-8")

    content = Content(
        id=content_id, topic_id=topic_id, pillar="ai", title=title,
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=27.0,
        gate_scores={"info": 9, "fun": 9, "view": 9},
        gate_verdict="好",
        status=ContentStatus.GATED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_content(conn, content)
    return content


@pytest.fixture(autouse=True)
def reset_provider(tmp_path):
    set_provider(ScriptedProvider([]))
    conn = _open_db(tmp_path)
    llm_mod.init_db_conn(conn)
    yield
    llm_mod.init_db_conn(None)  # type: ignore[arg-type]
    conn.close()
    set_provider(ScriptedProvider([]))


# ── 围栏剥离 ───────────────────────────────────────────────

def test_strip_fence_no_fence() -> None:
    assert _strip_fence("plain text") == "plain text"


def test_strip_fence_json_fence() -> None:
    s = '```json\n{"k": "v"}\n```'
    assert _strip_fence(s) == '{"k": "v"}'


def test_strip_fence_plain_fence() -> None:
    s = '```\n{"k": "v"}\n```'
    assert _strip_fence(s) == '{"k": "v"}'


def test_strip_fence_json_no_newline_after_tag() -> None:
    """LLM 偶尔产出 `` ```json{...}\\n``` ``——围栏标签后无换行。"""
    s = '```json{"k": "v"}\n```'
    assert _strip_fence(s) == '{"k": "v"}'


def test_strip_fence_multiline_json() -> None:
    """多行 JSON 围栏。"""
    s = '```json\n{\n  "k": "v",\n  "x": 1\n}\n```'
    out = _strip_fence(s)
    assert json.loads(out) == {"k": "v", "x": 1}


# ── _parse_toutiao ─────────────────────────────────────────

def _toutiao_payload() -> dict:
    return {
        "titles": ["标题A", "标题B", "标题C"],
        "body": "# 选定\n\n正文...",
    }


def test_parse_toutiao_valid() -> None:
    out = _parse_toutiao(json.dumps(_toutiao_payload()))
    assert isinstance(out, ToutiaoOutput)
    assert len(out.titles) == 3
    assert out.body.startswith("# 选定")


def test_parse_toutiao_invalid_json_raises() -> None:
    from pipeline.utils.errors import CreateError
    with pytest.raises(CreateError):
        _parse_toutiao("not json")


def test_parse_toutiao_wrong_titles_count() -> None:
    from pipeline.utils.errors import CreateError
    payload = _toutiao_payload()
    payload["titles"] = ["only one"]
    with pytest.raises(CreateError, match="must be list of 3"):
        _parse_toutiao(json.dumps(payload))


def test_parse_toutiao_title_too_long() -> None:
    from pipeline.utils.errors import CreateError
    payload = _toutiao_payload()
    payload["titles"] = ["x" * 37, "b", "c"]  # 36 字是头条硬上限
    with pytest.raises(CreateError, match="too long"):
        _parse_toutiao(json.dumps(payload))


def test_parse_toutiao_missing_body() -> None:
    from pipeline.utils.errors import CreateError
    payload = _toutiao_payload()
    payload["body"] = ""
    with pytest.raises(CreateError, match="body missing"):
        _parse_toutiao(json.dumps(payload))


def test_parse_toutiao_handles_fenced_response() -> None:
    payload = _toutiao_payload()
    text = "```json\n" + json.dumps(payload) + "\n```"
    out = _parse_toutiao(text)
    assert len(out.titles) == 3


# ── _parse_xhs ────────────────────────────────────────────

def _xhs_payload() -> dict:
    return {
        "slides": [
            {"type": "cover", "title": "封面", "body": "x" * 50},
            {"type": "content", "title": "观点1", "body": "x" * 60},
            {"type": "content", "title": "观点2", "body": "x" * 70},
            {"type": "content", "title": "观点3", "body": "x" * 80},
            {"type": "action", "title": "关注", "body": "x" * 30},
        ],
        "caption": "x" * 100,
        "tags": ["AI", "工具", "效率", "深度", "评测"],
    }


def test_parse_xhs_valid() -> None:
    out = _parse_xhs(json.dumps(_xhs_payload()))
    assert isinstance(out, XiaohongshuOutput)
    assert len(out.slides) == 5
    assert out.slides[0].type == "cover"
    assert out.slides[-1].type == "action"
    assert len(out.tags) == 5


def test_parse_xhs_too_few_slides() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["slides"] = payload["slides"][:3]  # 只剩 3 张
    with pytest.raises(CreateError, match="slides count"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_too_many_slides() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["slides"] = payload["slides"] * 2  # 10 张
    with pytest.raises(CreateError, match="slides count"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_first_slide_not_cover() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["slides"][0]["type"] = "content"
    with pytest.raises(CreateError, match="must be 'cover'"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_last_slide_not_action() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["slides"][-1]["type"] = "content"
    with pytest.raises(CreateError, match="must be 'action'"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_slide_body_too_long() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["slides"][1]["body"] = "x" * 101  # 100 字是单卡上限
    with pytest.raises(CreateError, match="slide body too long"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_caption_too_short() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["caption"] = "太短"
    with pytest.raises(CreateError, match="caption length"):
        _parse_xhs(json.dumps(payload))


def test_parse_xhs_tags_too_few() -> None:
    from pipeline.utils.errors import CreateError
    payload = _xhs_payload()
    payload["tags"] = ["a", "b"]
    with pytest.raises(CreateError, match="tags count"):
        _parse_xhs(json.dumps(payload))


# ── _parse_x ───────────────────────────────────────────────

def _x_payload() -> dict:
    return {
        "tweets": [
            "Hook tweet with numbers 123 and a question?",
            "Second point explained.",
            "Third point explained.",
            "Fourth point explained.",
            "Follow for more! 🚀",
        ]
    }


def test_parse_x_valid() -> None:
    out = _parse_x(json.dumps(_x_payload()))
    assert isinstance(out, XOutput)
    assert len(out.tweets) == 5


def test_parse_x_too_few_tweets() -> None:
    from pipeline.utils.errors import CreateError
    payload = _x_payload()
    payload["tweets"] = payload["tweets"][:3]
    with pytest.raises(CreateError, match="tweets count"):
        _parse_x(json.dumps(payload))


def test_parse_x_too_many_tweets() -> None:
    from pipeline.utils.errors import CreateError
    payload = _x_payload()
    payload["tweets"] = payload["tweets"] * 3  # 15
    with pytest.raises(CreateError, match="tweets count"):
        _parse_x(json.dumps(payload))


def test_parse_x_tweet_too_long() -> None:
    from pipeline.utils.errors import CreateError
    payload = _x_payload()
    payload["tweets"][0] = "x" * 281  # 280 是 X 标准单推上限
    with pytest.raises(CreateError, match="too long"):
        _parse_x(json.dumps(payload))


# ── 三个派生函数调 LLM ───────────────────────────────────

def test_derive_toutiao_returns_output(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_der_t001")
    set_provider(ScriptedProvider([json.dumps(_toutiao_payload())]))

    out = derive_toutiao(
        title="x", canonical_md="y",
        conn=conn, ref_id="c_der_t001",
    )
    assert out.titles[0] == "标题A"


def test_derive_xiaohongshu_returns_output(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_der_x001")
    set_provider(ScriptedProvider([json.dumps(_xhs_payload())]))

    out = derive_xiaohongshu(
        title="x", canonical_md="y",
        conn=conn, ref_id="c_der_x001",
    )
    assert len(out.slides) == 5


def test_derive_x_returns_output(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_der_x002")
    set_provider(ScriptedProvider([json.dumps(_x_payload())]))

    out = derive_x(
        title="x", canonical_md="y",
        conn=conn, ref_id="c_der_x002",
    )
    assert len(out.tweets) == 5


# ── derive_one 完整链路 ────────────────────────────────────

def test_derive_one_writes_all_files(tmp_path) -> None:
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der001")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    result = derive_one(
        content,
        output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00",
        conn=conn,
    )

    # 各平台输出非空
    assert result.toutiao is not None
    assert result.xiaohongshu is not None
    assert result.x is not None
    assert result.failed_platforms == ()

    # 文件落盘
    out_dir = Path(content.canonical_path).parent
    assert (out_dir / "toutiao.md").exists()
    assert (out_dir / "xiaohongshu" / "slides.json").exists()
    assert (out_dir / "xiaohongshu" / "caption.md").exists()
    assert (out_dir / "xiaohongshu" / "tags.txt").exists()
    assert (out_dir / "x" / "thread.md").exists()


def test_derive_one_x_thread_format(tmp_path) -> None:
    """thread.md 格式：每条编号 (i/N) + 内容。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der002")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    result = derive_one(
        content, output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00", conn=conn,
    )
    assert result.x is not None

    thread_md = (Path(content.canonical_path).parent / "x" / "thread.md").read_text(
        encoding="utf-8"
    )
    assert "1/5" in thread_md
    assert "5/5" in thread_md


def test_derive_one_xhs_files_format(tmp_path) -> None:
    """小红书三件文件格式校验。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der003")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    result = derive_one(
        content, output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00", conn=conn,
    )
    assert result.xiaohongshu is not None

    xhs_dir = Path(content.canonical_path).parent / "xiaohongshu"
    slides = json.loads((xhs_dir / "slides.json").read_text(encoding="utf-8"))
    assert isinstance(slides, list)
    assert slides[0]["type"] == "cover"
    assert slides[-1]["type"] == "action"

    tags = (xhs_dir / "tags.txt").read_text(encoding="utf-8").splitlines()
    assert len(tags) == 5


def test_derive_one_toutiao_includes_three_titles(tmp_path) -> None:
    """toutiao.md 含 3 候选标题 + 正文。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der004")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    derive_one(
        content, output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00", conn=conn,
    )
    toutiao_md = (Path(content.canonical_path).parent / "toutiao.md").read_text(
        encoding="utf-8"
    )
    assert "1. 标题A" in toutiao_md
    assert "2. 标题B" in toutiao_md
    assert "3. 标题C" in toutiao_md
    assert "正文..." in toutiao_md


def test_derive_one_isolates_platform_failures(tmp_path) -> None:
    """某平台 LLM 失败 → 标 failed_platforms，其他平台仍成功。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der005")

    # 第 2 个响应（xiaohongshu）给坏 JSON 触发两次解析失败
    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        "garbage 1",  # xhs 第一次坏
        "garbage 2",  # xhs 第二次也坏 → CreateError
        json.dumps(_x_payload()),
    ]))

    result = derive_one(
        content, output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00", conn=conn,
    )
    assert result.toutiao is not None
    assert result.xiaohongshu is None
    assert result.x is not None
    assert "xiaohongshu" in result.failed_platforms

    # 失败平台的文件不应被创建
    out_dir = Path(content.canonical_path).parent
    assert (out_dir / "toutiao.md").exists()
    assert not (out_dir / "xiaohongshu").exists()
    assert (out_dir / "x" / "thread.md").exists()


def test_derive_one_logs_platform_failure(tmp_path, caplog) -> None:
    """某平台 CreateError → 记录 WARNING 日志（TECH_SPEC §8：异常分支不可静默吞没）。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der005b")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        "garbage 1",  # xhs 第一次坏
        "garbage 2",  # xhs 第二次也坏 → CreateError
        json.dumps(_x_payload()),
    ]))

    # get_logger() 默认 propagate=False，caplog 挂在 root logger 上抓不到，
    # 测试内显式打开传播（仿照 tests/test_metrics_logging_r7_4.py 的做法）。
    deriv._LOGGER.propagate = True
    with caplog.at_level(logging.WARNING, logger=deriv._LOGGER.name):
        derive_one(
            content, output_dir=Path(content.canonical_path).parent,
            now="2026-07-05T05:00:00+00:00", conn=conn,
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("xiaohongshu" in r.getMessage() for r in warnings)


def test_derive_one_idempotent(tmp_path) -> None:
    """重跑覆盖写（幂等）。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der006")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
        # 第二轮
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    out_dir = Path(content.canonical_path).parent

    derive_one(content, output_dir=out_dir, now="2026-07-05T05:00:00+00:00", conn=conn)
    toutiao_md_v1 = (out_dir / "toutiao.md").read_text(encoding="utf-8")

    derive_one(content, output_dir=out_dir, now="2026-07-05T05:00:00+00:00", conn=conn)
    toutiao_md_v2 = (out_dir / "toutiao.md").read_text(encoding="utf-8")

    assert toutiao_md_v1 == toutiao_md_v2


# ── run_derivative 批量 + formats 字段更新 ─────────────────

def test_run_derivative_updates_contents_formats(tmp_path) -> None:
    """派生成功的平台 → 写入 contents.formats JSON。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_run001")

    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))

    results = run_derivative(
        conn, output_root=tmp_path / "output",
        now="2026-07-05T05:00:00+00:00",
    )
    assert len(results) == 1
    assert not results[0].failed_platforms

    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", ("c_run001",)
    ).fetchone()
    formats = json.loads(row["formats"])
    assert set(formats) == {"toutiao", "xiaohongshu", "x"}


def test_run_derivative_partial_formats(tmp_path) -> None:
    """部分平台失败 → formats 只列成功的。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_run002")

    # xhs 失败
    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        "garbage 1", "garbage 2",
        json.dumps(_x_payload()),
    ]))

    run_derivative(
        conn, output_root=tmp_path / "output",
        now="2026-07-05T05:00:00+00:00",
    )

    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", ("c_run002",)
    ).fetchone()
    formats = json.loads(row["formats"])
    assert set(formats) == {"toutiao", "x"}


def test_run_derivative_formats_merges_on_rerun(tmp_path) -> None:
    """审计 Bug 3：重跑时 formats 字段应累积而非覆盖。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_run003")

    # 第一轮：toutiao + xiaohongshu 成功
    set_provider(ScriptedProvider([
        json.dumps(_toutiao_payload()),
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))
    run_derivative(conn, output_root=tmp_path / "output",
                   now="2026-07-05T05:00:00+00:00")

    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", ("c_run003",)
    ).fetchone()
    formats_v1 = set(json.loads(row["formats"]))
    assert formats_v1 == {"toutiao", "xiaohongshu", "x"}

    # 第二轮：toutiao 失败，xiaohongshu 成功（合并不应丢 toutiao）
    set_provider(ScriptedProvider([
        "garbage 1", "garbage 2",  # toutiao retry exhausted
        json.dumps(_xhs_payload()),
        json.dumps(_x_payload()),
    ]))
    run_derivative(conn, output_root=tmp_path / "output",
                   now="2026-07-05T06:00:00+00:00")

    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", ("c_run003",)
    ).fetchone()
    formats_v2 = set(json.loads(row["formats"]))
    # 关键：toutiao 仍在 formats（合并语义）
    assert "toutiao" in formats_v2
    assert formats_v2 == {"toutiao", "xiaohongshu", "x"}


def test_run_derivative_no_gated_returns_empty(tmp_path) -> None:
    """无 gated 内容 → 返回空 tuple。"""
    conn = _open_db(tmp_path)
    set_provider(ScriptedProvider([]))
    results = run_derivative(
        conn, output_root=tmp_path / "output",
        now="2026-07-05T05:00:00+00:00",
    )
    assert results == ()


def test_derive_one_budget_exceeded_propagates(tmp_path) -> None:
    """审计 Bug 2：BudgetExceeded 必须上抛，不被单平台吃掉。"""
    from pipeline.utils.errors import BudgetExceeded
    from pipeline.creators import llm as llm_mod

    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_budget001")

    class BudgetProvider(LLMProvider):
        def call(self, prompt, model, max_tokens):
            raise BudgetExceeded(
                stage="derive_toutiao", used_usd=100.0, limit_usd=5.0
            )

    set_provider(BudgetProvider())

    # BudgetExceeded 必须上抛到 derive_one → run_derivative → CLI 终止
    with pytest.raises(BudgetExceeded):
        derive_one(
            content,
            output_dir=Path(content.canonical_path).parent,
            now="2026-07-05T05:00:00+00:00",
            conn=conn,
        )


def test_derive_one_missing_canonical_marks_all_platforms_failed(tmp_path) -> None:
    """审计 Bug 4：canonical.md 缺失 → 所有平台标 failed，不抛。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_io001")

    # 删除 canonical.md
    Path(content.canonical_path).unlink()

    set_provider(ScriptedProvider([json.dumps(_toutiao_payload())]))

    result = derive_one(
        content,
        output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00",
        conn=conn,
    )
    # 所有平台都标 failed
    assert set(result.failed_platforms) == {"toutiao", "xiaohongshu", "x"}
    assert result.toutiao is None
    assert result.xiaohongshu is None
    assert result.x is None