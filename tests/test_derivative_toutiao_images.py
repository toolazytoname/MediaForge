"""头条正文真实插图同步测试（镜像 tests/test_derivative_wechat_mp.py 的插图拼接部分）。

覆盖 `pipeline/creators/derivative_toutiao_images.py::sync_toutiao_images`：
  - toutiao.md 不存在 → False
  - canonical_md 无真实插图引用 → False
  - toutiao.md 无 [IMAGE: 占位符 → False
  - 正常按位置替换（用不同 caption 证明位置匹配而非文字匹配，且保留 toutiao.md 自己的 caption）
  - 占位符多于图片 → 多余占位符整段消失（不留裸文字）
  - 图片多于占位符 → 多余图片追加到文末
  - 二次调用幂等 → False，内容不变
  - 原子写入（无残留 .tmp）
"""
from __future__ import annotations

from pathlib import Path

from pipeline.creators.derivative_toutiao_images import sync_toutiao_images


def _canonical_with_inline_images() -> str:
    return (
        "# T\n\n## 第一部分\n\n正文……\n\n"
        "![canonical 侧 caption 一](images/inline-1.png)\n\n"
        "## 第二部分\n\n正文……\n\n"
        "![canonical 侧 caption 二](images/inline-2.png)\n"
    )


def _write_toutiao_md(content_dir: Path, text: str) -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    (content_dir / "toutiao.md").write_text(text, encoding="utf-8")


def test_no_toutiao_md_returns_false(tmp_path) -> None:
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    assert sync_toutiao_images(content_dir, _canonical_with_inline_images()) is False


def test_no_canonical_images_returns_false(tmp_path) -> None:
    content_dir = tmp_path / "content"
    _write_toutiao_md(content_dir, "正文……\n\n[IMAGE: 占位一]\n\n正文……")

    changed = sync_toutiao_images(content_dir, "# canonical 里没有任何真实插图引用")
    assert changed is False
    assert (content_dir / "toutiao.md").read_text(encoding="utf-8") == (
        "正文……\n\n[IMAGE: 占位一]\n\n正文……"
    )


def test_no_markers_in_toutiao_md_returns_false(tmp_path) -> None:
    content_dir = tmp_path / "content"
    body = "正文……没有任何占位符……"
    _write_toutiao_md(content_dir, body)

    changed = sync_toutiao_images(content_dir, _canonical_with_inline_images())
    assert changed is False
    assert (content_dir / "toutiao.md").read_text(encoding="utf-8") == body


def test_replaces_markers_by_position_keeping_own_caption(tmp_path) -> None:
    """toutiao.md 与 canonical.md 的 caption 来自两次独立 LLM 调用，互不相同——
    必须证明是按序号位置匹配，而非按文字匹配；且保留 toutiao.md 自己的 caption。"""
    content_dir = tmp_path / "content"
    body = (
        "开头……\n\n"
        "[IMAGE: 头条侧 caption 甲]\n\n"
        "中间正文……\n\n"
        "[IMAGE: 头条侧 caption 乙]\n\n"
        "结尾……"
    )
    _write_toutiao_md(content_dir, body)

    changed = sync_toutiao_images(content_dir, _canonical_with_inline_images())
    assert changed is True

    out = (content_dir / "toutiao.md").read_text(encoding="utf-8")
    assert "![头条侧 caption 甲](images/inline-1.png)" in out
    assert "![头条侧 caption 乙](images/inline-2.png)" in out
    # canonical 侧 caption 不应该泄漏进 toutiao.md
    assert "canonical 侧" not in out
    assert "[IMAGE:" not in out


def test_extra_markers_removed_when_fewer_images(tmp_path) -> None:
    content_dir = tmp_path / "content"
    body = (
        "开头……\n\n"
        "[IMAGE: 甲]\n\n"
        "中间……\n\n"
        "[IMAGE: 乙]\n\n"
        "结尾……\n\n"
        "[IMAGE: 丙（无图可配）]\n\n"
        "尾声……"
    )
    _write_toutiao_md(content_dir, body)

    canonical_md = (
        "## 第一部分\n\n正文……\n\n![c1](images/inline-1.png)\n\n"
        "## 第二部分\n\n正文……\n\n![c2](images/inline-2.png)\n"
    )
    changed = sync_toutiao_images(content_dir, canonical_md)
    assert changed is True

    out = (content_dir / "toutiao.md").read_text(encoding="utf-8")
    assert "[IMAGE:" not in out
    assert "丙" not in out
    assert "![甲](images/inline-1.png)" in out
    assert "![乙](images/inline-2.png)" in out
    assert "尾声……" in out


def test_extra_images_appended_at_end_when_more_images(tmp_path) -> None:
    content_dir = tmp_path / "content"
    body = "开头……\n\n[IMAGE: 唯一占位]\n\n结尾……"
    _write_toutiao_md(content_dir, body)

    canonical_md = (
        "## 第一部分\n\n正文……\n\n![c1](images/inline-1.png)\n\n"
        "## 第二部分\n\n正文……\n\n![c2](images/inline-2.png)\n\n"
        "## 第三部分\n\n正文……\n\n![c3](images/inline-3.png)\n"
    )
    changed = sync_toutiao_images(content_dir, canonical_md)
    assert changed is True

    out = (content_dir / "toutiao.md").read_text(encoding="utf-8")
    assert "![唯一占位](images/inline-1.png)" in out
    # 多出的两张图追加在文末，用 canonical 侧自己的 caption
    assert out.rstrip().endswith("![c3](images/inline-3.png)")
    assert "![c2](images/inline-2.png)" in out


def test_idempotent_second_call_returns_false_and_unchanged(tmp_path) -> None:
    content_dir = tmp_path / "content"
    body = "开头……\n\n[IMAGE: 甲]\n\n结尾……"
    _write_toutiao_md(content_dir, body)

    first = sync_toutiao_images(content_dir, _canonical_with_inline_images())
    assert first is True
    after_first = (content_dir / "toutiao.md").read_text(encoding="utf-8")

    second = sync_toutiao_images(content_dir, _canonical_with_inline_images())
    assert second is False
    after_second = (content_dir / "toutiao.md").read_text(encoding="utf-8")
    assert after_second == after_first


def test_atomic_write_leaves_no_tmp_residue(tmp_path) -> None:
    content_dir = tmp_path / "content"
    body = "开头……\n\n[IMAGE: 甲]\n\n结尾……"
    _write_toutiao_md(content_dir, body)

    sync_toutiao_images(content_dir, _canonical_with_inline_images())
    assert not (content_dir / "toutiao.md.tmp").exists()
