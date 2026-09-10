"""QA-01 desktop/mobile walk: today, works, accounts, settings.

Does not type secrets. Does not click real publish.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8788"
OUT = Path("docs/product-validation/qa01-browser")


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


def run_viewport(browser, *, width: int, height: int, prefix: str) -> None:
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_default_timeout(20000)
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    page.get_by_role("heading", name="先处理真实待办，而不是后台状态。").wait_for()
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-today.png")

    if width >= 1100:
        page.locator("nav[aria-label='主导航']").get_by_role("button", name="作品").click()
        page.wait_for_url("**/projects**")
    else:
        page.goto(f"{BASE}/projects", wait_until="domcontentloaded")
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-works.png")

    page.goto(f"{BASE}/projects/prj_a63f79b2", wait_until="domcontentloaded")
    page.locator('[data-testid="article-center"]').wait_for()
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-project.png")

    if width >= 1100:
        page.locator("nav[aria-label='主导航']").get_by_role("button", name="账号").click()
        page.wait_for_url("**/accounts**")
    else:
        page.goto(f"{BASE}/accounts", wait_until="domcontentloaded")
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-accounts.png")

    page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
    page.locator("h2", has_text="设置").wait_for()
    _assert_no_overflow(page)
    _shot(page, f"{prefix}-settings.png")
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
