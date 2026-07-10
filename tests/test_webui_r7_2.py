"""M7 R7-2 测试：消除 /output 静态挂载条件导致图卡 404。

错点：`pipeline/webui/app.py:97-112` 的 /output 与 /static 用 `if exists:`
挂载。修复后：/output 启动时自动 mkdir 并无条件挂载；/static 无条件挂载。
这样流水线新生成的图卡无需重启 webui 即可访问。
"""
from __future__ import annotations

import sqlite3
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


# ── 最小 PNG（8 字节 magic + 至少 1 个 chunk） ───────────────────
# 真实 1×1 透明 PNG，StaticFiles 会按扩展名返回 image/png
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa6\x35\x81\x84"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
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
    """在 tmp_path 建一个已 init 的 state.db，避开 db.connect 默认路径。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


# ── 1. /output 启动时不存在也能挂载，新文件可访问 ────────────


class TestOutputDirAutoCreate:
    def test_output_dir_missing_at_create_app_then_png_accessible(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """启动时 output/ 不存在 → create_app 应 mkdir 后挂载。

        之后在 output/ 下写 PNG，GET /output/... 应返回 200 + image/png。
        这是 R7-2 的核心修复场景。
        """
        # 关键：chdir 到 tmp_path，让 Path("output") 指向这里
        monkeypatch.chdir(tmp_path)

        # 确认启动时 output/ 确实不存在
        assert not (tmp_path / "output").exists()

        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app_safe(monkeypatch))

        # 验证 create_app 已创建 output/（自动 mkdir 生效）
        assert (tmp_path / "output").is_dir(), (
            "create_app 应在挂载前 mkdir output/，但目录仍不存在"
        )

        # 流水线后建：先建了 output/，再写入 PNG（模拟流水线运行后产图）
        png_dir = tmp_path / "output" / "2026-01-01"
        png_dir.mkdir(parents=True, exist_ok=True)
        png_path = png_dir / "x.png"
        png_path.write_bytes(_MINIMAL_PNG)

        r = client.get("/output/2026-01-01/x.png")
        assert r.status_code == 200, (
            f"应返回 200 但拿到 {r.status_code}，body: {r.text[:300]}"
        )
        assert r.headers["content-type"].startswith("image/png"), (
            f"content-type 应为 image/png，实际: {r.headers.get('content-type')}"
        )

    def test_output_dir_existing_at_create_app_also_works(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """启动时 output/ 已存在 → 也应能挂载并提供访问（回归覆盖）。"""
        monkeypatch.chdir(tmp_path)
        # 启动前就有 output/
        (tmp_path / "output" / "2026-01-01").mkdir(parents=True)
        (tmp_path / "output" / "2026-01-01" / "y.png").write_bytes(_MINIMAL_PNG)

        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app_safe(monkeypatch))
        r = client.get("/output/2026-01-01/y.png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")

    def test_output_route_is_readonly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """/output 必须只读（StaticFiles 默认行为）——红线条目。

        PUT/POST/DELETE 都应 405。这是契约硬性要求。
        """
        monkeypatch.chdir(tmp_path)

        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app_safe(monkeypatch))
        # 尝试 POST/PUT/DELETE 都应被拒绝（StaticFiles 只支持 GET/HEAD）
        assert client.post("/output/test.txt").status_code == 405
        assert client.put("/output/test.txt").status_code == 405
        assert client.delete("/output/test.txt").status_code == 405


# ── 2. (历史) /static 无 if 条件也能挂 ─────────────────────
#
# M10 P1 后 /static 已废弃,pico.min.css 不再 vendored。
# /assets(Vite 产物)取代它。SPA build 验证见 test_spa_serving.py。
# 本节不再有断言 —— M3-3 时期的 CSS 静态测试已被 SPA 端 AntD Vue 自带
# 样式链路取代,FastAPI / 静态资源契约由 test_spa_serving.test_assets_mounted
# 当 dist 存在时实际覆盖。

# ── 3. 行为契约：/output 与 SPA 都对外只读 ────────────────


class TestStaticFilesUnchanged:
    """回归覆盖：修复不应影响路由签名和其它行为。"""

    def test_root_serves_spa_index(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pre_init_db: Path,
        minimal_config: AppConfig,
    ) -> None:
        """/ 由 SPA catch-all 服务 frontend/dist/index.html。"""
        import pipeline.webui.app as app_mod
        import pipeline.webui.deps as deps
        monkeypatch.setattr(deps, "_DB_PATH", str(pre_init_db))
        monkeypatch.setattr(
            deps, "load_config", lambda *a, **kw: minimal_config,
        )

        client = TestClient(create_app_safe(monkeypatch))
        r = client.get("/")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        # SPA 不再用 Pico
        assert "pico" not in r.text.lower()

    def test_api_status_still_renders(
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

        client = TestClient(create_app_safe(monkeypatch))
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert "topics" in body
        assert "contents" in body
        assert "publications" in body


# ── helper ──────────────────────────────────────────────────


def create_app_safe(monkeypatch: pytest.MonkeyPatch):
    """调 create_app。monkeypatch 不直接用，保留以备对称引用。"""
    from pipeline.webui.app import create_app
    return create_app()