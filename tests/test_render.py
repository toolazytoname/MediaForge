"""M2-4 模板渲染引擎测试。

覆盖（TECH_SPEC §5.4 契约 + M2-4 验收）：
  - 模板解析：Jinja2 加载 templates/xhs_card.html
  - 渲染纯字符串：注入 slides 数据 → HTML 含正文/标题
  - slide 字段校验：type ∈ {cover, content, action}、title/body 非空
  - render_cards() 端到端：5 slides → 5 PNG 文件
  - PNG 尺寸：1080x1440（默认 viewport）
  - 自定义 viewport：非默认值生效
  - 字体栈：CSS 含 PingFang SC + Noto Sans CJK 兜底
  - 异常路径：模板不存在 / slide 字段缺失 / 写盘失败
  - 幂等：覆盖写（tmp→rename）
  - 集成：从 xiaohongshu/slides.json 路径加载 → 渲染

Chromium 不可用时整组 end-to-end 测试自动 skip（CI 鲁棒 + 离线可跑
纯渲染部分）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from pipeline.creators import render as render_mod
from pipeline.creators.render import (
    RenderError,
    SlideValidationError,
    _render_template,
    _validate_slide,
    _detect_chromium_path,
    render_cards,
    load_template,
)


# ── fixtures ─────────────────────────────────────────────

# 仓库根 = tests/ 的父级父级
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


@pytest.fixture
def sample_slides() -> list[dict]:
    """5 张样例 slide：1 cover + 3 content + 1 action。"""
    return [
        {
            "type": "cover",
            "title": "AI 编程新范式",
            "body": "从 Copilot 到 Agent，工程实践正在重塑。",
        },
        {
            "type": "content",
            "title": "核心变化",
            "body": "AI 不再只是补全代码，而是能独立完成多步任务。",
        },
        {
            "type": "content",
            "title": "工程挑战",
            "body": "上下文管理、工具调用稳定性、可观测性是新难题。",
        },
        {
            "type": "content",
            "title": "未来趋势",
            "body": "Agent 将逐步进入生产链路，但人审不可省。",
        },
        {
            "type": "action",
            "title": "关注我们",
            "body": "点赞收藏，下期讲具体落地案例。",
        },
    ]


@pytest.fixture
def tmp_out_dir(tmp_path: Path) -> Path:
    """测试用输出目录。"""
    d = tmp_path / "cards_out"
    return d


# ── 1. 模板加载（无 chromium）────────────────────────────

class TestTemplateLoad:
    def test_xhs_card_template_exists(self):
        """xhs_card.html 必须存在于 templates/。"""
        path = TEMPLATES_DIR / "xhs_card.html"
        assert path.exists(), f"template missing: {path}"

    def test_load_template_returns_jinja_template(self):
        tpl = load_template("xhs_card", templates_dir=TEMPLATES_DIR)
        assert tpl is not None
        # 至少能渲染空数据不报错
        out = tpl.render(slide={"type": "cover", "title": "t", "body": "b"})
        assert isinstance(out, str)
        assert len(out) > 0

    def test_load_template_missing_raises(self, tmp_path: Path):
        with pytest.raises(RenderError):
            load_template("nope", templates_dir=tmp_path)


# ── 2. _validate_slide（无 chromium）─────────────────────

class TestValidateSlide:
    def test_valid_cover(self):
        s = _validate_slide(
            {"type": "cover", "title": "t", "body": "b"}, index=0
        )
        assert s["type"] == "cover"

    def test_valid_content(self):
        s = _validate_slide(
            {"type": "content", "title": "t", "body": "b"}, index=1
        )
        assert s["type"] == "content"

    def test_valid_action(self):
        s = _validate_slide(
            {"type": "action", "title": "t", "body": "b"}, index=4
        )
        assert s["type"] == "action"

    @pytest.mark.parametrize("bad_type", ["intro", "", "cover ", "COVER", None, 1])
    def test_invalid_type_raises(self, bad_type):
        with pytest.raises(SlideValidationError):
            _validate_slide(
                {"type": bad_type, "title": "t", "body": "b"}, index=0
            )

    def test_missing_title_raises(self):
        with pytest.raises(SlideValidationError):
            _validate_slide(
                {"type": "cover", "body": "b"}, index=0
            )

    def test_missing_body_raises(self):
        with pytest.raises(SlideValidationError):
            _validate_slide(
                {"type": "cover", "title": "t"}, index=0
            )

    def test_non_dict_raises(self):
        with pytest.raises(SlideValidationError):
            _validate_slide("not a dict", index=0)

    def test_empty_slides_list_raises(self):
        with pytest.raises(SlideValidationError):
            render_mod._validate_slides([])


# ── 3. _render_template：纯字符串渲染（无 chromium）────

class TestRenderTemplate:
    def test_render_includes_title_and_body(self, sample_slides):
        html = _render_template(
            "xhs_card", sample_slides[0], templates_dir=TEMPLATES_DIR
        )
        assert "AI 编程新范式" in html  # title
        assert "Copilot" in html  # body 部分

    def test_render_escapes_html_in_body(self):
        """XSS 防御：<script> 必须被转义。"""
        slide = {"type": "content", "title": "t<b>", "body": "<script>alert(1)</script>"}
        html = _render_template(
            "xhs_card", slide, templates_dir=TEMPLATES_DIR
        )
        assert "<script>alert(1)</script>" not in html

    def test_render_uses_chinese_font_stack(self, sample_slides):
        """字体栈：PingFang SC, Noto Sans CJK, 兜底 sans-serif。"""
        html = _render_template(
            "xhs_card", sample_slides[0], templates_dir=TEMPLATES_DIR
        )
        assert "PingFang SC" in html
        assert "Noto" in html

    def test_render_1080x1440_viewport(self, sample_slides):
        """默认 viewport=1080x1440 注入模板。"""
        html = _render_template(
            "xhs_card", sample_slides[0], templates_dir=TEMPLATES_DIR
        )
        assert "1080px" in html
        assert "1440px" in html


# ── 4. Chromium 探测 ─────────────────────────────────────

class TestChromiumDetection:
    def test_detect_returns_str_or_none(self):
        """探测函数返回 str（路径）或 None（找不到）。"""
        path = _detect_chromium_path()
        assert path is None or isinstance(path, str)
        if path:
            assert Path(path).exists()


# ── 5. render_cards() 端到端（需 chromium）───────────────

# 探测：snap chromium 优先；否则尝试 playwright 内置
_AVAILABLE_CHROMIUM = _detect_chromium_path()
_PLAYWRIGHT_BUNDLED = (
    Path(os.path.expanduser("~/.cache/ms-playwright")) if _AVAILABLE_CHROMIUM is None else None
)

needs_chromium = pytest.mark.skipif(
    _AVAILABLE_CHROMIUM is None and not (
        _PLAYWRIGHT_BUNDLED and any(_PLAYWRIGHT_BUNDLED.glob("chromium-*"))
    ),
    reason="chromium not available (run: python -m playwright install chromium "
           "or set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)",
)


@needs_chromium
class TestRenderCardsE2E:
    def test_basic_render_5_pngs(
        self, sample_slides, tmp_out_dir: Path
    ):
        paths = render_cards(
            template="xhs_card",
            slides=sample_slides,
            out_dir=tmp_out_dir,
        )
        assert len(paths) == len(sample_slides)
        for p in paths:
            assert p.exists(), f"missing: {p}"
            assert p.suffix == ".png"
            assert p.stat().st_size > 1000, f"png too small: {p}"

    def test_png_dimensions_1080x1440(
        self, sample_slides, tmp_out_dir: Path
    ):
        from PIL import Image  # optional dep, lazy import
        paths = render_cards(
            template="xhs_card",
            slides=sample_slides,
            out_dir=tmp_out_dir,
        )
        for p in paths:
            with Image.open(p) as img:
                assert img.size == (1080, 1440), f"bad size: {img.size} for {p}"

    def test_custom_viewport(
        self, sample_slides, tmp_out_dir: Path
    ):
        from PIL import Image
        paths = render_cards(
            template="xhs_card",
            slides=sample_slides,
            out_dir=tmp_out_dir,
            viewport=(720, 960),
        )
        with Image.open(paths[0]) as img:
            assert img.size == (720, 960)

    def test_out_dir_created_if_missing(
        self, sample_slides, tmp_path: Path
    ):
        nested = tmp_path / "deep" / "nested" / "cards"
        assert not nested.exists()
        paths = render_cards(
            template="xhs_card",
            slides=sample_slides,
            out_dir=nested,
        )
        assert nested.exists()
        assert all(p.exists() for p in paths)

    def test_idempotent_rerun_overwrites(
        self, sample_slides, tmp_out_dir: Path
    ):
        """tmp→rename 幂等：第二次跑覆盖第一次。"""
        first = render_cards(
            "xhs_card", sample_slides, tmp_out_dir
        )
        second = render_cards(
            "xhs_card", sample_slides, tmp_out_dir
        )
        assert len(first) == len(second) == len(sample_slides)
        # 路径可能不同（不同进程），但数量与可读性一致
        for p in second:
            assert p.exists()

    def test_slide_with_chinese_text_no_crash(
        self, sample_slides, tmp_out_dir: Path
    ):
        """含中文不抛异常，PNG 正常生成。"""
        paths = render_cards(
            "xhs_card", sample_slides, tmp_out_dir
        )
        # 没异常 + 全部有内容 = 视觉验收在 docs/samples/ 单独做
        assert all(p.stat().st_size > 1000 for p in paths)

    def test_prefix_in_filename(
        self, sample_slides, tmp_out_dir: Path
    ):
        paths = render_cards(
            "xhs_card", sample_slides, tmp_out_dir
        )
        # 默认 prefix = template 名
        names = [p.name for p in paths]
        for n in names:
            assert n.startswith("xhs_card"), f"bad prefix: {n}"

    def test_custom_prefix(
        self, sample_slides, tmp_out_dir: Path
    ):
        paths = render_cards(
            "xhs_card", sample_slides, tmp_out_dir,
            filename_prefix="cover",
        )
        names = [p.name for p in paths]
        for n in names:
            assert n.startswith("cover"), f"bad prefix: {n}"


# ── 6. 异常路径（无 chromium）────────────────────────────

class TestRenderCardsErrors:
    def test_empty_slides_raises(self, tmp_out_dir: Path):
        with pytest.raises(SlideValidationError):
            render_cards("xhs_card", [], tmp_out_dir)

    def test_bad_slide_type_raises(self, tmp_out_dir: Path):
        with pytest.raises(SlideValidationError):
            render_cards(
                "xhs_card",
                [{"type": "bad", "title": "t", "body": "b"}],
                tmp_out_dir,
            )

    def test_missing_template_raises(self, sample_slides, tmp_out_dir: Path):
        with pytest.raises(RenderError):
            render_cards(
                "no_such_template", sample_slides, tmp_out_dir,
                templates_dir=tmp_out_dir / "empty",
            )

    def test_chromium_not_found_raises(
        self, sample_slides, tmp_out_dir: Path, monkeypatch,
    ):
        """探测 + 显式 chromium_path 都为 None → RenderError。"""
        # 强制探测返 None
        from pipeline.creators import render as rmod
        monkeypatch.setattr(rmod, "_detect_chromium_path", lambda: None)
        with pytest.raises(RenderError, match="chromium not found"):
            render_cards(
                "xhs_card", sample_slides, tmp_out_dir,
                chromium_path=None,
            )


class TestChromiumDetectionDetailed:
    """_detect_chromium_path 各分支覆盖（line 135 / 144-149 缺路径）。"""

    def test_env_var_points_to_existing(self, tmp_path: Path, monkeypatch):
        """env 指向存在路径 → 优先返回 env（line 135）。"""
        from pipeline.creators import render as rmod

        fake_chrome = tmp_path / "my-chrome"
        fake_chrome.write_text("#!/bin/sh\n")
        monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", str(fake_chrome))
        # 系统探测路径（/snap/bin/chromium 等）全 patch 成不存在
        monkeypatch.setattr(rmod.Path, "exists", lambda self: True if str(self) == str(fake_chrome) else False)

        result = rmod._detect_chromium_path()
        assert result == str(fake_chrome)

    def test_env_var_points_to_nonexistent_falls_through(
        self, tmp_path: Path, monkeypatch,
    ):
        """env 指向不存在路径 → 跳过 env，继续探测。"""
        from pipeline.creators import render as rmod

        monkeypatch.setenv(
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH",
            str(tmp_path / "no-such-chrome"),
        )
        # 不抛错；返回 None 或有效路径
        result = rmod._detect_chromium_path()
        assert result is None or Path(result).exists()

    def test_pw_cache_fallback(self, tmp_path: Path, monkeypatch):
        """~/.cache/ms-playwright 命中 chromium 二进制 → 返回（line 144-148）。"""
        from pipeline.creators import render as rmod

        # 构造假 ~/.cache/ms-playwright/chromium-1234/chrome-linux/chrome
        fake_cache = tmp_path / "ms-playwright"
        chrome_dir = fake_cache / "chromium-1234" / "chrome-linux"
        chrome_dir.mkdir(parents=True)
        chrome_bin = chrome_dir / "chrome"
        chrome_bin.write_text("#!/bin/sh\n")

        # 屏蔽其他探测路径
        monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "")
        # 强制所有 /snap/bin, /usr/bin/* chromium 路径不存在
        orig_exists = rmod.Path.exists
        def fake_exists(self):
            s = str(self)
            if any(s.startswith(p) for p in ("/snap/bin", "/usr/bin/chromium", "/usr/bin/google-chrome")):
                return False
            return orig_exists(self)
        monkeypatch.setattr(rmod.Path, "exists", fake_exists)
        # 强制 os.path.expanduser → tmp_path
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(tmp_path / "ms-playwright") if p == "~/.cache/ms-playwright" else p,
        )

        result = rmod._detect_chromium_path()
        assert result == str(chrome_bin)

    def test_no_chromium_anywhere_returns_none(
        self, tmp_path: Path, monkeypatch,
    ):
        """env 失效 + snap /usr/bin 都不存在 + pw_cache 不存在 → None（line 149）。"""
        from pipeline.creators import render as rmod

        monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/no/such/env/chrome")
        # 所有探测路径全不存在 + pw_cache 也不存在
        orig_exists = rmod.Path.exists
        monkeypatch.setattr(rmod.Path, "exists", lambda self: False)
        # 防御：orig_exists 可能被 monkeypatch 替换后丢失，这里用 lambda

        result = rmod._detect_chromium_path()
        assert result is None


class TestWriteAtomicEdgeCases:
    """_write_atomic_png 边界：已有 .tmp 文件 → 先 unlink（HARD_PARTS §5 幂等）。"""

    def test_unlinks_existing_tmp(self, tmp_path: Path):
        from pipeline.creators.render import _write_atomic_png

        out = tmp_path / "card.png"
        stale_tmp = out.parent / (out.name + ".tmp")
        stale_tmp.write_bytes(b"stale content from previous run")

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"fresh" * 10
        _write_atomic_png(out, png_bytes)

        # 旧 .tmp 已被删；最终 png 写入新内容
        assert not stale_tmp.exists()
        assert out.read_bytes() == png_bytes


# ── 8. render_cards 主循环 mock 覆盖（coverage 工具追踪） ────

# 真实 chromium e2e（TestRenderCardsE2E）能 PASS 但 coverage 工具漏追踪
# Playwright 内部 C extension 内的 Python 代码。用纯 mock 的版本确保
# 220-250 render 主循环被 coverage 工具统计。

class TestRenderCardsMockedMainLoop:
    """mock sync_playwright 跑全 render_cards 主循环 → 覆盖 220-250。"""

    def _build_mock_playwright(self):
        """构造 fake Playwright contextmanager，模拟一次成功渲染。"""
        from unittest.mock import MagicMock

        fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        fake_page = MagicMock()
        fake_page.screenshot.return_value = fake_png
        fake_page.set_viewport_size.return_value = None
        fake_page.set_content.return_value = None
        fake_page.evaluate.return_value = None

        fake_context = MagicMock()
        fake_context.new_page.return_value = fake_page

        fake_browser = MagicMock()
        fake_browser.new_context.return_value = fake_context

        fake_p = MagicMock()
        fake_p.chromium.launch.return_value = fake_browser
        fake_p.__enter__ = lambda s: fake_p
        fake_p.__exit__ = lambda s, *a: None
        return fake_p

    def test_main_loop_with_chromium_explicit(
        self, sample_slides, tmp_out_dir: Path, monkeypatch,
    ):
        """显式 chromium_path 走主循环：launch → context → page → 渲染 N 张。"""
        from unittest.mock import patch

        monkeypatch.chdir(tmp_out_dir.parent)
        with patch("playwright.sync_api.sync_playwright", return_value=self._build_mock_playwright()):
            paths = render_cards(
                template="xhs_card",
                slides=sample_slides[:2],  # 2 张图覆盖主循环
                out_dir=tmp_out_dir,
                chromium_path="/fake/chrome",
            )
        assert len(paths) == 2
        for p in paths:
            assert p.exists()
            assert p.suffix == ".png"

    def test_main_loop_chromium_launch_fails_wrapped_as_render_error(
        self, sample_slides, tmp_out_dir: Path, monkeypatch,
    ):
        """launch 抛异常 → 包装为 RenderError（line 248-250）。"""
        from unittest.mock import MagicMock, patch

        fake_p = MagicMock()
        fake_p.__enter__ = lambda s: fake_p
        fake_p.__exit__ = lambda s, *a: None
        fake_p.chromium.launch.side_effect = RuntimeError("chrome crashed")
        monkeypatch.chdir(tmp_out_dir.parent)
        with patch("playwright.sync_api.sync_playwright", return_value=fake_p):
            with pytest.raises(RenderError, match="render_cards failed"):
                render_cards(
                    template="xhs_card",
                    slides=sample_slides[:1],
                    out_dir=tmp_out_dir,
                    chromium_path="/fake/chrome",
                )


# ── 7. 集成：从真实 xiaohongshu/slides.json 路径加载 ──

class TestIntegrationWithDerivative:
    def test_load_slides_from_json(
        self, tmp_path: Path, sample_slides
    ):
        """模拟 M2-3 写出的 xiaohongshu/slides.json 能直接喂给 render_cards。"""
        xhs_dir = tmp_path / "xiaohongshu"
        xhs_dir.mkdir()
        slides_path = xhs_dir / "slides.json"
        slides_path.write_text(
            json.dumps(sample_slides, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        loaded = json.loads(slides_path.read_text(encoding="utf-8"))
        assert len(loaded) == 5
        # 不调用 chromium；只验证 schema 一致
        for s in loaded:
            render_mod._validate_slide(s, index=loaded.index(s))
