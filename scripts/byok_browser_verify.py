"""BYOK desktop/mobile smoke: settings, project markdown, visuals, draft preflight.

Does not type API keys. Screenshots must not contain secret plaintext.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8788"
OUT = Path("docs/product-validation/byok-browser")
PROJECT = f"{BASE}/projects/prj_a63f79b2"


def _shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / name), full_page=True)


def _assert_no_overflow(page) -> None:
    overflow = page.evaluate(
        """() => {
          const root = document.scrollingElement;
          return root ? root.scrollWidth > root.clientWidth + 2 : false;
        }"""
    )
    if overflow:
        raise AssertionError("page has horizontal overflow")


def _open_settings(page) -> None:
    page.goto(f"{BASE}/settings", wait_until="networkidle")
    page.get_by_text("中转站连接（BYOK）").wait_for()
    page.get_by_text("微信公众号（main）").wait_for()


def _open_project_section(page, label: str) -> None:
    page.goto(PROJECT, wait_until="networkidle")
    page.get_by_role("button", name=re.compile(label)).click()


def run_viewport(browser, *, width: int, height: int, prefix: str) -> None:
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_default_timeout(20000)
    _open_settings(page)
    assert page.get_by_text("检查连接只读取模型列表").count()
    assert page.locator("input[type='password'], .ant-input-password").count() >= 1
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-settings.png")

    _open_project_section(page, "2 主稿")
    page.locator("button:visible", has_text="复制 Markdown").first.wait_for()
    page.locator("button:visible", has_text="下载 .md").first.wait_for()
    _shot(page, f"{prefix}-master-markdown.png")

    _open_project_section(page, "3 视觉")
    page.get_by_text("视觉计划", exact=True).wait_for()
    _shot(page, f"{prefix}-visuals.png")

    _open_project_section(page, "5 图文")
    page.locator("button:visible", has_text="复制 Markdown").first.wait_for()
    _shot(page, f"{prefix}-variants-markdown.png")

    _open_project_section(page, "6 审批导出")
    page.get_by_text("内容包审批", exact=True).wait_for()
    _shot(page, f"{prefix}-draft-preflight.png")
    page.close()


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            run_viewport(browser, width=1280, height=800, prefix="desktop")
            run_viewport(browser, width=390, height=844, prefix="mobile")
        finally:
            browser.close()
    print(f"screenshots written to {OUT}")


if __name__ == "__main__":
    main()
