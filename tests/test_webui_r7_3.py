"""M7 R7-3 webui 层测试：refactor 后 webui 行为不变 + 不再有裸写 SQL。

1. 行为不变：
   - reject 分支：状态匹配（gated）→ 200 + DB gate_verdict 真更新 + 状态转 rejected_by_human
   - reject 分支：状态不匹配（已 approved）→ 400 alert 片段（rowcount=0 路径）
   - reschedule 分支：状态匹配（queued）→ 200 + scheduled_at 真更新
   - reschedule 分支：状态不匹配（failed/published）→ 400 alert 片段
2. 反契约：`pipeline/webui/app.py` 除 `_status_counts` 的只读 SELECT 外
   不再有写 SQL（`conn.execute("UPDATE ...")`）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.config import (
    AppConfig,
    LLMConfig,
    LLMBudget,
    LLMTiers,
    Pillar,
)
from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
)


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def minimal_config() -> AppConfig:
    return AppConfig(
        timezone="Asia/Shanghai",
        pillars=[Pillar(id="ai_daily", name="AI 日报",
                        description="d", scoring_hint="s")],
        sources=[],
        llm=LLMConfig(
            tiers=LLMTiers(
                cheap="claude-haiku-4-5",
                creative="claude-sonnet-5",
                critical="claude-sonnet-5",
            ),
        ),
        budget=LLMBudget(monthly_usd=80.0),
    )


@pytest.fixture
def pre_init_db(tmp_path: Path) -> Path:
    """在 tmp_path 建一个已 init 的 state.db。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


@pytest.fixture
def conn(pre_init_db: Path) -> sqlite3.Connection:
    """返回已 init 的临时 db 连接（与 test_webui.py 同模式）。"""
    c = db.connect(pre_init_db)
    yield c
    c.close()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pre_init_db: Path,
    minimal_config: AppConfig,
) -> TestClient:
    import pipeline.webui.app as app_mod
    import pipeline.webui.deps as deps
    monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
    monkeypatch.setattr(
        deps, "load_config", lambda *a, **kw: minimal_config,
    )
    return TestClient(app_mod.create_app())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_content(
    conn: sqlite3.Connection,
    *,
    id: str = "c_r730001",
    status: str = ContentStatus.GATED.value,
) -> None:
    """seed content + topic（满足 FK）。"""
    now = _now()
    tid = "t_" + id.removeprefix("c_")
    db.insert_topic(conn, Topic(
        id=tid, source="rss:test", title="T", url=None,
        summary=None, content_hash=f"h-{tid}", pillar="ai_daily",
        score=7.0, score_reason=None,
        status=TopicStatus.CONSUMED.value,
        created_at=now, updated_at=now,
    ))
    db.insert_content(conn, Content(
        id=id, topic_id=tid, pillar="ai_daily",
        title="T", canonical_path=f"output/{id}/canonical.md",
        formats='["x"]', gate_score_total=27.0,
        gate_scores='{"info":9,"fun":9,"view":9}',
        gate_verdict=None,
        status=status,
        created_at=now, updated_at=now,
    ))


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    id: str = "p_r730001",
    status: str = PublicationStatus.QUEUED.value,
    scheduled_at: str = "2026-07-07T10:00:00+00:00",
) -> None:
    """seed publication + content + topic（满足 FK）。"""
    cid = "c_" + id.removeprefix("p_")
    _seed_content(conn, id=cid, status=ContentStatus.APPROVED.value)
    now = _now()
    db.insert_publication(conn, Publication(
        id=id, content_id=cid, platform="x",
        account_id="main", scheduled_at=scheduled_at,
        published_at=None, platform_post_id=None,
        platform_url=None, error=None, retry_count=0,
        status=status,
        created_at=now, updated_at=now,
    ))


# ── 1. reject 分支（db.set_gate_verdict + transition） ──────


class TestRejectBehaviorUnchanged:
    def test_reject_gated_status_match_succeeds(
        self, client: TestClient, conn: sqlite3.Connection,
    ) -> None:
        """gated 状态 reject → 200 + gate_verdict 真更新 + 状态转 rejected_by_human。"""
        _seed_content(conn, id="c_rej0001", status=ContentStatus.GATED.value)
        r = client.post(
            "/review/c_rej0001",
            data={"decision": "reject", "reason": "内容空洞"},
        )
        assert 200 <= r.status_code < 400, r.text
        row = conn.execute(
            "SELECT status, gate_verdict FROM contents WHERE id=?",
            ("c_rej0001",),
        ).fetchone()
        assert row["status"] == ContentStatus.REJECTED_BY_HUMAN.value
        assert "内容空洞" in row["gate_verdict"]

    def test_reject_already_approved_returns_alert(
        self, client: TestClient, conn: sqlite3.Connection,
    ) -> None:
        """approved 状态 reject → 400 alert 片段（rowcount=0 路径）。

        修复前裸 SQL：UPDATE WHERE id=? AND status='gated' → rowcount=0 → _alert。
        修复后走 db.set_gate_verdict：行为必须完全等价。
        """
        _seed_content(
            conn, id="c_rej0002", status=ContentStatus.APPROVED.value,
        )
        r = client.post(
            "/review/c_rej0002",
            data={"decision": "reject", "reason": "nope"},
        )
        assert r.status_code >= 400, r.text
        assert 'role="alert"' in r.text
        # 状态应保持原状（未被 transition 改）
        row = conn.execute(
            "SELECT status FROM contents WHERE id=?",
            ("c_rej0002",),
        ).fetchone()
        assert row["status"] == ContentStatus.APPROVED.value

    def test_reject_nonexistent_content_returns_alert(
        self, client: TestClient,
    ) -> None:
        """不存在的内容 → 400 alert（rowcount=0）。"""
        r = client.post(
            "/review/c_ghost001",
            data={"decision": "reject", "reason": "nope"},
        )
        assert r.status_code >= 400
        assert 'role="alert"' in r.text


# ── 2. reschedule 分支（db.reschedule_publication） ────────


class TestRescheduleBehaviorUnchanged:
    def test_reschedule_queued_status_match_succeeds(
        self, client: TestClient, conn: sqlite3.Connection,
    ) -> None:
        """queued 状态 reschedule → 200 + scheduled_at 真更新。"""
        _seed_publication(
            conn, id="p_rs00001",
            status=PublicationStatus.QUEUED.value,
            scheduled_at="2026-07-07T10:00:00+00:00",
        )
        new_time = "2026-07-08T18:30:00+00:00"
        r = client.post(
            "/publications/p_rs00001/reschedule",
            data={"scheduled_at": new_time},
        )
        assert 200 <= r.status_code < 400, r.text
        row = conn.execute(
            "SELECT scheduled_at FROM publications WHERE id=?",
            ("p_rs00001",),
        ).fetchone()
        assert row["scheduled_at"].startswith("2026-07-08T18:30")

    @pytest.mark.parametrize("bad_status", [
        PublicationStatus.FAILED.value,
        PublicationStatus.PUBLISHED.value,
        PublicationStatus.CANCELLED.value,
    ])
    def test_reschedule_non_queued_returns_alert(
        self, client: TestClient, conn: sqlite3.Connection,
        bad_status: str,
    ) -> None:
        """非 queued 状态 reschedule → 400 alert 片段（rowcount=0 路径）。"""
        _seed_publication(
            conn, id=f"p_rs_{bad_status}", status=bad_status,
        )
        r = client.post(
            f"/publications/p_rs_{bad_status}/reschedule",
            data={"scheduled_at": "2026-07-09T08:00:00+00:00"},
        )
        assert r.status_code >= 400, r.text
        assert 'role="alert"' in r.text

    def test_reschedule_nonexistent_publication_returns_alert(
        self, client: TestClient,
    ) -> None:
        """不存在的 publication → 400 alert。"""
        r = client.post(
            "/publications/p_ghost001/reschedule",
            data={"scheduled_at": "2026-07-09T08:00:00+00:00"},
        )
        assert r.status_code >= 400
        assert 'role="alert"' in r.text


# ── 3. 反契约：app.py 除 _status_counts 外不再有裸写 SQL ────


class TestNoRawUpdateSqlInApp:
    def test_app_py_has_no_raw_update_sql_outside_status_counts(
        self,
    ) -> None:
        """反契约：`conn.execute("UPDATE ...")` 应只在 db.py 里出现。

        app.py 里只剩 SELECT（_status_counts 的只读 COUNT GROUP BY），
        写 SQL 一律抽到 db.py 助手函数。
        """
        app_path = Path(__file__).parent.parent / "pipeline" / "webui" / "app.py"
        text = app_path.read_text(encoding="utf-8")
        # 抓出所有 `conn.execute(...)` 调用
        update_calls = re.findall(
            r'conn\.execute\(\s*["\'](UPDATE|INSERT|DELETE)',
            text,
        )
        assert update_calls == [], (
            f"app.py 不应出现裸写 SQL，发现 {len(update_calls)} 处："
            f"{update_calls}"
        )