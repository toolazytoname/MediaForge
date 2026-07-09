"""M10-6 SPA 托管接线测试（修订：dist 默认提交 + assets 真实挂载）。

覆盖：
  - /assets 挂载：dist 存在时静态文件可访问
  - catch-all：dist 存在 → 返回 index.html
  - catch-all 不吞 API / /output / /static / /assets 前缀（404）
  - 旧 htmx 路由仍工作
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from pipeline.webui import deps


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
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


def _dist_dir() -> Path:
    """frontend/dist 真实路径（M10-7 默认 build 后存在）。"""
    from pipeline.webui import app as app_module
    return Path(app_module.__file__).parent.parent.parent / "frontend" / "dist"


class TestSpaServing:
    def test_catch_all_returns_index_html_when_dist_exists(self, tmp_env):
        from pipeline.webui.app import create_app
        # 假定 dist 已 build 存在（M10-7 完成后 dist 默认提交）
        if not _dist_dir().is_dir():
            pytest.skip("frontend/dist not built; run `cd frontend && npm run build`")
        c = TestClient(create_app())
        r = c.get("/any-spa-route")
        assert r.status_code == 200
        assert "<!doctype" in r.text.lower() or "<!DOCTYPE" in r.text

    def test_catch_all_hint_page_when_no_dist(self, tmp_env, monkeypatch):
        """dist 不存在时返回构建提示页。"""
        from pipeline.webui import app as app_module
        # 临时把 base 指向不存在的 dist
        original_init = app_module.create_app
        real_dist = _dist_dir()

        def patched_create_app():
            app = original_init()
            # 把 catch-all route 临时替换——但 create_app 每次跑都一样
            return app

        # 用临时 monkeypatched 路径：直接把真实 dist 改名为不存在
        if real_dist.is_dir():
            backup = real_dist.with_suffix(".dist.bak")
            real_dist.rename(backup)
            try:
                c = TestClient(patched_create_app())
                r = c.get("/any-spa-route")
                assert r.status_code == 200
                assert "frontend not built" in r.text or "MediaForge" in r.text
            finally:
                backup.rename(real_dist)
        else:
            c = TestClient(patched_create_app())
            r = c.get("/any-spa-route")
            assert r.status_code == 200
            assert "frontend not built" in r.text

    def test_catch_all_does_not_swallow_api(self, tmp_env):
        from pipeline.webui.app import create_app
        c = TestClient(create_app())
        r = c.get("/api/v1/dashboard")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_catch_all_does_not_swallow_output(self, tmp_env, monkeypatch):
        from pipeline.webui.app import create_app
        monkeypatch.chdir(tmp_env)
        (tmp_env / "output").mkdir(exist_ok=True)
        (tmp_env / "output" / "test.txt").write_text("hi", encoding="utf-8")
        c = TestClient(create_app())
        r = c.get("/output/test.txt")
        assert r.status_code == 200
        assert r.text == "hi"

    def test_assets_mounted_when_dist_exists(self, tmp_env):
        from pipeline.webui.app import create_app
        if not _dist_dir().is_dir():
            pytest.skip("frontend/dist not built; run `cd frontend && npm run build`")
        c = TestClient(create_app())
        # dist/index.html 存在；找 dist 里任一 assets/* 文件
        assets = _dist_dir() / "assets"
        if not assets.is_dir():
            pytest.skip("dist/assets not present")
        any_asset = next(assets.iterdir(), None)
        if not any_asset:
            pytest.skip("no asset files in dist/assets")
        r = c.get(f"/assets/{any_asset.name}")
        assert r.status_code == 200

    def test_legacy_htmx_routes_still_work(self, tmp_env):
        from pipeline.webui.app import create_app
        c = TestClient(create_app())
        r = c.get("/topics")
        # 旧 htmx 路由应正常响应（不是 catch-all）
        assert r.status_code == 200
        assert "<!doctype" in r.text.lower()  # 模板渲染（Jinja 输出小写）
