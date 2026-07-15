"""头条正文/封面真实图片上传（Phase C，HARD_PARTS §2 决策 4 防腐层延伸）。

现状：`toutiao_selectors.py::IMAGE_FILE_INPUT` 是未经真实站点验证的猜测选择器。
头条富文本编辑器的插图真实交互方式（工具栏按钮触发？拖拽？粘贴？）目前未知，
不该靠反复真人试错正式站点去摸索（触发风控风险）。

策略：只做"直接对猜测选择器尝试 `set_input_files()`"这一种最低风险动作——
成功就成功，失败就优雅降级（记录、可选截图/DOM 转储，继续走完剩余发布流程，
不因图片上传失败导致整体发布失败）。

纯函数、`page` 依赖注入，不耦合 `PostBundle`/`ToutiaoPublisher`，方便单测用
`MagicMock` 而不需要真 Playwright。
"""
from __future__ import annotations

from pathlib import Path


def _find_upload_input(page, selectors: tuple[str, ...], timeout_ms: int = 4000):
    """逐个候选选择器等它挂载到 DOM。

    用 `state="attached"` 而非 `"visible"`——file input 几乎总是视觉隐藏
    （`display:none` 或被样式遮罩触发点击），要求可见会导致 100% 找不到。
    """
    for css in selectors:
        try:
            page.wait_for_selector(css, timeout=timeout_ms, state="attached")
        except Exception:
            continue
        try:
            loc = page.locator(css).first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def dump_debug_html(page, screenshot_dir: Path, tag: str) -> None:
    """最佳努力把 page.content() 存成 `<tag>.html`，供离线核对真实 DOM 结构。

    失败静默吞（截图/DOM 转储从不应该导致发布流程中断）。
    """
    try:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        html = page.content()
        (screenshot_dir / f"{tag}.html").write_text(html, encoding="utf-8")
    except Exception:
        pass


def attempt_cover_upload(page, cover_path: Path | None, selectors: tuple[str, ...], shot_fn) -> bool:
    """尝试真实封面上传。

    找不到 input 或无 cover_path 时返回 False——调用方应回退现有的
    "自动封面"单选按钮兜底逻辑，行为保持不变。任何 Playwright 异常只记录不上抛。
    """
    if not cover_path:
        return False
    loc = _find_upload_input(page, selectors)
    if loc is None:
        return False
    try:
        loc.set_input_files(str(cover_path))
    except Exception:
        return False
    shot_fn(page, "cover_uploaded")
    return True


def attempt_inline_image_upload(
    page,
    images: tuple[Path, ...],
    selectors: tuple[str, ...],
    shot_fn,
    screenshot_dir: Path,
) -> dict:
    """尽力而为上传正文插图；任何异常只记录不上抛。

    返回 {"attempted": n, "uploaded": n, "status": "skipped"|"ok"|
    "selector_not_found"|"error:<repr(Exc)>"}，写入 PublishResult.raw_response，
    供后续离线统计真实成功率。
    """
    if not images:
        return {"attempted": 0, "uploaded": 0, "status": "skipped"}

    loc = _find_upload_input(page, selectors)
    if loc is None:
        dump_debug_html(page, screenshot_dir, "inline_upload_input_not_found")
        return {"attempted": len(images), "uploaded": 0, "status": "selector_not_found"}

    uploaded = 0
    for img in images:
        try:
            loc.set_input_files(str(img))
        except Exception as e:
            shot_fn(page, f"inline_upload_error_{uploaded}")
            return {
                "attempted": len(images),
                "uploaded": uploaded,
                "status": f"error:{e!r}",
            }
        uploaded += 1

    shot_fn(page, "inline_uploaded")
    return {"attempted": len(images), "uploaded": uploaded, "status": "ok"}


__all__ = [
    "attempt_cover_upload",
    "attempt_inline_image_upload",
    "dump_debug_html",
]
