"""头条真实图片上传单测（Phase C，纯 MagicMock page，不需要真 Playwright）。

覆盖 `pipeline/publishers/toutiao_image_upload.py`：
  - _find_upload_input 用 state="attached" 而非 "visible"（file input 视觉隐藏）
  - 找不到 input → 优雅降级（不抛异常）
  - set_input_files 抛异常 → 优雅降级，返回 error 状态
  - 正常成功路径
  - 无图片可传 → skipped 状态
  - dump_debug_html 任何失败都不上抛
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from pipeline.publishers.toutiao_image_upload import (
    _find_upload_input,
    attempt_cover_upload,
    attempt_inline_image_upload,
    dump_debug_html,
)


SELECTORS = ("input[type='file'][accept*='image']", ".upload-image input[type='file']")


# ── _find_upload_input ──────────────────────────────────────


def test_find_upload_input_returns_locator_when_selector_matches() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    page.locator.return_value.first = loc

    result = _find_upload_input(page, SELECTORS)
    assert result is loc


def test_find_upload_input_uses_attached_state_not_visible() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    page.locator.return_value.first = loc

    _find_upload_input(page, SELECTORS)

    first_call = page.wait_for_selector.call_args_list[0]
    assert first_call.kwargs.get("state") == "attached"


def test_find_upload_input_returns_none_when_all_selectors_fail() -> None:
    page = MagicMock()
    page.wait_for_selector.side_effect = Exception("timeout")

    result = _find_upload_input(page, SELECTORS)
    assert result is None
    assert page.wait_for_selector.call_count == len(SELECTORS)


def test_find_upload_input_tries_next_selector_when_locator_count_zero() -> None:
    page = MagicMock()
    empty_loc = MagicMock()
    empty_loc.count.return_value = 0
    found_loc = MagicMock()
    found_loc.count.return_value = 1

    page.locator.return_value.first = empty_loc
    # 第一个选择器命中但 count()==0，应该继续试第二个
    call_results = [empty_loc, found_loc]

    def fake_locator(css):
        m = MagicMock()
        m.first = call_results.pop(0)
        return m

    page.locator.side_effect = fake_locator

    result = _find_upload_input(page, SELECTORS)
    assert result is found_loc


# ── attempt_cover_upload ─────────────────────────────────────


def test_attempt_cover_upload_returns_false_when_no_cover_path() -> None:
    page = MagicMock()
    shot_fn = MagicMock()
    result = attempt_cover_upload(page, None, SELECTORS, shot_fn)
    assert result is False
    shot_fn.assert_not_called()


def test_attempt_cover_upload_returns_false_when_input_not_found() -> None:
    page = MagicMock()
    page.wait_for_selector.side_effect = Exception("timeout")
    shot_fn = MagicMock()

    result = attempt_cover_upload(page, Path("/tmp/cover.png"), SELECTORS, shot_fn)
    assert result is False
    shot_fn.assert_not_called()


def test_attempt_cover_upload_returns_true_on_success_and_calls_shot_fn() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    page.locator.return_value.first = loc
    shot_fn = MagicMock()

    result = attempt_cover_upload(page, Path("/tmp/cover.png"), SELECTORS, shot_fn)
    assert result is True
    loc.set_input_files.assert_called_once_with("/tmp/cover.png")
    shot_fn.assert_called_once()


def test_attempt_cover_upload_returns_false_when_set_input_files_raises() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    loc.set_input_files.side_effect = RuntimeError("boom")
    page.locator.return_value.first = loc
    shot_fn = MagicMock()

    result = attempt_cover_upload(page, Path("/tmp/cover.png"), SELECTORS, shot_fn)
    assert result is False
    shot_fn.assert_not_called()


# ── attempt_inline_image_upload ──────────────────────────────


def test_attempt_inline_image_upload_skipped_when_no_images(tmp_path: Path) -> None:
    page = MagicMock()
    shot_fn = MagicMock()
    result = attempt_inline_image_upload(page, (), SELECTORS, shot_fn, tmp_path)
    assert result == {"attempted": 0, "uploaded": 0, "status": "skipped"}
    shot_fn.assert_not_called()


def test_attempt_inline_image_upload_selector_not_found_status(tmp_path: Path) -> None:
    page = MagicMock()
    page.wait_for_selector.side_effect = Exception("timeout")
    page.content.return_value = "<html></html>"
    shot_fn = MagicMock()

    images = (Path("/tmp/inline-1.png"), Path("/tmp/inline-2.png"))
    result = attempt_inline_image_upload(page, images, SELECTORS, shot_fn, tmp_path)

    assert result == {"attempted": 2, "uploaded": 0, "status": "selector_not_found"}
    # 找不到 input 应该 dump 一份 DOM 快照方便离线排查
    assert (tmp_path / "inline_upload_input_not_found.html").exists()


def test_attempt_inline_image_upload_ok_status_on_success(tmp_path: Path) -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    page.locator.return_value.first = loc
    shot_fn = MagicMock()

    images = (Path("/tmp/inline-1.png"), Path("/tmp/inline-2.png"))
    result = attempt_inline_image_upload(page, images, SELECTORS, shot_fn, tmp_path)

    assert result == {"attempted": 2, "uploaded": 2, "status": "ok"}
    assert loc.set_input_files.call_args_list == [
        call("/tmp/inline-1.png"), call("/tmp/inline-2.png"),
    ]
    shot_fn.assert_called_once()


def test_attempt_inline_image_upload_error_status_when_set_input_files_raises_partway(
    tmp_path: Path,
) -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    loc.set_input_files.side_effect = [None, RuntimeError("boom")]
    page.locator.return_value.first = loc
    shot_fn = MagicMock()

    images = (Path("/tmp/inline-1.png"), Path("/tmp/inline-2.png"))
    result = attempt_inline_image_upload(page, images, SELECTORS, shot_fn, tmp_path)

    assert result["attempted"] == 2
    assert result["uploaded"] == 1
    assert result["status"].startswith("error:")
    shot_fn.assert_called_once()


# ── dump_debug_html ───────────────────────────────────────────


def test_dump_debug_html_writes_html_file(tmp_path: Path) -> None:
    page = MagicMock()
    page.content.return_value = "<html><body>test</body></html>"
    dump_debug_html(page, tmp_path, "some_tag")
    assert (tmp_path / "some_tag.html").read_text(encoding="utf-8") == (
        "<html><body>test</body></html>"
    )


def test_dump_debug_html_never_raises_on_page_content_failure(tmp_path: Path) -> None:
    page = MagicMock()
    page.content.side_effect = RuntimeError("page crashed")
    dump_debug_html(page, tmp_path, "some_tag")  # 不应抛异常
    assert not (tmp_path / "some_tag.html").exists()


def test_dump_debug_html_never_raises_when_dir_not_writable(monkeypatch) -> None:
    page = MagicMock()
    page.content.return_value = "<html></html>"

    def fake_mkdir(*a, **kw):
        raise OSError("no permission")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    dump_debug_html(page, Path("/no/such/dir"), "tag")  # 不应抛异常
