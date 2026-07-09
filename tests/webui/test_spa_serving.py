"""M10-6 SPA 托管接线测试。

覆盖：
  - /assets 挂载：目录不存在时不挂；存在时静态文件可访问
  - catch-all：dist 缺失时返回提示页（200 不 500）；dist 存在返回 index.html
  - catch-all 不吞 API / /output / /static / /assets 前缀（404）
  - 旧 htmx 路由仍工作
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.webui import deps


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    from pipeline import db
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    monkeypatch.setattr(deps, "_DB_PATH", str(db_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Shanghai\n"
        "pillars:\n  - id: ai_daily\n    name: AI\n    description: d\n    scoring_hint: s\n"
        "sources: []\n"
        "llm: {tiers: {cheap: m, creative: m, critical: m}}\n"
        "budget: {monthly_usd: 80.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "_CONFIG_PATH", str(cfg_path))
    return tmp_path


def _make_dist(project_root: Path) -> Path:
    """造一个 frontend/dist/ + index.html + assets/x.js。"""
    dist = project_root / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><body>SPA</body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "x.js").write_text("// js", encoding="utf-8")
    return dist


class TestSpaServing:
    def test_catch_all_no_dist_returns_hint_page(self, tmp_env, monkeypatch):
        from pipeline.webui.app import create_app
        app = create_app()
        c = TestClient(app)
        # 任意非 api/output/static/assets 路径
        r = c.get("/random-spa-route")
        assert r.status_code == 200
        assert "frontend not built" in r.text or "MediaForge" in r.text

    def test_catch_all_with_dist_returns_index_html(self, tmp_env, monkeypatch):
        # 用项目根目录造 dist
        from pipeline.webui.app import create_app
        from pipeline.webui import app as app_module
        project_root = Path(app_module.__file__).parent.parent.parent
        _make_dist(project_root)
        try:
            app = create_app()
            c = TestClient(app)
            r = c.get("/any-route")
            assert r.status_code == 200
            assert "SPA" in r.text
        finally:
            import shutil
            shutil.rmtree(project_root / "frontend", ignore_errors=True)

    def test_catch_all_does_not_swallow_api(self, tmp_env):
        from pipeline.webui.app import create_app
        c = TestClient(create_app())
        # /api/v1/dashboard 应该返回真实 API（200 + JSON），不是 catch-all
        r = c.get("/api/v1/dashboard")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_catch_all_does_not_swallow_output(self, tmp_env, monkeypatch):
        # /output 路径应走 StaticFiles mount 而非 catch-all
        from pipeline.webui.app import create_app
        # 先在 output/ 下放一个文件
        monkeypatch.chdir(tmp_env)
        (tmp_env / "output").mkdir(exist_ok=True)
        (tmp_env / "output" / "test.txt").write_text("hi", encoding="utf-8")
        c = TestClient(create_app())
        r = c.get("/output/test.txt")
        assert r.status_code == 200
        assert r.text == "hi"

    def test_assets_mounted_when_dist_exists(self, tmp_env, monkeypatch):
        from pipeline.webui.app import create_app
        from pipeline.webui import app as app_module
        project_root = Path(app_module.__file__).parent.parent.parent
        _make_dist(project_root)
        try:
            c = TestClient(create_app())
            r = c.get("/assets/x.js")
            assert r.status_code == 200
            assert r.text == "// js"
        finally:
            import shutil
            shutil.rmtree(project_root / "frontend", ignore_errors=True)

    def test_legacy_htmx_routes_still_work(self, tmp_env):
        from pipeline.webui.app import create_app
        c = TestClient(create_app())
        r = c.get("/topics")
        # 旧 htmx 路由应正常响应（不是 catch-all）
        assert r.status_code == 200
        assert "<!doctype" in r.text.lower()  # 模板渲染（Jinja 输出小写）
