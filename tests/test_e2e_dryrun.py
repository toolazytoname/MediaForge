"""M7 R7-5: 端到端 dry-run 集成测试（TECH_SPEC §9 必测项）。

按 TECH_SPEC §9 + HARD_PARTS §10 验收：
  1. 临时 db（不碰真实 state.db）
  2. LLM 全 mock（不打真实网络）
  3. 平台用 MockPublisherAdapter
  4. 全链路：score → create → gate → review → schedule → safe_publish dry-run
  5. 每个阶段断言 DB 真实状态转移（不 mock 状态机——HARD_PARTS §10 第 3 条）
  6. dry-run 模式下真实 publish 未被触发

参考：
  - M4-1 test_publish_safety.py（MockPublisherAdapter + CountingMockAdapter 模式）
  - M2-1 test_canonical.py（ScriptedProvider + llm.set_provider 注入模式）
  - M2-2 test_gate.py（run_gate + anchors_dir 路径模式）
  - M2-5 test_review.py（write_checklist + read_and_apply 模式）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pipeline import db
from pipeline.config import (
    GateConfig,
    Pillar,
    PlatformAPI,
    PublishConfig,
)
from pipeline.creators import llm as llm_mod
from pipeline.creators.canonical import create_one
from pipeline.creators.llm import (
    CompletionResult,
    LLMProvider,
    set_provider,
)
from pipeline.gate.runner import run_gate
from pipeline.models import (
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)
from pipeline.publishers.base import (
    AccountConfig,
    PostBundle,
    PublishResult,
    PublisherAdapter,
)
from pipeline.publishers.safe_publish import (
    SafePublishResult,
    safe_publish,
)
from pipeline.review.checklist import write_checklist
from pipeline.review.reader import read_and_apply
from pipeline.scheduler import plan
from pipeline.sources.dedup import content_hash
from pipeline.topics.runner import score_all


# ── LLM Mock（与 test_canonical / test_gate 同模式） ────────


class ScriptedProvider(LLMProvider):
    """按调用顺序返回预设响应的脚本 provider。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def call(
        self, prompt: str, model: str, max_tokens: int
    ) -> CompletionResult:
        self.calls.append({"prompt": prompt, "model": model})
        if not self._responses:
            raise llm_mod.RetryableError("scripted responses exhausted")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=500,
            output_tokens=300,
        )


def _scripted_responses_for_pipeline() -> list[str]:
    """全链路（无错无重写）LLM 响应序列：1 + 2 + 2 = 5 次。

    调用顺序：
      1. score_topic    → cheap tier
      2. create_one     → creative tier, stage 1 outline
      3. create_one     → creative tier, stage 2 essay
      4. gate critic    → critical tier
      5. gate scorer    → critical tier
    """
    return [
        # 1. score (cheap)
        json.dumps({"pillar": "ai", "score": 8.5, "reason": "good"}),
        # 2. create stage 1 outline (creative)
        json.dumps({"viewpoint": "v", "outline": ["a", "b"]}),
        # 3. create stage 2 essay (creative)
        "# Title\n\n正文...\n",
        # 4. gate critic: 无问题 → needs_rewrite=False（不触发重写）
        json.dumps({"problems": [], "summary": "无问题"}),
        # 5. gate scorer: 9/9/9 = 27 → 通过门禁
        json.dumps({"info": 9, "fun": 9, "view": 9, "problems": [], "verdict": "好"}),
    ]


# ── Platform Mock（与 test_publish_safety._mock_adapter 同契约） ─


class CountingMockAdapter(PublisherAdapter):
    """计数 + 区分 dry_run 的 Mock PublisherAdapter。

    契约与 safe_publish 框架对齐：
      - validate(bundle) → 返回 []（通过）
      - publish(bundle, account, dry_run=False) → 返回 PublishResult；
        post_id 含 "dry-" 前缀当 dry_run=True

    publishes 列表记录每次 publish 调用的 dry_run 参数（测试断言用）。
    """
    platform = "x"

    def __init__(self) -> None:
        self.publishes: list[bool] = []
        self.validate_calls = 0

    def validate(self, bundle: PostBundle) -> list[str]:
        self.validate_calls += 1
        return []

    def publish(
        self,
        bundle: PostBundle,
        account: AccountConfig,
        dry_run: bool = False,
    ) -> PublishResult:
        self.publishes.append(dry_run)
        prefix = "dry-" if dry_run else "REAL-"
        return PublishResult(
            platform_post_id=f"{prefix}post_001",
            url="https://example.com/post_001",
            raw_response=json.dumps({"id": "post_001"}),
        )


# ── helpers ────────────────────────────────────────────────


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "state.db")
    db.init_db(c)
    return c


def _pillars() -> list[Pillar]:
    return [
        Pillar(
            id="ai",
            name="AI",
            description="AI news",
            scoring_hint="时效性",
        ),
    ]


def _seed_raw_topic(
    conn: sqlite3.Connection, *, topic_id: str, title: str
) -> None:
    t = Topic(
        id=topic_id,
        source="rss:test",
        title=title,
        url=None,
        summary="summary",
        content_hash=content_hash(title, None),
        pillar=None,
        score=None,
        score_reason=None,
        status=TopicStatus.RAW.value,
        created_at="2026-07-06T00:00:00+00:00",
        updated_at="2026-07-06T00:00:00+00:00",
    )
    db.insert_topic(conn, t)


# ── autouse fixture：隔离模块级 LLM 状态 ───────────────────


@pytest.fixture(autouse=True)
def _reset_llm_state():
    """每个测试前后清空 LLM provider + db conn（隔离状态）。"""
    set_provider(ScriptedProvider([]))
    llm_mod.init_db_conn(None)
    yield
    set_provider(ScriptedProvider([]))
    llm_mod.init_db_conn(None)


# ── 主测试 ────────────────────────────────────────────────


class TestE2EDryRun:
    """§9 必测：端到端 dry-run 集成测试。

    每个测试都跑一遍完整 pipeline（score → create → gate → review →
    schedule），最后在 publish 阶段断言两种 dry-run 路径下真实 publish
    是否被触发（HARD_PARTS §1 全系统最高优先级 + §9 必测第 4 条）。
    """

    def _run_pipeline_to_queued_publication(
        self, tmp_path: Path
    ) -> tuple[sqlite3.Connection, Publication, AccountConfig]:
        """跑 score → create → gate → review → schedule，返回 (conn, queued pub, account)。

        不在末尾调 safe_publish——交给各 case 自己验证不同配置下的行为。
        所有状态转移必须走真实 db.transition（HARD_PARTS §10 第 3 条：禁 mock 状态机）。
        """
        conn = _open_db(tmp_path)
        # 给 llm.complete 注入 DB conn（写 llm_calls + 避免 RuntimeError）
        llm_mod.init_db_conn(conn)
        set_provider(ScriptedProvider(_scripted_responses_for_pipeline()))

        topic_id = "t_e2e00001"
        _seed_raw_topic(conn, topic_id=topic_id, title="E2E topic")
        assert db.get_topic(conn, topic_id).status == TopicStatus.RAW.value

        # ── 2. score 阶段：raw → scored → selected ──
        score_result = score_all(
            conn,
            pillars=_pillars(),
            quota=5,
            min_score=6.0,
            now="2026-07-06T01:00:00+00:00",
        )
        assert score_result.selected == 1
        topic = db.get_topic(conn, topic_id)
        assert topic.status == TopicStatus.SELECTED.value
        assert topic.score == 8.5
        assert topic.pillar == "ai"

        # ── 3. create 阶段：selected → consumed + content draft ──
        content = create_one(
            conn,
            topic,
            pillars=_pillars(),
            output_root=tmp_path / "output",
            now="2026-07-06T02:00:00+00:00",
        )
        content_id = content.id
        assert content.status == ContentStatus.DRAFT.value
        assert db.get_topic(conn, topic_id).status == TopicStatus.CONSUMED.value
        # canonical.md 真实落盘（run_gate 会再读它）
        canonical_path = Path(content.canonical_path)
        assert canonical_path.exists()
        # contents.topic_id FK
        assert content.topic_id == topic_id

        # ── 4. gate 阶段：draft → gated（走 critic → scorer → 真实 transition） ──
        gate_result = run_gate(
            conn,
            gate_cfg=GateConfig(),
            anchors_dir=(
                Path(__file__).parent.parent / "pipeline" / "gate" / "anchors"
            ),
            now="2026-07-06T03:00:00+00:00",
        )
        assert gate_result.gated_count == 1
        assert gate_result.discarded_count == 0
        assert gate_result.failed_count == 0
        row = conn.execute(
            "SELECT status, gate_score_total FROM contents WHERE id=?",
            (content_id,),
        ).fetchone()
        assert row["status"] == ContentStatus.GATED.value
        assert row["gate_score_total"] == 27.0
        # critique.md 也应落盘
        critique_path = canonical_path.parent / "critique.md"
        assert critique_path.exists()

        # ── 5. review 阶段：gated → approved ──
        # 5a. 写清单（gated 1 条）
        output_root = tmp_path / "output"
        write_checklist(
            conn,
            date_str="2026-07-06",
            output_root=output_root,
            now_iso="2026-07-06T03:30:00+00:00",
        )
        review_file = output_root / "2026-07-06" / "REVIEW.md"
        assert review_file.exists()
        # 5b. 模拟人审：把模板占位 "- [ ] approve" 替换为 "- [x] approve"
        md = review_file.read_text(encoding="utf-8")
        assert "- [ ] approve" in md
        md = md.replace("- [ ] approve", "- [x] approve")
        review_file.write_text(md, encoding="utf-8")
        # 5c. 应用：read_and_apply 走真实 db.transition（gated → approved）
        applied, rejected = read_and_apply(
            conn, review_file, log_dir=tmp_path / "logs",
        )
        assert applied == 1
        assert rejected == 0
        row = conn.execute(
            "SELECT status FROM contents WHERE id=?",
            (content_id,),
        ).fetchone()
        assert row["status"] == ContentStatus.APPROVED.value

        # ── 6. schedule 阶段：approved × platforms → queued publication ──
        approved = db.get_contents_by_status(
            conn, ContentStatus.APPROVED.value
        )
        plan_result = plan(
            approved_contents=approved,
            platform_configs={
                "x": PlatformAPI(
                    kind="api",
                    windows=["09:00-11:00", "21:00-23:00"],
                    accounts=[],
                ),
            },
            existing_publications=[],
            now_iso="2026-07-06T04:00:00+00:00",
            min_gap_hours=4,
            cross_platform_gap_minutes=30,
            tz_name="Asia/Shanghai",
        )
        assert len(plan_result.publications) >= 1
        for pub in plan_result.publications:
            db.insert_publication(conn, pub)

        queued = db.get_publications_by_status(
            conn, PublicationStatus.QUEUED.value
        )
        assert len(queued) == 1
        pub = queued[0]
        assert pub.status == PublicationStatus.QUEUED.value

        # 把 scheduled_at 改成过去（生产中 schedule → publish 间隔若干分钟）：
        # 测试 focus 在 dry_run 行为而非 _is_due 时序
        past_iso = "2026-07-06T03:00:00+00:00"
        conn.execute(
            "UPDATE publications SET scheduled_at=? WHERE id=?",
            (past_iso, pub.id),
        )
        conn.commit()
        # 重新 fetch 让 pub 对象反映最新 scheduled_at
        pub = db.get_publication(conn, pub.id)
        assert pub is not None and pub.scheduled_at == past_iso

        account = AccountConfig(
            id="main",
            credentials_path=tmp_path / "secrets" / "x.json",
        )
        return conn, pub, account

    def test_disabled_short_circuits_no_adapter_publish(
        self, tmp_path: Path
    ) -> None:
        """case A: publish.enabled=false → safe_publish 直接拒。

        断言（HARD_PARTS §1 全系统最高优先级 + §9 必测第 4 条）：
          - SafePublishResult(published=False, reason 含 'disabled')
          - adapter.publishes == []（adapter.publish 完全未被调）
          - adapter.validate_calls == 0（甚至本地校验都没做）
          - publication status 仍是 queued（config 锁早期返回，未触 DB）
        """
        conn, pub, account = self._run_pipeline_to_queued_publication(
            tmp_path
        )
        adapter = CountingMockAdapter()
        cfg = PublishConfig(
            enabled=False,
            allowed_platforms=["x"],
            min_gap_hours=4,
            max_daily_per_account=3,
            cross_platform_gap_minutes=30,
        )

        result: SafePublishResult = safe_publish(
            conn,
            pub,
            adapter,
            config=cfg,
            account=account,
            dry_run=True,
            now_iso="2026-07-06T05:00:00+00:00",
            log_dir=tmp_path / "logs",
        )

        assert result.published is False
        assert "disabled" in result.reason.lower()
        # 真实 publish 完全未被触发（HARD_PARTS §1）
        assert adapter.publishes == []
        assert adapter.validate_calls == 0
        # publication 状态没变（config 锁早期返回，未触 DB）
        row = conn.execute(
            "SELECT status FROM publications WHERE id=?", (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.QUEUED.value

    def test_dry_run_flag_calls_adapter_with_dry_run_true(
        self, tmp_path: Path
    ) -> None:
        """case B: publish.enabled=true + dry_run=True → adapter.publish 被调 1 次且 dry_run=True。

        断言（§9 必测第 4 条）：
          - SafePublishResult(published=True, dry_run=True)
          - adapter.publishes == [True]（被调 1 次，dry_run=True）
          - adapter 返回 post_id 含 'dry-' 前缀
          - publication status 推到 published（safe_publish 不区分 dry_run vs 真发；
            测试隔离靠 adapter 自身行为——它识别 dry_run 返回 dry- 前缀 post_id）
        """
        conn, pub, account = self._run_pipeline_to_queued_publication(
            tmp_path
        )
        adapter = CountingMockAdapter()
        cfg = PublishConfig(
            enabled=True,
            allowed_platforms=["x"],
            min_gap_hours=4,
            max_daily_per_account=3,
            cross_platform_gap_minutes=30,
        )

        result: SafePublishResult = safe_publish(
            conn,
            pub,
            adapter,
            config=cfg,
            account=account,
            dry_run=True,
            now_iso="2026-07-06T05:00:00+00:00",
            log_dir=tmp_path / "logs",
        )

        # adapter.publish 被调 1 次，dry_run=True
        assert adapter.publishes == [True]
        assert adapter.validate_calls == 1
        # SafePublishResult 反映 dry_run 模式
        assert result.published is True
        assert result.dry_run is True
        # adapter 在 dry_run 下返回 dry- 前缀 post_id
        assert result.platform_post_id == "dry-post_001"
        # DB 状态被推到 published（safe_publish 不区分 dry_run；
        # 测试隔离靠 adapter 自身返回 dry- 前缀 post_id）
        row = conn.execute(
            "SELECT status, platform_post_id FROM publications WHERE id=?",
            (pub.id,),
        ).fetchone()
        assert row["status"] == PublicationStatus.PUBLISHED.value
        assert row["platform_post_id"] == "dry-post_001"
