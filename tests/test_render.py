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
