"""头条正文真实插图同步（generate-images 阶段之后调用）。

toutiao.md 是头条真实发布读取的文件，创作阶段只写入 `[IMAGE: caption]` 占位符
（真实插图此时还未生成）。本模块把 generate-images 阶段已生成、已写入
canonical_md 的真实 `![caption](images/inline-N.png)` 引用，按序号位置同步进
toutiao.md，替换掉占位符——逻辑镜像 `derivative_wechat_mp.py` 的
`insert_generated_images`/`splice_inline_images`/`_write_atomic` 幂等模式。

toutiao.md 与 canonical.md 的 `[IMAGE: ...]` 占位符文字来自两次独立 LLM 派生
调用（不同 prompt），caption 不会逐字对应——只能按序号位置匹配，不能按文字匹配。
保留 toutiao.md 自己的 caption 文字，只把占位符替换成真实文件引用。
"""
from __future__ import annotations

import re
from pathlib import Path

_TOUTIAO_MARKER_RE = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")
_CANONICAL_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(images/inline-(\d+)\.png\)")
_SPLICED_MARKER = "](images/inline-"  # toutiao.md 与 images/ 同级，无 "../" 前缀


def _write_atomic(path: Path, content: str) -> None:
    """原子写入：tmp → rename（HARD_PARTS §5 幂等，与 derivative.py 同名函数一致）。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / (path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def sync_toutiao_images(content_dir: Path, canonical_md: str) -> bool:
    """把 toutiao.md 里的 `[IMAGE: caption]` 按位置顺序替换为已生成的真实插图。

    返回 True 表示做了替换；以下情况返回 False（不算错误）：
      - content_dir/toutiao.md 不存在（该内容没有派生过 toutiao 版本）
      - canonical_md 里没有任何真实插图引用
      - toutiao.md 里没有任何 `[IMAGE:` 占位符
      - toutiao.md 已经同步过（幂等，见 HARD_PARTS §5）

    多出的占位符（图片数 < 占位符数）整段删除，不留裸文字；多出的图片
    （图片数 > 占位符数）依次追加到正文末尾，用 canonical 侧自己的 caption。
    """
    toutiao_path = content_dir / "toutiao.md"
    if not toutiao_path.exists():
        return False

    body = toutiao_path.read_text(encoding="utf-8")
    if _SPLICED_MARKER in body:
        return False

    images = sorted(
        ((int(n), caption) for caption, n in _CANONICAL_INLINE_IMAGE_RE.findall(canonical_md)),
        key=lambda pair: pair[0],
    )
    if not images:
        return False

    markers = list(_TOUTIAO_MARKER_RE.finditer(body))
    if not markers:
        return False

    n_slots = min(len(markers), len(images))

    replacements: list[tuple[int, int, str]] = []
    for marker, (n, _canonical_caption) in zip(markers[:n_slots], images[:n_slots]):
        own_caption = marker.group(1)
        replacements.append(
            (marker.start(), marker.end(), f"![{own_caption}](images/inline-{n}.png)")
        )
    # 多出的占位符（超出 n_slots）整段删除
    for marker in markers[n_slots:]:
        replacements.append((marker.start(), marker.end(), ""))

    result = body
    for start, end, text in sorted(replacements, key=lambda item: item[0], reverse=True):
        result = result[:start] + text + result[end:]

    leftover = images[n_slots:]
    if leftover:
        tail = "\n\n" + "\n\n".join(
            f"![{caption}](images/inline-{n}.png)" for n, caption in leftover
        )
        result = result.rstrip("\n") + tail

    _write_atomic(toutiao_path, result)
    return True


__all__ = ["sync_toutiao_images"]
