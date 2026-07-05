"""M2-5 review 阶段：审核清单。

测试 checklist 生成 / reader 解析 / notify 推送 / run_review 编排。
参考：TASKS.md M2-5、ARCHITECTURE.md §3.5、HARD_PARTS §5 幂等。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from unittest.mock import patch

import pytest

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Topic,
    TopicStatus,
)
from pipeline.review import (
    ReviewDecision,
    run_review,
)
from pipeline.review.checklist import (
    build_checklist_markdown,
    checklist_path,
)
from pipeline.review.reader import (
    apply_decisions,
    parse_review_markdown,
)


# ── helpers ────────────────────────────────────────────────

_NOW = "2026-07-05T12:00:00+00:00"


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "state.db")
    db.init_db(conn)
    return conn


def _make_topic(
    conn: sqlite3.Connection, *, id: str = "t_aaaaaaaa", title: str = "T"
) -> Topic:
    t = Topic(
        id=id, source="rss:test", title=title, url=None,
        summary=None, content_hash=f"hash-{id}", pillar="oss_review",
        score=None, score_reason=None, status=TopicStatus.CONSUMED.value,
        created_at=_NOW, updated_at=_NOW,
    )
    db.insert_topic(conn, t)
    return t


def _make_content(
    conn: sqlite3.Connection, *,
    output_root: Path | None = None,
    id: str = "c_11111111",
    title: str = "测试内容",
    status: str = ContentStatus.GATED.value,
    pillar: str = "oss_review",
    date_dir: str = "2026-07-05",
    gate_total: float = 25.0,
    gate_scores: dict | None = None,
    gate_verdict: str = "通过",
    topic_id: str | None = None,
) -> Content:
    """插入 content 并在 output_root 下真实创建目录/canonical.md。

    默认 output_root=tmp_path（测试隔离）。canonical_path 写为相对路径
    '<output_root>/<date>/<cid>/canonical.md'，与 review/checklist 约定的
    'output_root/<date>/<cid>/canonical.md' 完全对齐。

    topic_id 默认 = 't_<id without c_ prefix>'，避免 UNIQUE(topic_id) 冲突。
    """
    if output_root is None:
        output_root = Path("/tmp/mf_test_root")
    if topic_id is None:
        topic_id = "t_" + id.removeprefix("c_")
    # 也插入对应 topic（topic_id UNIQUE + contents.topic_id FK）
    _make_topic(conn, id=topic_id, title="T:" + id)
    canonical_path = str(output_root / date_dir / id / "canonical.md")
    full_dir = Path(canonical_path).parent
    full_dir.mkdir(parents=True, exist_ok=True)
    (full_dir / "canonical.md").write_text("# " + title, encoding="utf-8")
    c = Content(
        id=id, topic_id=topic_id, pillar=pillar, title=title,
        canonical_path=canonical_path, formats="[]",
        gate_score_total=gate_total,
        gate_scores=json.dumps(gate_scores or {"info": 8, "fun": 8, "view": 9}),
        gate_verdict=gate_verdict, status=status,
        created_at=_NOW, updated_at=_NOW,
    )
    db.insert_content(conn, c)
    return c


def _all_statuses(conn: sqlite3.Connection, content_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT status FROM contents WHERE id=?", (content_id,)
    ).fetchall()
    return [r["status"] for r in rows]


# ── checklist.py ───────────────────────────────────────────


class TestChecklistPath:
    def test_date_from_iso_string(self) -> None:
        assert checklist_path("output", "2026-07-05T13:00:00+00:00") == \
            Path("output/2026-07-05/REVIEW.md")

    def test_date_from_iso_with_z_suffix(self) -> None:
        # 边界：slice 取前 10 个字符，对 Z 与 +00:00 都鲁棒
        assert checklist_path("output", "2026-07-05T13:00:00Z") == \
            Path("output/2026-07-05/REVIEW.md")


class TestBuildChecklistMarkdown:
    def test_one_gated_content_renders_full_block(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        _make_content(
            conn, output_root=tmp_path, id="c_aaaaaaaa", title="X 评估", gate_total=27.0,
            gate_scores={"info": 9, "fun": 9, "view": 9},
            gate_verdict="很好",
        )

        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )

        assert "# 审核清单 — 2026-07-05" in md
        assert "## [c_aaaaaaaa] X 评估" in md
        assert "pillar: oss_review" in md
        assert "gate_score_total: 27" in md
        assert "gate_verdict: 很好" in md
        # canonical 路径相对 REVIEW.md
        assert "./c_aaaaaaaa/canonical.md" in md
        # 复选框默认 [ ]
        assert "- [ ] approve" in md
        # 行尾 reject 提示
        assert "reject:" in md

    def test_multiple_gated_sorted_by_score_desc(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_low00001", title="低分", gate_total=21.0)
        _make_content(conn, output_root=tmp_path, id="c_high0001", title="高分", gate_total=27.0)
        _make_content(conn, output_root=tmp_path, id="c_mid00001", title="中分", gate_total=24.0)

        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )

        idx_high = md.index("c_high0001")
        idx_mid = md.index("c_mid00001")
        idx_low = md.index("c_low00001")
        assert idx_high < idx_mid < idx_low

    def test_no_gated_emits_empty_placeholder(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )
        assert "# 审核清单 — 2026-07-05" in md
        # 应明示 0 条
        assert "0" in md
        assert "## [" not in md

    def test_only_gated_listed_not_others(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_gated001", status=ContentStatus.GATED.value)
        _make_content(conn, output_root=tmp_path, id="c_draft001", status=ContentStatus.DRAFT.value)
        _make_content(conn, output_root=tmp_path, id="c_appv0001", status=ContentStatus.APPROVED.value)

        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )

        assert "c_gated001" in md
        assert "c_draft001" not in md
        assert "c_appv0001" not in md

    def test_cover_image_linked_when_exists(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        c = _make_content(conn, output_root=tmp_path, id="c_cover0001")
        # 模拟派生已有封面
        cover_dir = tmp_path / "2026-07-05" / "c_cover0001" / "xiaohongshu"
        (cover_dir / "cards").mkdir(parents=True)
        (cover_dir / "cards" / "cover.png").write_bytes(b"\x89PNG\r\n")

        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )

        assert "xiaohongshu/cards/cover.png" in md

    def test_missing_cover_image_graceful(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_nocov001")

        md = build_checklist_markdown(
            conn, date_str="2026-07-05", output_root=tmp_path
        )
        # 没图就完全不渲染 cover 行（不渲染破损 link）
        assert "cover_image" not in md
        # 但主体仍渲染
        assert "c_nocov001" in md


# ── reader.py ──────────────────────────────────────────────


class TestParseReviewMarkdown:
    def test_parse_approve_marker(self) -> None:
        md = """
# 审核清单 — 2026-07-05

## [c_aaaaaaaa] 标题
- pillar: oss_review
- [x] approve
"""
        decisions = parse_review_markdown(md)
        assert ReviewDecision("c_aaaaaaaa", "approve", None) in decisions

    def test_parse_reject_marker_with_reason(self) -> None:
        md = """
## [c_bbbbbbbb] 标题
- pillar: oss_review
- [-] reject: 信息密度太低
"""
        decisions = parse_review_markdown(md)
        assert ReviewDecision("c_bbbbbbbb", "reject", "信息密度太低") in decisions

    def test_parse_reject_marker_without_reason(self) -> None:
        """模板占位行 `- [-] reject:`（理由为空）必须视为"未标记"。

        否则 run_review 在读旧 + 写新时会立刻把刚生成的清单 reject 一遍
        ——这是 template/editor roundtrip 的关键防线。
        """
        md = """
## [c_cccccccc] 标题
- [-] reject:
"""
        decisions = parse_review_markdown(md)
        # 空理由 = 模板占位，不是人写的决定
        assert ReviewDecision("c_cccccccc", "reject", "") not in decisions
        assert decisions == []

    def test_parse_unmarked_section_ignored(self) -> None:
        md = """
## [c_dddddddd] 标题
- pillar: oss_review
- [ ] approve
"""
        decisions = parse_review_markdown(md)
        # 未标记 = 不在决策里
        assert ReviewDecision("c_dddddddd", "approve", None) not in decisions
        assert ReviewDecision("c_dddddddd", "reject", None) not in decisions
        assert decisions == []

    def test_parse_reject_wins_over_approve(self) -> None:
        """人同时勾了 [x] 和 [-] 时，保守：reject 胜出（更安全）。"""
        md = """
## [c_eeeeeeee] 标题
- [x] approve
- [-] reject: 误标
"""
        decisions = parse_review_markdown(md)
        assert ReviewDecision("c_eeeeeeee", "reject", "误标") in decisions
        assert ReviewDecision("c_eeeeeeee", "approve", None) not in decisions

    def test_parse_multiple_sections(self) -> None:
        md = """
## [c_11111111] A
- [x] approve

## [c_22222222] B
- [-] reject: 差

## [c_33333333] C
- [ ] approve
"""
        decisions = parse_review_markdown(md)
        assert len(decisions) == 2
        assert ReviewDecision("c_11111111", "approve", None) in decisions
        assert ReviewDecision("c_22222222", "reject", "差") in decisions
        # C 未标记
        assert not any(d.content_id == "c_33333333" for d in decisions)


class TestApplyDecisions:
    def _seed(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        _make_content(conn, output_root=tmp_path, id="c_apply0001", status=ContentStatus.GATED.value)

    def test_apply_approve(self, tmp_path: Path) -> None:
        conn = _make_conn(tmp_path)
        self._seed(conn, tmp_path)

        decisions = [ReviewDecision("c_apply0001", "approve", None)]
        apply_decisions(conn, decisions)

        assert _all_statuses(conn, "c_apply0001") == [ContentStatus.APPROVED.value]

    def test_apply_reject(self, tmp_path: Path) -> None:
        conn = _make_conn(tmp_path)
        self._seed(conn, tmp_path)

        decisions = [ReviewDecision("c_apply0001", "reject", "内容空洞")]
        apply_decisions(conn, decisions)

        assert _all_statuses(conn, "c_apply0001") == [
            ContentStatus.REJECTED_BY_HUMAN.value
        ]

    def test_apply_skip_non_gated(self, tmp_path: Path) -> None:
        """已 approved 的不应被覆盖（HARD_PARTS §5 幂等）。"""
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_skip00001", status=ContentStatus.GATED.value)
        _make_content(conn, output_root=tmp_path, id="c_skip00002", status=ContentStatus.APPROVED.value)

        decisions = [
            ReviewDecision("c_skip00001", "approve", None),
            # approved 状态再次 approve 应当跳过（不影响），不抛异常
            ReviewDecision("c_skip00002", "approve", None),
        ]
        apply_decisions(conn, decisions)

        assert _all_statuses(conn, "c_skip00001") == [
            ContentStatus.APPROVED.value
        ]
        assert _all_statuses(conn, "c_skip00002") == [
            ContentStatus.APPROVED.value
        ]

    def test_apply_missing_content_id_warns_but_continues(
        self, tmp_path: Path
    ) -> None:
        conn = _make_conn(tmp_path)
        self._seed(conn, tmp_path)

        decisions = [
            ReviewDecision("c_nothere01", "approve", None),
            ReviewDecision("c_apply0001", "approve", None),
        ]
        # 不应抛异常，只 warn 跳过
        apply_decisions(conn, decisions)

        assert _all_statuses(conn, "c_apply0001") == [
            ContentStatus.APPROVED.value
        ]


# ── notify.py ──────────────────────────────────────────────


class TestNotify:
    def test_webhook_null_skips(self) -> None:
        from pipeline.review.notify import notify_review
        # 应无异常、无网络
        notify_review(webhook_url=None, count=3, review_path=Path("/tmp/r.md"))

    def test_webhook_set_posts_json(self) -> None:
        from pipeline.review.notify import notify_review

        with patch("pipeline.review.notify.httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.raise_for_status.return_value = None

            notify_review(
                webhook_url="https://example.com/hook",
                count=2,
                review_path=Path("/tmp/REVIEW.md"),
            )

            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert args[0] == "https://example.com/hook"
            body = kwargs.get("json") or json.loads(kwargs.get("data") or "{}")
            assert "text" in body
            assert "2" in body["text"]
            assert "REVIEW.md" in body["text"]

    def test_webhook_failure_does_not_raise(self) -> None:
        """webhook 挂掉不能阻断 review 主流程。"""
        from pipeline.review.notify import notify_review
        import httpx

        with patch("pipeline.review.notify.httpx.post") as mock_post:
            mock_post.side_effect = httpx.HTTPError("net down")

            # 不应抛异常
            notify_review(
                webhook_url="https://example.com/hook",
                count=1,
                review_path=Path("/tmp/REVIEW.md"),
            )


# ── run_review 编排 ────────────────────────────────────────


class TestRunReview:
    def test_run_generates_then_applies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_review 应该是：先读旧 REVIEW.md 落库 → 再生成新 REVIEW.md。"""
        # 隔离：让 checklist_path 把日期目录建到 tmp_path 下
        monkeypatch.chdir(tmp_path)

        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_run00001", title="第一次", gate_total=27.0)

        # 第一次跑：仅生成，无决定
        result1 = run_review(
            conn, date_str="2026-07-05", output_root=tmp_path,
            now_iso="2026-07-05T12:00:00+00:00",
        )
        assert result1.generated == 1
        assert result1.applied == 0
        assert result1.rejected == 0
        # REVIEW.md 已写入
        review_file = tmp_path / "2026-07-05" / "REVIEW.md"
        assert review_file.exists()
        # 内容仍 gated
        assert _all_statuses(conn, "c_run00001") == [ContentStatus.GATED.value]

        # 人编辑：标 [x]
        md = review_file.read_text(encoding="utf-8").replace(
            "- [ ] approve", "- [x] approve"
        )
        review_file.write_text(md, encoding="utf-8")

        # 第二次跑：先读旧（应用）→ 再写新
        result2 = run_review(
            conn, date_str="2026-07-05", output_root=tmp_path,
            now_iso="2026-07-05T12:00:00+00:00",
        )
        assert result2.applied == 1
        # 该条已 approved
        assert _all_statuses(conn, "c_run00001") == [
            ContentStatus.APPROVED.value
        ]
        # 新生成的 REVIEW.md 不再含 c_run00001（已非 gated）
        md2 = review_file.read_text(encoding="utf-8")
        assert "c_run00001" not in md2

    def test_run_idempotent_when_no_markings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_idem0001")

        result1 = run_review(
            conn, date_str="2026-07-05", output_root=tmp_path,
            now_iso="2026-07-05T12:00:00+00:00",
        )
        result2 = run_review(
            conn, date_str="2026-07-05", output_root=tmp_path,
            now_iso="2026-07-05T12:00:00+00:00",
        )

        # 两次都 generated=1（生成），applied=0（人未标记）
        assert result1.generated == 1
        assert result1.applied == 0
        assert result2.generated == 1
        assert result2.applied == 0
        assert _all_statuses(conn, "c_idem0001") == [ContentStatus.GATED.value]

    def test_run_with_notify_calls_notify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_notify01")

        with patch("pipeline.review.notify.httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.raise_for_status.return_value = None

            run_review(
                conn, date_str="2026-07-05", output_root=tmp_path,
                now_iso="2026-07-05T12:00:00+00:00",
                webhook_url="https://example.com/hook",
            )
            # 有内容时通知一次
            assert mock_post.call_count == 1

    def test_run_no_gated_skips_notify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        conn = _make_conn(tmp_path)
        # 没有 gated

        with patch("pipeline.review.notify.httpx.post") as mock_post:
            run_review(
                conn, date_str="2026-07-05", output_root=tmp_path,
                now_iso="2026-07-05T12:00:00+00:00",
                webhook_url="https://example.com/hook",
            )
            # 0 条时不要骚扰人
            assert mock_post.call_count == 0

    def test_summary_line_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        conn = _make_conn(tmp_path)
        _make_content(conn, output_root=tmp_path, id="c_sum00001")

        result = run_review(
            conn, date_str="2026-07-05", output_root=tmp_path,
            now_iso="2026-07-05T12:00:00+00:00",
        )
        summary = (
            f"review: {result.generated} generated, "
            f"{result.applied} approved, {result.rejected} rejected"
        )
        # 固定顺序 + 明确字段（run.py 直接打印它）
        assert summary == "review: 1 generated, 0 approved, 0 rejected"