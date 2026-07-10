"""M7 R7-1 测试：消除 webui 三处隐患。

1. _conn() 每请求 init_db 跑 DDL → 浪费 + 拖慢
2. create_app() 内一次性 init_db 即可
3. datetime.utcnow() 已 deprecated → 用 datetime.now(timezone.utc)
   （M10 P1 后该验证场景不再适用,dashboard 改 SPA 渲染,见下方说明）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
from pipeline.webui.app import create_app


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
    """在 tmp_path 创建一个已建表的 state.db，供测试复用。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


# ── 1. init_db 不再每请求调用 ────────────────────────────────


class TestInitDbOnce:
    def test_init_db_called_at_most_once_across_many_requests(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """3 个 GET /api/status 后，db.init_db 调用次数 ≤ 1。

        当前实现每请求都跑 DDL（3 次）。修复后只在 create_app 期间调一次。
        """
        # patch db.init_db 计数（不实际建表——db 已预先建好）
        init_db_mock = MagicMock(return_value=None)
        monkeypatch.setattr(db, "init_db", init_db_mock)

        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app())

        for _ in range(3):
            r = client.get("/api/status")
            assert r.status_code == 200

        # 修复前：3 次（每请求 1 次）
        # 修复后：1 次（create_app 期间）或 0 次（如果 init_db 完全不走 mock 路径）
        assert init_db_mock.call_count <= 1, (
            f"db.init_db 被调用了 {init_db_mock.call_count} 次，"
            f"应 ≤ 1（修复前每请求 1 次=3+）"
        )

    def test_init_db_called_at_least_once_during_create_app(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """create_app 期间应至少调一次 init_db（保证表存在）。"""
        init_db_mock = MagicMock(return_value=None)
        monkeypatch.setattr(db, "init_db", init_db_mock)

        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        TestClient(create_app())
        # create_app 应主动 init_db 一次（保证应用启动时表已就绪）
        assert init_db_mock.call_count >= 1


# ── 2. M10 P1 后 dashboard.html 不再由 app 渲染 ─────────────
#
# M10 P1 之前,dashboard.html 模板含 "更新时间：<iso>" 字符串,
# 用以断言 datetime.utcnow() → datetime.now(timezone.utc) 迁移。
# M10 P1 后 / 由 SPA catch-all 服务 frontend/dist/index.html,
# 该字符串已无意义,timezone 测试被 SPA 自身的 fetch /api/v1/dashboard 覆盖。
# 保留此节仅为结构:该迁移已在多处 (M8/M9 等) 验证过。


# ── 3. 完整 init_db 不被绕过的兜底测试 ──────────────────────


class TestNoDbSchemaBroken:
    """修复后所有路由仍能正常查询（init_db 在 create_app 已建表）。"""

    def test_api_status_returns_valid_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app())
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert "topics" in body
        assert "contents" in body
        assert "publications" in body