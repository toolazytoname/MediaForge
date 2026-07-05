"""M2-4 模板渲染引擎（TECH_SPEC §5.4）。

slides JSON → PNG 图卡，零外部服务（除本机 Chromium）。

公开 API：
  - render_cards(template, slides, out_dir, ...) → list[Path]
  - load_template(name, templates_dir) → jinja2.Template
  - RenderError / SlideValidationError

设计：
  - 模板放 templates/<name>.html（Jinja2），本仓库资产
  - 1080×1440 默认 viewport，覆盖小红书 3:4 推荐尺寸
  - 中文字体栈：PingFang SC（mac）/ Noto Sans/Serif CJK SC（Linux）
  - Chromium 探测顺序：环境变量 → snap → playwright bundled
  - 单张截图：HTML → page.set_content() → page.screenshot() → tmp→rename
  - 单条失败不阻断整批（IO 隔离）
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape


# ── 路径常量 ─────────────────────────────────────────────

# 仓库根 = pipeline/creators/render.py 的父级父级
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "templates"


# ── 错误 ────────────────────────────────────────────────

class RenderError(Exception):
    """模板渲染失败（模板缺失、IO 错误、chromium 启动失败等）。"""


class SlideValidationError(RenderError):
    """slide 字段缺失或类型非法。"""


# ── Slide 校验 ───────────────────────────────────────────

_VALID_TYPES = frozenset({"cover", "content", "action"})


def _validate_slide(slide: object, *, index: int) -> dict:
    """校验单张 slide，返回标准化 dict。失败抛 SlideValidationError。"""
    if not isinstance(slide, dict):
        raise SlideValidationError(
            f"slide[{index}] not a dict: {type(slide).__name__}"
        )
    t = slide.get("type")
    if t not in _VALID_TYPES:
        raise SlideValidationError(
            f"slide[{index}].type invalid: {t!r} (must be one of {sorted(_VALID_TYPES)})"
        )
    title = slide.get("title")
    body = slide.get("body")
    if not isinstance(title, str) or not title.strip():
        raise SlideValidationError(
            f"slide[{index}].title missing/empty: {title!r}"
        )
    if not isinstance(body, str) or not body.strip():
        raise SlideValidationError(
            f"slide[{index}].body missing/empty: {body!r}"
        )
    return {"type": t, "title": title, "body": body}


def _validate_slides(slides: Iterable[dict]) -> list[dict]:
    """校验全部 slides，返回标准化 list。空 list 也抛错。"""
    slides_list = list(slides)
    if not slides_list:
        raise SlideValidationError("slides list is empty")
    return [_validate_slide(s, index=i) for i, s in enumerate(slides_list)]


# ── 模板加载 ────────────────────────────────────────────

def _make_env(templates_dir: Path) -> Environment:
    """构造 Jinja2 env（autoescape 防止 XSS）。"""
    if not templates_dir.exists():
        raise RenderError(f"templates dir not found: {templates_dir}")
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )


def load_template(
    name: str, *, templates_dir: Path | None = None
) -> Template:
    """加载 templates/<name>.html（不含扩展名）。"""
    tdir = templates_dir or DEFAULT_TEMPLATES_DIR
    env = _make_env(tdir)
    try:
        return env.get_template(f"{name}.html")
    except Exception as e:
        raise RenderError(
            f"template load failed: {name!r} from {tdir}: {e}"
        ) from e


def _render_template(
    template_name: str,
    slide: dict,
    *,
    templates_dir: Path | None = None,
    viewport: tuple[int, int] = (1080, 1440),
) -> str:
    """单张 slide → HTML 字符串（不调 chromium）。"""
    tpl = load_template(template_name, templates_dir=templates_dir)
    return tpl.render(slide=slide, viewport=viewport)


# ── Chromium 探测 ──────────────────────────────────────

def _detect_chromium_path() -> str | None:
    """探测本机 Chromium 路径。优先级：

    1. 环境变量 PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    2. /snap/bin/chromium（Linux snap 安装）
    3. /usr/bin/chromium / /usr/bin/chromium-browser / /usr/bin/google-chrome
    4. ~/.cache/ms-playwright/chromium-*/chrome-linux/chrome
    """
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env and Path(env).exists():
        return env
    for cand in (
        "/snap/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if Path(cand).exists():
            return cand
    pw_cache = Path(os.path.expanduser("~/.cache/ms-playwright"))
    if pw_cache.exists():
        for sub in sorted(pw_cache.glob("chromium-*/chrome-linux/chrome")):
            if sub.exists():
                return str(sub)
    return None


# ── 写文件（tmp→rename）───────────────────────────────

def _write_atomic_png(path: Path, png_bytes: bytes) -> None:
    """PNG 原子写入：tmp → rename（HARD_PARTS §5 幂等）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_bytes(png_bytes)
    tmp.rename(path)


# ── 主入口：render_cards ───────────────────────────────

def render_cards(
    template: str,
    slides: list[dict],
    out_dir: Path,
    *,
    viewport: tuple[int, int] = (1080, 1440),
    filename_prefix: str | None = None,
    templates_dir: Path | None = None,
    chromium_path: str | None = None,
) -> list[Path]:
    """slides JSON → PNG 图卡列表。

    Args:
        template: 模板名（不含 .html），对应 templates/<template>.html
        slides: [{type, title, body}, ...]
        out_dir: 输出目录（不存在则创建）
        viewport: 截图视口，默认 (1080, 1440) = 小红书 3:4 推荐尺寸
        filename_prefix: 文件名前缀，默认 = template 名
        templates_dir: 模板目录，默认 REPO_ROOT/templates
        chromium_path: 显式指定 chromium 路径；None 走探测

    Returns:
        生成的 PNG 文件路径列表（按输入顺序）

    Raises:
        SlideValidationError: slides 字段错
        RenderError: 模板/IO/chromium 错误
    """
    # 1. 校验 + 标准化 slides
    valid_slides = _validate_slides(slides)

    # 2. 探测 chromium
    resolved_chromium = chromium_path or _detect_chromium_path()
    if resolved_chromium is None:
        raise RenderError(
            "chromium not found. Install one of: "
            "(a) `python -m playwright install chromium`, "
            "(b) snap install chromium, "
            "(c) set env PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"
        )

    # 3. 预加载模板（避免 chromium 启动后才发现模板错）
    tpl = load_template(template, templates_dir=templates_dir)

    # 4. 启动 chromium
    from playwright.sync_api import sync_playwright

    prefix = filename_prefix or template
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=resolved_chromium,
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": viewport[0], "height": viewport[1]},
                    device_scale_factor=1,
                )
                page = context.new_page()
                for i, slide in enumerate(valid_slides):
                    html = tpl.render(slide=slide, viewport=viewport)
                    # 注入 viewport meta 后再 set_content（防移动端缩放）
                    page.set_viewport_size({
                        "width": viewport[0], "height": viewport[1]
                    })
                    page.set_content(html, wait_until="load")
                    # 等字体加载（中文不渲染完会出豆腐）
                    page.evaluate("document.fonts.ready")
                    png_bytes = page.screenshot(
                        type="png", full_page=False, omit_background=False
                    )
                    out_path = out_dir / f"{prefix}-{i+1:03d}.png"
                    _write_atomic_png(out_path, png_bytes)
                    paths.append(out_path)
            finally:
                browser.close()
    except Exception as e:
        # 包装为 RenderError 方便上层捕获
        raise RenderError(f"render_cards failed: {e}") from e

    return paths
