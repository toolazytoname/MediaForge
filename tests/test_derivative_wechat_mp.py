"""M13 公众号图文派生测试（结构镜像 tests/test_derivative.py）。

覆盖：
  - _parse_wechat_mp 字段校验（合法 / 缺字段 / 超长 / 含 [IMAGE: 拒绝）
  - derive_wechat_mp 调 LLM + parse（含围栏剥离、坏 JSON 重试耗尽）
  - write_wechat_mp 原子写（article.md + meta.json）
  - derive_one(platforms=(...,"wechat_mp")) 显式传入才出现（钉死默认元组不含 wechat_mp 的决定）
  - run_derivative 的 formats 字段累积包含 wechat_mp
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import db
from pipeline.creators.derivative import derive_one, run_derivative
from pipeline.creators.derivative_wechat_mp import (
    WechatMpOutput,
    _parse_wechat_mp,
    derive_wechat_mp,
    write_wechat_mp,
)
from pipeline.creators import llm as llm_mod
from pipeline.creators.llm import CompletionResult, LLMProvider, set_provider
from pipeline.models import Content, ContentStatus, Topic, TopicStatus
from pipeline.sources.dedup import content_hash
from pipeline.utils.errors import CreateError


# ── helpers（与 test_derivative.py 一致）───────────────────

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


def _wechat_mp_payload() -> dict:
    return {
        "title": "AI 工具正在重塑内容创作",
        "digest": "一篇讲 AI 工具如何影响内容创作流程的长文摘要。",
        "body_md": "# AI 工具正在重塑内容创作\n\n## 第一部分\n\n正文……\n\n## 第二部分\n\n正文……",
    }


# ── _parse_wechat_mp ─────────────────────────────────────────

def test_parse_wechat_mp_valid() -> None:
    out = _parse_wechat_mp(json.dumps(_wechat_mp_payload()))
    assert isinstance(out, WechatMpOutput)
    assert out.title == "AI 工具正在重塑内容创作"
    assert "## 第一部分" in out.body_md


def test_parse_wechat_mp_invalid_json_raises() -> None:
    with pytest.raises(CreateError):
        _parse_wechat_mp("not json")


def test_parse_wechat_mp_handles_fenced_response() -> None:
    payload = _wechat_mp_payload()
    text = "```json\n" + json.dumps(payload) + "\n```"
    out = _parse_wechat_mp(text)
    assert out.title == payload["title"]


def test_parse_wechat_mp_missing_title() -> None:
    payload = _wechat_mp_payload()
    payload["title"] = ""
    with pytest.raises(CreateError, match="title missing"):
        _parse_wechat_mp(json.dumps(payload))


def test_parse_wechat_mp_title_too_long() -> None:
    payload = _wechat_mp_payload()
    payload["title"] = "x" * 65  # 64 是微信草稿标题上限
    with pytest.raises(CreateError, match="title too long"):
        _parse_wechat_mp(json.dumps(payload))


def test_parse_wechat_mp_missing_digest() -> None:
    payload = _wechat_mp_payload()
    payload["digest"] = ""
    with pytest.raises(CreateError, match="digest missing"):
        _parse_wechat_mp(json.dumps(payload))


def test_parse_wechat_mp_digest_too_long() -> None:
    payload = _wechat_mp_payload()
    payload["digest"] = "x" * 121  # 120 是 digest 上限
    with pytest.raises(CreateError, match="digest too long"):
        _parse_wechat_mp(json.dumps(payload))


def test_parse_wechat_mp_missing_body() -> None:
    payload = _wechat_mp_payload()
    payload["body_md"] = ""
    with pytest.raises(CreateError, match="body_md missing"):
        _parse_wechat_mp(json.dumps(payload))


def test_parse_wechat_mp_rejects_image_placeholder() -> None:
    """v1 无正文插图承接机制，出现 [IMAGE: 直接拒绝而非静默产出脏内容。"""
    payload = _wechat_mp_payload()
    payload["body_md"] = "正文……\n\n[IMAGE: 一张配图]\n\n后续正文……"
    with pytest.raises(CreateError, match="image placeholder"):
        _parse_wechat_mp(json.dumps(payload))


# ── derive_wechat_mp 调 LLM ──────────────────────────────────

def test_derive_wechat_mp_returns_output(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_der_wm001")
    set_provider(ScriptedProvider([json.dumps(_wechat_mp_payload())]))

    out = derive_wechat_mp(
        title="x", canonical_md="y",
        conn=conn, ref_id="c_der_wm001",
    )
    assert out.title == "AI 工具正在重塑内容创作"


def test_derive_wechat_mp_retry_exhausted_raises_create_error(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_der_wm002")
    set_provider(ScriptedProvider(["garbage 1", "garbage 2"]))

    with pytest.raises(CreateError):
        derive_wechat_mp(
            title="x", canonical_md="y",
            conn=conn, ref_id="c_der_wm002",
        )


# ── write_wechat_mp 原子写 ───────────────────────────────────

def test_write_wechat_mp_writes_article_and_meta(tmp_path) -> None:
    out_dir = tmp_path / "content"
    output = WechatMpOutput(**_wechat_mp_payload())
    write_wechat_mp(out_dir, output)

    article = (out_dir / "wechat_mp" / "article.md").read_text(encoding="utf-8")
    assert article == output.body_md

    meta = json.loads((out_dir / "wechat_mp" / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"title": output.title, "digest": output.digest}


def test_write_wechat_mp_idempotent_overwrite(tmp_path) -> None:
    out_dir = tmp_path / "content"
    output_v1 = WechatMpOutput(title="T1", digest="D1", body_md="body v1")
    write_wechat_mp(out_dir, output_v1)

    output_v2 = WechatMpOutput(title="T2", digest="D2", body_md="body v2")
    write_wechat_mp(out_dir, output_v2)

    article = (out_dir / "wechat_mp" / "article.md").read_text(encoding="utf-8")
    assert article == "body v2"
    meta = json.loads((out_dir / "wechat_mp" / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"title": "T2", "digest": "D2"}


# ── derive_one：默认元组不含 wechat_mp（回归测试）───────────

def test_derive_one_default_platforms_excludes_wechat_mp(tmp_path) -> None:
    """钉死设计决定：不显式传 platforms 时，wechat_mp 不参与派生（成本护栏）。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der_wm003")

    set_provider(ScriptedProvider([
        json.dumps({"titles": ["a", "b", "c"], "body": "正文"}),
        json.dumps({
            "slides": [
                {"type": "cover", "title": "封面", "body": "x" * 50},
                {"type": "content", "title": "1", "body": "x" * 60},
                {"type": "content", "title": "2", "body": "x" * 70},
                {"type": "content", "title": "3", "body": "x" * 80},
                {"type": "action", "title": "关注", "body": "x" * 30},
            ],
            "caption": "x" * 100,
            "tags": ["AI", "工具", "效率", "深度", "评测"],
        }),
        json.dumps({"tweets": [
            "tweet one is here", "tweet two is here", "tweet three is here",
            "tweet four is here", "tweet five is here",
        ]}),
    ]))

    result = derive_one(
        content,
        output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00",
        conn=conn,
    )
    assert result.wechat_mp is None
    assert "wechat_mp" not in result.failed_platforms
    out_dir = Path(content.canonical_path).parent
    assert not (out_dir / "wechat_mp").exists()


def test_derive_one_explicit_wechat_mp_platform(tmp_path) -> None:
    """显式传入 platforms=(...,"wechat_mp") 才会派生。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der_wm004")

    set_provider(ScriptedProvider([json.dumps(_wechat_mp_payload())]))

    result = derive_one(
        content,
        output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00",
        conn=conn,
        platforms=("wechat_mp",),
    )
    assert result.wechat_mp is not None
    assert result.wechat_mp.title == "AI 工具正在重塑内容创作"
    assert result.failed_platforms == ()

    out_dir = Path(content.canonical_path).parent
    assert (out_dir / "wechat_mp" / "article.md").exists()
    assert (out_dir / "wechat_mp" / "meta.json").exists()


def test_derive_one_wechat_mp_failure_isolated(tmp_path) -> None:
    """wechat_mp 失败不影响其他平台，且被列入 failed_platforms。"""
    conn = _open_db(tmp_path)
    content = _seed_gated_content(tmp_path, conn, content_id="c_der_wm005")

    set_provider(ScriptedProvider([
        json.dumps({"titles": ["a", "b", "c"], "body": "正文"}),  # toutiao ok
        "garbage 1", "garbage 2",  # wechat_mp 两次坏 JSON → CreateError
    ]))

    result = derive_one(
        content,
        output_dir=Path(content.canonical_path).parent,
        now="2026-07-05T05:00:00+00:00",
        conn=conn,
        platforms=("toutiao", "wechat_mp"),
    )
    assert result.toutiao is not None
    assert result.wechat_mp is None
    assert "wechat_mp" in result.failed_platforms

    out_dir = Path(content.canonical_path).parent
    assert (out_dir / "toutiao.md").exists()
    assert not (out_dir / "wechat_mp").exists()


# ── run_derivative：formats 累积包含 wechat_mp ──────────────

def test_run_derivative_wechat_mp_updates_formats(tmp_path) -> None:
    conn = _open_db(tmp_path)
    _seed_gated_content(tmp_path, conn, content_id="c_run_wm001")

    set_provider(ScriptedProvider([json.dumps(_wechat_mp_payload())]))

    results = run_derivative(
        conn, output_root=tmp_path / "output",
        now="2026-07-05T05:00:00+00:00",
        platforms=("wechat_mp",),
    )
    assert len(results) == 1
    assert results[0].wechat_mp is not None

    row = conn.execute(
        "SELECT formats FROM contents WHERE id=?", ("c_run_wm001",)
    ).fetchone()
    formats = json.loads(row["formats"])
    assert "wechat_mp" in formats
