"""creators/source_fetcher + creators/canonical 单元测试（M2-1）。

source_fetcher:
  - HTTP 200 HTML → 简单 strip 后纯文本
  - HTTP 非 200 / 网络错 → 返回 None + error（不抛）
  - URL 为空 → 返回 None（不尝试）

canonical.create_one:
  - stage1 LLM 返回 outline JSON；stage2 LLM 返回 essay 文本
  - 输出写到 output/<date>/<content_id>.tmp/canonical.md + meta.json
  - .tmp → rename 后返回非 .tmp 路径
  - contents 表 status=draft；topic status=selected → consumed
  - 二次运行（已存在的 .tmp）→ 先删 .tmp 再来（HARD_PARTS §5）
  - URL 抓取失败 → 仍能继续（仅用 title + summary 作为素材）
  - LLM 异常 → CreateError，topic 不动
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.config import Pillar
from pipeline.creators import llm as llm_mod
from pipeline.creators import source_fetcher
from pipeline.creators.canonical import create_one
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.models import Content, ContentStatus, Topic, TopicStatus
from pipeline.sources.dedup import content_hash


# ── helpers ─────────────────────────────────────────

class ScriptedProvider(LLMProvider):
    def __init__(
        self, responses: list[str], *, fail_remaining: bool = False,
    ):
        self._responses = list(responses)
        self._fail_remaining = fail_remaining
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if self._fail_remaining:
            raise llm_mod.RetryableError("429 mocked")
        if not self._responses:
            raise llm_mod.RetryableError("no scripted")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=500, output_tokens=3000,
        )


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _seed_topic(conn, *, id: str, title: str, url: str | None = None,
                summary: str | None = None) -> Topic:
    topic = Topic(
        id=id, source="rss:test", title=title, url=url, summary=summary,
        content_hash=content_hash(title, url),
        pillar="ai", score=8.0, score_reason="ok",
        status=TopicStatus.SELECTED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)
    return topic


@pytest.fixture(autouse=True)
def reset_provider():
    set_provider(ScriptedProvider([]))
    yield
    set_provider(ScriptedProvider([]))


def _pillars():
    return [
        Pillar(id="ai", name="AI", description="AI news", scoring_hint="x"),
    ]


# ── source_fetcher ─────────────────────────────────

def test_fetch_html_strips_to_text() -> None:
    """HTML 200 → 提取纯文本（去 tag 去 script/style）。"""
    html = (
        "<html><head><style>body{}</style></head>"
        "<body><h1>Title</h1>"
        "<script>alert(1)</script>"
        "<p>Paragraph one.</p>"
        "<p>Paragraph two.</p>"
        "</body></html>"
    )
    with patch.object(source_fetcher.httpx, "get") as mock_get:
        mock_resp = mock_get.return_value
        mock_resp.text = html
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status.return_value = None

        result = source_fetcher.fetch_text("https://example.com/x")

    assert "Title" in result
    assert "Paragraph one." in result
    assert "Paragraph two." in result
    assert "<" not in result
    assert "alert(1)" not in result


def test_fetch_returns_none_on_network_error() -> None:
    """网络错误 → 返回 None，不抛。"""
    with patch.object(
        source_fetcher.httpx, "get",
        side_effect=source_fetcher.httpx.RequestError("dns fail"),
    ):
        result = source_fetcher.fetch_text("https://nonexistent.example/x")

    assert result is None


def test_fetch_returns_none_on_http_error() -> None:
    """HTTP 4xx/5xx → 返回 None。"""
    with patch.object(source_fetcher.httpx, "get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = (
            source_fetcher.httpx.HTTPStatusError(
                "404", request=None, response=None
            )
        )
        result = source_fetcher.fetch_text("https://example.com/404")

    assert result is None


def test_fetch_returns_none_on_empty_url() -> None:
    """url 为空 → 不尝试网络，返回 None。"""
    assert source_fetcher.fetch_text("") is None
    assert source_fetcher.fetch_text(None) is None


# ── canonical.create_one ──────────────────────────

def test_create_one_writes_files_and_transitions(tmp_path) -> None:
    """完整成功路径：写文件 + contents + topic consumed。"""
    conn = _open_db(tmp_path)
    topic = _seed_topic(
        conn, id="t_aaaa1111",
        title="Test topic",
        url="https://example.com/x",
        summary="test summary",
    )

    set_provider(ScriptedProvider([
        # stage 1 outline
        json.dumps({
            "viewpoint": "AI 工具化是大势所趋",
            "outline": ["背景", "现状", "判断", "行动建议"],
        }),
        # stage 2 essay
        "# Title\n\n## 背景\n正文...\n",
    ]))

    output_root = tmp_path / "output"
    content = create_one(
        conn, topic, pillars=_pillars(),
        output_root=output_root,
        now="2026-07-05T02:00:00+00:00",
    )

    # 内容目录
    final_dir = output_root / "2026-07-05" / content.id
    assert final_dir.exists()
    assert not (final_dir.parent / f"{content.id}.tmp").exists()
    assert (final_dir / "canonical.md").exists()
    assert (final_dir / "meta.json").exists()

    # canonical.md 含正文
    md = (final_dir / "canonical.md").read_text(encoding="utf-8")
    assert "# Title" in md

    # meta.json 含必要字段
    meta = json.loads(
        (final_dir / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["topic_id"] == "t_aaaa1111"
    assert meta["title"] == "Test topic"
    assert meta["viewpoint"] == "AI 工具化是大势所趋"
    assert isinstance(meta["outline"], list)

    # contents 表
    assert content.id.startswith("c_")
    assert content.topic_id == "t_aaaa1111"
    assert content.title == "Test topic"
    assert content.pillar == "ai"
    assert content.status == ContentStatus.DRAFT.value
    assert content.canonical_path == str(
        final_dir / "canonical.md"
    )

    # topic 状态
    row = conn.execute(
        "SELECT status FROM topics WHERE id=?", ("t_aaaa1111",)
    ).fetchone()
    assert row["status"] == TopicStatus.CONSUMED.value


def test_create_one_uses_creative_tier(tmp_path) -> None:
    """创作走 creative 档（Sonnet）。"""
    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa2222", title="x")
    prov = ScriptedProvider([
        json.dumps({"viewpoint": "v", "outline": []}),
        "essay text",
    ])
    set_provider(prov)

    create_one(
        conn, topic, pillars=_pillars(),
        output_root=tmp_path / "output",
        now="2026-07-05T02:00:00+00:00",
    )

    models = [c["model"] for c in prov.calls]
    assert models == ["claude-sonnet-5", "claude-sonnet-5"]


def test_create_one_handles_url_fetch_failure(tmp_path) -> None:
    """URL 抓取失败时仍能用 title+summary 继续（M2-1 step 2）。"""
    conn = _open_db(tmp_path)
    topic = _seed_topic(
        conn, id="t_aaaa3333", title="x", url="https://fail.example/",
        summary="still have summary",
    )

    set_provider(ScriptedProvider([
        json.dumps({"viewpoint": "v", "outline": ["a", "b"]}),
        "essay",
    ]))

    with patch.object(source_fetcher, "fetch_text", return_value=None):
        content = create_one(
            conn, topic, pillars=_pillars(),
            output_root=tmp_path / "output",
            now="2026-07-05T02:00:00+00:00",
        )

    assert content.status == ContentStatus.DRAFT.value


def test_create_one_clears_stale_tmp_dir(tmp_path, monkeypatch) -> None:
    """重跑前发现 .tmp 残留 → 先 rmtree 再写（HARD_PARTS §5 幂等）。

    通过 monkeypatch new_id 让 content_id 可预测，从而预置 .tmp 残留。
    """
    from pipeline.creators import canonical as can_mod

    # 固定 content_id 以便预置 .tmp 残留
    monkeypatch.setattr(can_mod, "new_id", lambda p: "c_fixed0001")

    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa4444", title="x")

    set_provider(ScriptedProvider([
        json.dumps({"viewpoint": "v", "outline": ["a"]}),
        "essay",
    ]))

    output_root = tmp_path / "output"
    date_dir = output_root / "2026-07-05"

    # 预置 .tmp 残留 + 一个脏文件
    stale = date_dir / "c_fixed0001.tmp"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "stale.txt").write_text("stale data", encoding="utf-8")
    assert stale.exists()

    content = create_one(
        conn, topic, pillars=_pillars(),
        output_root=output_root,
        now="2026-07-05T02:00:00+00:00",
    )

    # 内容目录是 c_fixed0001（无 .tmp 后缀）；残留 .tmp 应已删除
    assert content.id == "c_fixed0001"
    final_dir = date_dir / "c_fixed0001"
    assert final_dir.exists()
    assert not stale.exists()  # 残留被清
    assert (final_dir / "canonical.md").exists()  # 新内容写入


def test_create_one_lmm_exception_raises_create_error(tmp_path) -> None:
    """LLM RetryableError 重试用尽 → CreateError 上抛，topic 状态不动。"""
    from pipeline.utils.errors import CreateError

    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa5555", title="x")
    set_provider(ScriptedProvider([], fail_remaining=True))

    with pytest.raises(CreateError):
        create_one(
            conn, topic, pillars=_pillars(),
            output_root=tmp_path / "output",
            now="2026-07-05T02:00:00+00:00",
        )

    row = conn.execute(
        "SELECT status FROM topics WHERE id=?", ("t_aaaa5555",)
    ).fetchone()
    assert row["status"] == TopicStatus.SELECTED.value  # 未被消费


def test_create_one_propagates_budget_exceeded(tmp_path) -> None:
    """BudgetExceeded 是系统级错误 → 必须原样上抛，不被当 CreateError。

    修审计发现的 bug：原 `except Exception` 会把 BudgetExceeded 当单条失败，
    导致编排层继续运行其他 topic 但实际已无预算。正确：BudgetExceeded 上抛。
    """
    from pipeline.utils.errors import BudgetExceeded

    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa7777", title="x")

    class BudgetProvider(LLMProvider):
        def call(self, prompt, model, max_tokens):
            raise BudgetExceeded(stage="create", used_usd=100.0, limit_usd=5.0)

    set_provider(BudgetProvider())

    # BudgetExceeded 应原样抛出（不被捕获重包成 CreateError）
    with pytest.raises(BudgetExceeded):
        create_one(
            conn, topic, pillars=_pillars(),
            output_root=tmp_path / "output",
            now="2026-07-05T02:00:00+00:00",
        )


def test_create_one_outline_parse_fail_raises(tmp_path) -> None:
    """stage1 JSON 解析失败 → CreateError。"""
    from pipeline.utils.errors import CreateError

    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa6666", title="x")
    set_provider(ScriptedProvider(["not json", "essay"]))

    with pytest.raises(CreateError):
        create_one(
            conn, topic, pillars=_pillars(),
            output_root=tmp_path / "output",
            now="2026-07-05T02:00:00+00:00",
        )


def test_create_one_handles_json_code_fence(tmp_path) -> None:
    """stage1 LLM 把 JSON 包在 ```json ... ``` 围栏里 → 仍能解析（实战）。"""
    from pipeline.creators.canonical import _strip_code_fence

    fenced = '```json\n{"viewpoint": "v", "outline": ["a", "b"]}\n```'
    assert _strip_code_fence(fenced) == '{"viewpoint": "v", "outline": ["a", "b"]}'

    conn = _open_db(tmp_path)
    topic = _seed_topic(conn, id="t_aaaa9999", title="x")
    set_provider(ScriptedProvider([
        '```json\n{"viewpoint": "v", "outline": ["a", "b"]}\n```',
        "essay",
    ]))
    content = create_one(
        conn, topic, pillars=_pillars(),
        output_root=tmp_path / "output",
        now="2026-07-05T02:00:00+00:00",
    )
    assert content.status == ContentStatus.DRAFT.value