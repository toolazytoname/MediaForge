"""M2-2 质量门禁测试。

覆盖：
  - anchors_loader：6 篇加载、render_for_prompt 格式化
  - decision：has_blocker、safe_to_revise、should_accept_revision 4 层防御、
    decide_gate 阈值判定、decide_revision_action 决策树
  - critic：JSON 解析（合法/非法）；问题分类
  - scorer：JSON 解析、分数范围校验、强制 problems+verdict
  - canonical.rewrite_one：读旧文件 → 重写 → .tmp→rename
  - runner：完整链路（mock LLM）→ gated / discarded / rewrite 多路径
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.config import GateConfig
from pipeline.creators import llm as llm_mod
from pipeline.creators.canonical import rewrite_one
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.gate.anchors_loader import (
    AnchorsBundle,
    Anchor,
    load_anchors,
    render_for_prompt,
)
from pipeline.gate.critic import (
    CritiqueResult,
    critique_one,
    render_critique_text,
    _parse_critique,
    _render_critic_prompt,
)
from pipeline.gate.decision import (
    Action,
    GateDecision,
    Problem,
    REVISABLE_CATEGORIES,
    UNFIXABLE_CATEGORIES,
    decide_gate,
    decide_revision_action,
    has_blocker,
    safe_to_revise,
    should_accept_revision,
)
from pipeline.gate.runner import run_gate
from pipeline.gate.scorer import (
    ScoreResult,
    _parse_score,
    score_one,
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
    """按调用顺序返回不同 response 的脚本 provider。"""

    def __init__(self, responses: list[str], *, fail_remaining: bool = False):
        self._responses = list(responses)
        self._fail_remaining = fail_remaining
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if self._fail_remaining:
            raise llm_mod.RetryableError("mocked retryable")
        if not self._responses:
            raise llm_mod.RetryableError("no scripted response")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=500, output_tokens=800,
        )


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "state.db"
    c = db.connect(p)
    db.init_db(c)
    return c


def _seed_draft_content_unused() -> None:
    """占位防 unused 警告——实际跑测时用 _seed_full_content。"""
    pass


@pytest.fixture(autouse=True)
def reset_provider(tmp_path):
    set_provider(ScriptedProvider([]))
    # 给 llm.complete 注入 DB conn（避免 RuntimeError）
    conn = _open_db(tmp_path)
    llm_mod.init_db_conn(conn)
    yield
    llm_mod.init_db_conn(None)  # type: ignore[arg-type]
    conn.close()
    set_provider(ScriptedProvider([]))


# ── anchors_loader ────────────────────────────────────────

def test_load_anchors_returns_six() -> None:
    """默认目录应加载全部 6 篇锚点。"""
    bundle = load_anchors()
    names = [a.name for a in bundle.anchors]
    assert "good_01" in names
    assert "good_02" in names
    assert "mid_01" in names
    assert "bad_01" in names
    assert len(bundle.anchors) == 6


def test_load_anchors_by_tier() -> None:
    bundle = load_anchors()
    good = bundle.good()
    mid = bundle.mid()
    bad = bundle.bad()
    assert len(good) == 2 and all(a.tier == "good" for a in good)
    assert len(mid) == 2 and all(a.tier == "mid" for a in mid)
    assert len(bad) == 2 and all(a.tier == "bad" for a in bad)


def test_render_for_prompt_has_three_tiers() -> None:
    bundle = load_anchors()
    parts = render_for_prompt(bundle, excerpt_chars=200)
    assert "信息" in parts["good"]  # good 锚点评分表
    assert "总 " in parts["mid"]
    assert parts["bad"] != ""


def test_render_for_prompt_truncates_excerpts() -> None:
    bundle = load_anchors()
    parts = render_for_prompt(bundle, excerpt_chars=100)
    # 截断后应有 "..." 标记
    assert "..." in parts["good"]


# ── decision.has_blocker ──────────────────────────────────

def test_has_blocker_explicit_blocker() -> None:
    p = Problem(category="tone", severity="blocker", message="x")
    assert has_blocker((p,)) is True


def test_has_blocker_fact_high_implies_blocker() -> None:
    """fact 类 high 严重度默认升级为 blocker（HARD_PARTS §3 事实类不可改）。"""
    p = Problem(category="fact", severity="high", message="x")
    assert has_blocker((p,)) is True


def test_has_blocker_risk_high_implies_blocker() -> None:
    p = Problem(category="risk", severity="high", message="x")
    assert has_blocker((p,)) is True


def test_has_blocker_empty() -> None:
    assert has_blocker(()) is False


def test_has_blocker_only_low_medium() -> None:
    p1 = Problem(category="tone", severity="low", message="x")
    p2 = Problem(category="structure", severity="medium", message="y")
    assert has_blocker((p1, p2)) is False


# ── decision.safe_to_revise ───────────────────────────────

def test_safe_to_revise_blocker_blocks() -> None:
    p = Problem(category="fact", severity="blocker", message="x")
    assert safe_to_revise((p,)) is False


def test_safe_to_revise_tone_structure_title() -> None:
    """默认可重写类别 → safe."""
    p1 = Problem(category="tone", severity="medium", message="x")
    p2 = Problem(category="structure", severity="high", message="y")
    p3 = Problem(category="title", severity="low", message="z")
    assert safe_to_revise((p1, p2, p3)) is True


def test_safe_to_revise_autofixable_bypass() -> None:
    """autoFixable=true 是白名单旁路（TrendPublish isSafeRevisionCandidate）。"""
    p = Problem(category="fact", severity="low", message="x", auto_fixable=True)
    # 仍有 blocker 检查：low severity + autoFixable → OK
    assert safe_to_revise((p,)) is True


def test_safe_to_revise_empty() -> None:
    assert safe_to_revise(()) is True


# ── decision.should_accept_revision（4 层防御）────────────

def _pb(category: str, severity: str = "medium") -> Problem:
    return Problem(category=category, severity=severity, message="")


def test_revision_rule1_new_blocker_rejected() -> None:
    """新引入 blocker → 拒绝（即便分数升）。"""
    before = (Problem(category="tone", severity="low", message=""),)
    after = (Problem(category="fact", severity="high", message=""),)  # fact+high=blocker
    assert should_accept_revision(
        before_score=20, after_score=25,
        before_problems=before, after_problems=after,
        before_allow_publish=True, after_allow_publish=True,
    ) is False


def test_revision_rule2_publish_downgrade_rejected() -> None:
    """allow_publish 降级（true→false）→ 拒绝。"""
    before = (_pb("tone"),)
    after = (_pb("tone"),)
    assert should_accept_revision(
        before_score=24, after_score=18,  # 分数降
        before_problems=before, after_problems=after,
        before_allow_publish=True, after_allow_publish=False,
    ) is False


def test_revision_rule3_action_rank_up_adopted() -> None:
    """action 等级提升 → 哪怕分数降也采纳。"""
    # before: 不通过门禁（score=15, allow=False） → REVISE/DISCARD 档
    # after: 通过门禁（score=26, allow=True） → GATE 档
    before = (_pb("tone"),)
    after = (_pb("tone"),)
    assert should_accept_revision(
        before_score=15, after_score=26,
        before_problems=before, after_problems=after,
        before_allow_publish=False, after_allow_publish=True,
    ) is True


def test_revision_rule4_same_rank_requires_non_decreasing_score() -> None:
    """action 持平 → after.score >= before.score 才采纳。"""
    same = (_pb("tone"),)
    # 两者都 allow_publish=True（GATE）→ 持平 → 比分数
    assert should_accept_revision(
        before_score=24, after_score=24,
        before_problems=same, after_problems=same,
        before_allow_publish=True, after_allow_publish=True,
    ) is True
    assert should_accept_revision(
        before_score=24, after_score=23,
        before_problems=same, after_problems=same,
        before_allow_publish=True, after_allow_publish=True,
    ) is False


# ── decision.decide_gate（阈值判定）────────────────────────

def test_decide_gate_pass_high_score() -> None:
    d = decide_gate(
        info=9, fun=8, view=9,
        problems=(), threshold_total=24, threshold_each=6,
        verdict="good",
    )
    assert d.allow_publish is True
    assert d.action == Action.GATE
    assert d.score == 26


def test_decide_gate_fail_below_threshold_total() -> None:
    d = decide_gate(
        info=7, fun=6, view=6,
        problems=(), threshold_total=24, threshold_each=6,
    )
    assert d.allow_publish is False
    assert d.action in (Action.REVISE, Action.DISCARD)


def test_decide_gate_fail_below_threshold_each() -> None:
    """fun=5 低于 each=6 → 不通过。"""
    d = decide_gate(
        info=10, fun=5, view=10,
        problems=(), threshold_total=24, threshold_each=6,
    )
    assert d.allow_publish is False


def test_decide_gate_blocker_forces_block() -> None:
    """即便分数高，blocker 也强制 BLOCK。"""
    d = decide_gate(
        info=9, fun=9, view=9,
        problems=(Problem(category="fact", severity="high", message=""),),
        threshold_total=24, threshold_each=6,
    )
    assert d.allow_publish is False
    assert d.action == Action.BLOCK


# ── decision.decide_revision_action（决策树）──────────────

def test_decide_revision_action_blocker_returns_block() -> None:
    p = Problem(category="fact", severity="blocker", message="x")
    a = decide_revision_action(has_rewrite_issues=True, problems=(p,))
    assert a == Action.BLOCK


def test_decide_revision_action_no_problems_returns_gate() -> None:
    """无问题 → GATE（直接进 scorer 评分确认）。"""
    a = decide_revision_action(has_rewrite_issues=False, problems=())
    assert a == Action.GATE


def test_decide_revision_action_safe_no_rewrite_issues_returns_gate() -> None:
    """safe + 无需重写 → GATE（让 scorer 兜底）。"""
    p = Problem(category="tone", severity="low", message="x")
    a = decide_revision_action(has_rewrite_issues=False, problems=(p,))
    assert a == Action.GATE


def test_decide_revision_action_revise_triggers_rewrite() -> None:
    p = Problem(category="tone", severity="medium", message="x")
    a = decide_revision_action(has_rewrite_issues=True, problems=(p,))
    assert a == Action.REVISE


# ── critic ────────────────────────────────────────────────

def test_critic_parse_valid() -> None:
    text = json.dumps({
        "problems": [
            {"category": "tone", "severity": "medium", "message": "m",
             "evidence": "e", "suggestion": "s", "autoFixable": False}
        ],
        "summary": "ok",
    })
    problems, summary = _parse_critique(text)
    assert len(problems) == 1
    assert problems[0].category == "tone"
    assert summary == "ok"


def test_critic_parse_invalid_json_raises_gate_error() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_critique("not json")


def test_critic_parse_missing_fields_raises() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_critique(json.dumps({"summary": "no problems field"}))


def test_critic_parse_invalid_category_raises() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_critique(json.dumps({
            "problems": [{"category": "wrong", "severity": "low", "message": ""}],
            "summary": "",
        }))


def test_critic_one_calls_llm_and_returns() -> None:
    """critic 调 LLM 一次并返回 CritiqueResult。"""
    set_provider(ScriptedProvider([json.dumps({
        "problems": [
            {"category": "tone", "severity": "low", "message": "AI 套话"}
        ],
        "summary": "轻微 AI 味",
    })]))

    result = critique_one(
        title="x", canonical_md="# x\n\nbody",
        conn=None, ref_id="c_aaaa1111",
    )
    assert isinstance(result, CritiqueResult)
    assert len(result.problems) == 1
    assert result.summary == "轻微 AI 味"
    # tone 类 severity=low → safe_to_revise=True → needs_rewrite=True
    assert result.needs_rewrite is True


def test_critic_one_needs_rewrite_false_when_blocker() -> None:
    set_provider(ScriptedProvider([json.dumps({
        "problems": [
            {"category": "fact", "severity": "high", "message": "编造数字"}
        ],
        "summary": "事实错误",
    })]))
    result = critique_one(
        title="x", canonical_md="body", conn=None, ref_id="c_aaaa",
    )
    # fact+high = blocker → safe_to_revise=False → needs_rewrite=False
    assert result.needs_rewrite is False


def test_critic_render_text_format() -> None:
    r = CritiqueResult(
        problems=(Problem(category="tone", severity="medium", message="m",
                          evidence="e", suggestion="s", auto_fixable=False),),
        summary="summary",
        needs_rewrite=True,
        raw_response="raw",
    )
    text = render_critique_text(r)
    assert "Critic 审稿意见" in text
    assert "tone" in text
    assert "summary" in text


def test_critic_prompt_includes_source_text() -> None:
    """source_text 非空 → 原样嵌入 prompt，供 critic 核对事实。

    回归：真实数据冒烟发现 critic 无原文参照时，会把训练知识截止日期
    之后的真实新事实（如 2026 年的 CPython 3.14、真实项目文案）误判为
    编造，导致 100% discard。修复：critic 拿到与创作时同一份原文用于
    核对，而非凭自己的训练知识猜测"合不合理"。
    """
    prompt = _render_critic_prompt(
        title="x", canonical_md="body", source_text="真实原文片段 ABC123",
    )
    assert "真实原文片段 ABC123" in prompt


def test_critic_prompt_source_text_none_shows_placeholder() -> None:
    """source_text=None → 显示"（无）"占位，不是 Python None 字面量。"""
    prompt = _render_critic_prompt(title="x", canonical_md="body")
    assert "（无）" in prompt
    assert "None" not in prompt


# ── scorer ─────────────────────────────────────────────────

def test_scorer_parse_valid() -> None:
    text = json.dumps({
        "info": 8, "fun": 7, "view": 9,
        "problems": [
            {"category": "tone", "severity": "medium",
             "message": "m", "evidence": "e"}
        ],
        "verdict": "good",
    })
    r = _parse_score(text)
    assert r.info == 8 and r.fun == 7 and r.view == 9
    assert r.total == 24
    assert r.verdict == "good"


def test_scorer_parse_invalid_json() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_score("not json")


def test_scorer_parse_out_of_range_score_rejected() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_score(json.dumps({
            "info": 11, "fun": 7, "view": 9,
            "problems": [], "verdict": "",
        }))


def test_scorer_parse_missing_scores_rejected() -> None:
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_score(json.dumps({
            "info": 8, "fun": 7,  # view 缺
            "problems": [], "verdict": "",
        }))


def test_scorer_parse_bool_score_rejected() -> None:
    """bool 是 int 子类，必须显式排除（审计 Bug 1）。"""
    from pipeline.utils.errors import GateError
    with pytest.raises(GateError):
        _parse_score(json.dumps({
            "info": True, "fun": 7, "view": 9,  # bool 不可当 score
            "problems": [], "verdict": "",
        }))


def test_scorer_one_uses_critical_tier() -> None:
    """scorer 走 critical 档（与创作隔离，HARD_PARTS §3）。"""
    bundle = load_anchors()
    prov = ScriptedProvider([json.dumps({
        "info": 9, "fun": 8, "view": 9, "problems": [], "verdict": "ok",
    })])
    set_provider(prov)

    score_one(
        title="x", canonical_md="body",
        anchors=bundle, conn=None, ref_id="c_aaaa",
    )
    assert prov.calls[0]["model"] == "claude-sonnet-5"


# ── canonical.rewrite_one ─────────────────────────────────

def test_rewrite_one_replaces_canonical(tmp_path) -> None:
    """rewrite_one 读旧 → LLM → 写 .tmp → rename。"""
    conn = _open_db(tmp_path)
    topic_id = "t_rwtest001"
    content_id = "c_rwtest001"

    topic = Topic(
        id=topic_id, source="rss:test", title="t", url=None, summary=None,
        content_hash=content_hash("t", None),
        pillar="ai", score=8.0, score_reason="ok",
        status=TopicStatus.CONSUMED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)

    out_dir = tmp_path / "output" / "2026-07-05" / content_id
    out_dir.mkdir(parents=True)
    (out_dir / "canonical.md").write_text("# old\n\nold body", encoding="utf-8")

    content = Content(
        id=content_id, topic_id=topic_id, pillar="ai", title="t",
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=ContentStatus.DRAFT.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_content(conn, content)

    set_provider(ScriptedProvider(["# new\n\nrewritten body"]))
    new_content = rewrite_one(
        conn, content,
        critique_text="- tone: AI 套话",
        now="2026-07-05T03:00:00+00:00",
    )

    md = (out_dir / "canonical.md").read_text(encoding="utf-8")
    assert "rewritten body" in md
    assert "old body" not in md
    assert new_content.updated_at == "2026-07-05T03:00:00+00:00"
    assert new_content.status == ContentStatus.DRAFT.value  # status 不动


def test_rewrite_one_missing_canonical_raises(tmp_path) -> None:
    """canonical.md 不存在 → CreateError。"""
    from pipeline.utils.errors import CreateError

    conn = _open_db(tmp_path)
    content = Content(
        id="c_missing", topic_id="t_seed0001", pillar="ai", title="t",
        canonical_path=str(tmp_path / "no_such_dir" / "canonical.md"),
        formats=(), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=ContentStatus.DRAFT.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    with pytest.raises(CreateError):
        rewrite_one(conn, content, critique_text="x", now="2026-07-05T03:00:00+00:00")


# ── runner.run_gate ────────────────────────────────────────

def _seed_full_content(tmp_path, conn, *, content_id: str, md_text: str) -> Content:
    """种一条完整 draft content（含 canonical.md 文件）。"""
    topic_id = f"t_{content_id[2:]}"
    topic = Topic(
        id=topic_id, source="rss:test", title="Test", url=None, summary=None,
        content_hash=content_hash("Test", None),
        pillar="ai", score=8.0, score_reason="ok",
        status=TopicStatus.CONSUMED.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_topic(conn, topic)

    out_dir = tmp_path / "output" / "2026-07-05" / content_id
    out_dir.mkdir(parents=True)
    (out_dir / "canonical.md").write_text(md_text, encoding="utf-8")

    content = Content(
        id=content_id, topic_id=topic_id, pillar="ai", title="Test",
        canonical_path=str(out_dir / "canonical.md"),
        formats=(), gate_score_total=None, gate_scores=None,
        gate_verdict=None, status=ContentStatus.DRAFT.value,
        created_at="2026-07-05T01:00:00+00:00",
        updated_at="2026-07-05T01:00:00+00:00",
    )
    db.insert_content(conn, content)
    return content


def test_runner_passes_high_score_content_to_gated(tmp_path) -> None:
    """评分高 → gated，contents 表更新分数与状态。"""
    conn = _open_db(tmp_path)
    _seed_full_content(
        tmp_path, conn, content_id="c_aaaa1111",
        md_text="# t\n\n好内容...",
    )

    # critic: 无问题 → needs_rewrite=False
    # scorer: 9/9/9 = 27
    set_provider(ScriptedProvider([
        json.dumps({"problems": [], "summary": "无问题"}),
        json.dumps({
            "info": 9, "fun": 9, "view": 9,
            "problems": [], "verdict": "好",
        }),
    ]))

    result = run_gate(
        conn, gate_cfg=GateConfig(),
        anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
        now="2026-07-05T04:00:00+00:00",
    )
    assert result.gated_count == 1
    assert result.discarded_count == 0
    assert result.failed_count == 0

    # 状态已转
    row = conn.execute(
        "SELECT status, gate_score_total FROM contents WHERE id=?",
        ("c_aaaa1111",),
    ).fetchone()
    assert row["status"] == ContentStatus.GATED.value
    assert row["gate_score_total"] == 27.0

    # critique.md 已落盘
    critique_path = tmp_path / "output" / "2026-07-05" / "c_aaaa1111" / "critique.md"
    assert critique_path.exists()


def test_runner_discards_low_score_content(tmp_path) -> None:
    """评分低 → discarded，状态转移且 critique.md 保留。"""
    conn = _open_db(tmp_path)
    _seed_full_content(
        tmp_path, conn, content_id="c_aaaa2222",
        md_text="# t\n\n差内容...",
    )

    set_provider(ScriptedProvider([
        json.dumps({"problems": [], "summary": "无问题"}),
        json.dumps({
            "info": 3, "fun": 3, "view": 3,
            "problems": [], "verdict": "差",
        }),
    ]))

    result = run_gate(
        conn, gate_cfg=GateConfig(),
        anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
        now="2026-07-05T04:00:00+00:00",
    )
    assert result.discarded_count == 1
    row = conn.execute(
        "SELECT status FROM contents WHERE id=?", ("c_aaaa2222",)
    ).fetchone()
    assert row["status"] == ContentStatus.DISCARDED.value


def test_runner_triggers_rewrite_on_revise_problems(tmp_path) -> None:
    """critic 找到可重写问题 → 触发 rewrite → 再 critic → 再 scorer。"""
    conn = _open_db(tmp_path)
    _seed_full_content(
        tmp_path, conn, content_id="c_aaaa3333",
        md_text="# t\n\nold body",
    )

    set_provider(ScriptedProvider([
        # critic1: tone 问题 → needs_rewrite=True
        json.dumps({
            "problems": [{"category": "tone", "severity": "medium",
                          "message": "AI 套话", "evidence": "...",
                          "suggestion": "...", "autoFixable": False}],
            "summary": "需要重写",
        }),
        # rewrite: LLM 返回新正文
        "# t\n\nrewritten body",
        # critic2: 无问题
        json.dumps({"problems": [], "summary": "ok"}),
        # scorer: 9/9/9 = 27
        json.dumps({"info": 9, "fun": 9, "view": 9,
                    "problems": [], "verdict": "好"}),
    ]))

    result = run_gate(
        conn, gate_cfg=GateConfig(max_rewrites=1),
        anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
        now="2026-07-05T04:00:00+00:00",
    )
    assert result.gated_count == 1
    # canonical.md 已被重写
    final_md = (tmp_path / "output" / "2026-07-05" / "c_aaaa3333" / "canonical.md").read_text(
        encoding="utf-8"
    )
    assert "rewritten body" in final_md


def test_runner_marks_failed_on_llm_error(tmp_path) -> None:
    """LLM 持续报错 → content 转 DRAFT→FAILED，DB 状态正确。"""
    conn = _open_db(tmp_path)
    _seed_full_content(
        tmp_path, conn, content_id="c_aaaa4444",
        md_text="# t\n\n...",
    )

    set_provider(ScriptedProvider([], fail_remaining=True))

    result = run_gate(
        conn, gate_cfg=GateConfig(),
        anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
        now="2026-07-05T04:00:00+00:00",
    )
    assert result.failed_count == 1
    # status 应已 DRAFT→FAILED（审计 Bug 4：异常路径必须落库）
    row = conn.execute(
        "SELECT status FROM contents WHERE id=?", ("c_aaaa4444",)
    ).fetchone()
    assert row["status"] == ContentStatus.FAILED.value


def test_runner_blocker_problem_short_circuits(tmp_path) -> None:
    """critic 找 blocker → 不走重写也不走 scorer，直接 discarded。"""
    conn = _open_db(tmp_path)
    _seed_full_content(
        tmp_path, conn, content_id="c_aaaa5555",
        md_text="# t\n\n有事实错误...",
    )

    set_provider(ScriptedProvider([
        json.dumps({
            "problems": [{"category": "fact", "severity": "high",
                          "message": "编造数字"}],
            "summary": "事实错误",
        }),
        # scorer 不应被调用（blocker 短路到 DISCARD）
        json.dumps({"info": 0, "fun": 0, "view": 0,
                    "problems": [], "verdict": "x"}),
    ]))

    result = run_gate(
        conn, gate_cfg=GateConfig(),
        anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
        now="2026-07-05T04:00:00+00:00",
    )
    assert result.discarded_count == 1
    # 只调了 critic，没调 scorer（因为 blocker 走 decide_revision_action 返 BLOCK）
    assert len(llm_mod._PROVIDER.calls) == 1  # type: ignore[attr-defined]


def test_runner_fetches_source_text_for_critic(tmp_path) -> None:
    """runner 按 content.topic_id 查 topic.url，重新抓原文传给 critic 做事实核对。

    回归：真实数据冒烟测试中，critic 没有原文参照，把训练知识截止日期
    之后的真实事实误判为编造，两轮共 10 篇真实文章 100% discard。
    """
    conn = _open_db(tmp_path)
    content = _seed_full_content(
        tmp_path, conn, content_id="c_aaaa6666",
        md_text="# t\n\n正文引用了原文里的真实细节",
    )
    conn.execute(
        "UPDATE topics SET url=? WHERE id=?",
        ("https://example.com/real-article", content.topic_id),
    )
    conn.commit()

    set_provider(ScriptedProvider([
        json.dumps({"problems": [], "summary": "无问题"}),
        json.dumps({"info": 9, "fun": 9, "view": 9,
                    "problems": [], "verdict": "好"}),
    ]))

    with patch(
        "pipeline.gate.runner.source_fetcher.fetch_text",
        return_value="真实原文核对片段 XYZ789",
    ) as mock_fetch:
        run_gate(
            conn, gate_cfg=GateConfig(),
            anchors_dir=Path(__file__).parent.parent / "pipeline" / "gate" / "anchors",
            now="2026-07-05T04:00:00+00:00",
        )

    mock_fetch.assert_called_once_with("https://example.com/real-article")
    critic_prompt = llm_mod._PROVIDER.calls[0]["prompt"]  # type: ignore[attr-defined]
    assert "真实原文核对片段 XYZ789" in critic_prompt


def test_runner_requires_anchors(tmp_path) -> None:
    """无锚点 → GateError。"""
    from pipeline.utils.errors import GateError
    conn = _open_db(tmp_path)
    with pytest.raises(GateError):
        run_gate(
            conn, gate_cfg=GateConfig(),
            anchors_dir=tmp_path / "empty_dir",
            now="2026-07-05T04:00:00+00:00",
        )