"""M10 P2 阶段 A：POST /api/v1/contents + creation_bridge 单元测试。

覆盖：
  - 端到端成功路径：selected topic → 201 + content_dict + DB 落库 + 文件落盘
  - 错误路径：topic_not_found (404) / wrong_status (400) /
    budget_exceeded (503) / 缺 topic_id (400)
  - creation_bridge 纯函数：成功 + TopicNotFoundError + TopicStatusError +
    BudgetExceeded 上抛
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.creators import llm as llm_mod
from pipeline.creators.canonical import create_one
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
from pipeline.utils.errors import BudgetExceeded
from pipeline.webui import creation_bridge, deps


# ── helpers ──────────────────────────────────────────


class ScriptedProvider(LLMProvider):
    """模拟 LLM：每次 call 返回预设响应。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def call(self, prompt, model, max_tokens):
        self.calls.append({"prompt": prompt, "model": model})
        if not self._responses:
            raise llm_mod.RetryableError("no scripted")
        return CompletionResult(
            text=self._responses.pop(0),
            input_tokens=500, output_tokens=3000,
        )


class BudgetProvider(LLMProvider):
    def call(self, prompt, model, max_tokens):
        raise BudgetExceeded(stage="create", used_usd=100.0, limit_usd=5.0)


@pytest.fixture(autouse=True)
def reset_provider():
    set_provider(ScriptedProvider([]))
    yield
    set_provider(ScriptedProvider([]))


def _seed_topic(
    conn, *, id="t_sel01", title="Selected Topic",
    status=TopicStatus.SELECTED,
):
    now = "2026-07-05T01:00:00+00:00"
    t = Topic(
        id=id, source="rss:test", title=title, url=None,
        summary=None,
        content_hash=content_hash(title, None),
        pillar="ai_daily",
        score=8.0, score_reason="ok",
        status=status, created_at=now, updated_at=now,
    )
    db.insert_topic(conn, t)
    return t


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """临时 state.db + minimal config（含 pillars 必填字段）。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Shanghai\n"
        "pillars:\n"
        "  - id: ai_daily\n"
        "    name: AI/科技日报解读\n"
        "    description: d\n"
        "    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    return tmp_path


@pytest.fixture
def client(tmp_env):
    from pipeline.webui.app import create_app
    return TestClient(create_app())


# ── API 端点：成功路径 ──────────────────────────────────


class TestCreateContentsSuccess:
    def test_201_with_content_dict_and_db_state(
        self, client, tmp_env, monkeypatch,
    ):
        """selected topic → POST /api/v1/contents → 201 + content_dict。
        DB 中 content status=draft；topic 状态 selected → consumed；
        canonical.md 文件落盘。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_aaaa0001", title="Topic A")
        conn.close()

        set_provider(ScriptedProvider([
            json.dumps({
                "viewpoint": "AI 工具化",
                "outline": ["背景", "判断"],
            }),
            "# Topic A\n\n## 背景\n正文...\n",
        ]))

        # API 走 cwd 相对路径写 output/<date>/<id>/canonical.md
        monkeypatch.chdir(tmp_env)
        r = client.post(
            "/api/v1/contents", json={"topic_id": "t_aaaa0001"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["topic_id"] == "t_aaaa0001"
        assert body["status"] == ContentStatus.DRAFT.value
        assert body["title"] == "Topic A"
        assert body["pillar"] == "ai_daily"
        assert body["id"].startswith("c_")
        assert body["canonical_path"].endswith("/canonical.md")
        assert body["formats"] == []  # tuple → list

        # DB 状态
        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?",
            ("t_aaaa0001",),
        ).fetchone()
        assert row["status"] == TopicStatus.CONSUMED.value
        c_row = conn.execute(
            "SELECT status, pillar FROM contents WHERE id=?",
            (body["id"],),
        ).fetchone()
        assert c_row["status"] == ContentStatus.DRAFT.value
        assert c_row["pillar"] == "ai_daily"
        conn.close()

        # 文件落盘
        cp = Path(body["canonical_path"])
        assert cp.exists()
        md = cp.read_text(encoding="utf-8")
        assert "# Topic A" in md

    def test_400_on_missing_topic_id(self, client):
        """body 不含 topic_id → 400 envelope。"""
        r = client.post("/api/v1/contents", json={})
        assert r.status_code == 400
        body = r.json()
        # FastAPI HTTPException wraps detail：response = {"detail": {"error": ...}}
        assert body["detail"]["error"]["code"] == "missing_topic_id"


# ── API 端点：失败路径 ──────────────────────────────────


class TestCreateContentsErrors:
    def test_404_topic_not_found(self, client):
        """传不存在 id → 404 envelope。"""
        r = client.post(
            "/api/v1/contents", json={"topic_id": "t_nope0001"},
        )
        assert r.status_code == 404
        body = r.json()
        assert body["detail"]["error"]["code"] == "topic_not_found"
        assert "t_nope0001" in body["detail"]["error"]["message"]

    def test_400_wrong_status(self, client, tmp_env):
        """raw topic → 400 envelope（不消费 raw topic）。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_raw0001", title="raw topic",
                    status=TopicStatus.RAW)
        conn.close()

        r = client.post(
            "/api/v1/contents", json={"topic_id": "t_raw0001"},
        )
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["error"]["code"] == "topic_wrong_status"
        # topic 状态未变（不被消费）
        conn = db.connect(str(tmp_env / "state.db"))
        row = conn.execute(
            "SELECT status FROM topics WHERE id=?",
            ("t_raw0001",),
        ).fetchone()
        assert row["status"] == TopicStatus.RAW.value
        conn.close()

    def test_503_budget_exceeded(self, client, tmp_env):
        """create_one 抛 BudgetExceeded → 503 envelope。"""
        conn = db.connect(str(tmp_env / "state.db"))
        _seed_topic(conn, id="t_bud0001", title="Budget topic")
        conn.close()

        set_provider(BudgetProvider())

        r = client.post(
            "/api/v1/contents", json={"topic_id": "t_bud0001"},
        )
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["error"]["code"] == "budget_exceeded"
        assert "budget exceeded" in body["detail"]["error"]["message"]


# ── creation_bridge 纯函数 ──────────────────────────────────


class TestCreationBridge:
    """直接测 create_for_topic，不走 HTTP。"""

    def test_create_for_topic_success(self, tmp_path, monkeypatch):
        """直接调用 create_for_topic：返回 Content 且 DB 落库。"""
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()

        conn = db.connect(db_path)
        topic = _seed_topic(
            conn, id="t_bridge01", title="Bridge Topic",
        )
        conn.close()

        set_provider(ScriptedProvider([
            json.dumps({"viewpoint": "v", "outline": ["a"]}),
            "# Bridge Topic\n\n## A\n...",
        ]))

        # 走 tmp_path 下的 output，避免污染 CWD
        from pipeline.config import Pillar
        pillars = [
            Pillar(id="ai_daily", name="AI",
                   description="d", scoring_hint="s"),
        ]
        conn = db.connect(db_path)
        try:
            content = creation_bridge.create_for_topic(
                conn, "t_bridge01",
                pillars=pillars,
                output_root=tmp_path / "output",
                now="2026-07-05T02:00:00+00:00",
            )
        finally:
            conn.close()

        assert content.id.startswith("c_")
        assert content.status == ContentStatus.DRAFT.value
        assert content.title == "Bridge Topic"

        # 文件落盘
        cp = Path(content.canonical_path)
        assert cp.exists()

    def test_create_for_topic_not_found(self, tmp_path, monkeypatch):
        """topic 不存在 → 抛 TopicNotFoundError。"""
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()

        from pipeline.config import Pillar
        pillars = [
            Pillar(id="ai", name="AI", description="d", scoring_hint="s"),
        ]
        conn = db.connect(db_path)
        try:
            with pytest.raises(creation_bridge.TopicNotFoundError) as ei:
                creation_bridge.create_for_topic(
                    conn, "t_nope0001", pillars=pillars,
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "t_nope0001" in str(ei.value)
        finally:
            conn.close()

    def test_create_for_topic_wrong_status(self, tmp_path, monkeypatch):
        """topic 状态非 selected → 抛 TopicStatusError。"""
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()

        conn = db.connect(db_path)
        _seed_topic(conn, id="t_raw0001", title="raw",
                    status=TopicStatus.RAW)
        conn.close()

        from pipeline.config import Pillar
        pillars = [
            Pillar(id="ai", name="AI", description="d", scoring_hint="s"),
        ]
        conn = db.connect(db_path)
        try:
            with pytest.raises(creation_bridge.TopicStatusError) as ei:
                creation_bridge.create_for_topic(
                    conn, "t_raw0001", pillars=pillars,
                    now="2026-07-05T02:00:00+00:00",
                )
            assert "selected" in str(ei.value)
            assert "raw" in str(ei.value)
        finally:
            conn.close()

    def test_create_for_topic_budget_exceeded(self, tmp_path, monkeypatch):
        """create_one 抛 BudgetExceeded → 桥原样上抛（不被捕获重包）。"""
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()

        conn = db.connect(db_path)
        _seed_topic(conn, id="t_bud0002", title="B")
        conn.close()

        set_provider(BudgetProvider())

        from pipeline.config import Pillar
        pillars = [
            Pillar(id="ai", name="AI", description="d", scoring_hint="s"),
        ]
        conn = db.connect(db_path)
        try:
            with pytest.raises(BudgetExceeded):
                creation_bridge.create_for_topic(
                    conn, "t_bud0002", pillars=pillars,
                    now="2026-07-05T02:00:00+00:00",
                )
        finally:
            conn.close()

    def test_create_for_topic_pillars_required(self, tmp_path):
        """pillars=None → 抛 ValueError（前端须传 config.pillars）。"""
        db_path = tmp_path / "state.db"
        c = db.connect(db_path)
        db.init_db(c)
        c.close()
        conn = db.connect(db_path)
        try:
            with pytest.raises(ValueError) as ei:
                creation_bridge.create_for_topic(
                    conn, "t_x", pillars=None,
                )
            assert "pillars" in str(ei.value).lower()
        finally:
            conn.close()
