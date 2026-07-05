"""锚点样例加载（HARD_PARTS §3 要点 2）。

读 pipeline/gate/anchors/ 下 good/mid/bad 三档各 2 篇样例，组装成
供 scorer prompt 用的摘要文本与人工评分字典。

⚠️ 真实校准流程：用户在第一次 gate 真实冒烟前用真实长文替换占位样例，
详见 anchors/README.md。HARD_PARTS §3 验收：10 篇样文排序与 gate
评分的 Spearman 相关系数 > 0.6。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


ANCHORS_DIR: Path = Path(__file__).parent / "anchors"

# 三档 tier 各 2 篇（命名约定：<tier>_0N）
TIER_FILES: dict[str, list[str]] = {
    "good": ["good_01", "good_02"],
    "mid": ["mid_01", "mid_02"],
    "bad": ["bad_01", "bad_02"],
}


@dataclass(frozen=True)
class Anchor:
    """单篇锚点（短摘录 + 人工评分）。"""
    name: str            # 'good_01'
    tier: str            # 'good' | 'mid' | 'bad'
    excerpt: str         # markdown 摘录
    scores: dict         # {info, fun, view, total}
    verdict: str         # 人工评语
    label: str           # 来自 .json 的 label 字段


@dataclass(frozen=True)
class AnchorsBundle:
    """加载好的三档锚点集合，供 scorer prompt 用。"""
    anchors: tuple[Anchor, ...]

    def by_tier(self, tier: str) -> tuple[Anchor, ...]:
        return tuple(a for a in self.anchors if a.tier == tier)

    def good(self) -> tuple[Anchor, ...]:
        return self.by_tier("good")

    def mid(self) -> tuple[Anchor, ...]:
        return self.by_tier("mid")

    def bad(self) -> tuple[Anchor, ...]:
        return self.by_tier("bad")


def _load_one(name: str) -> Anchor:
    md_path = ANCHORS_DIR / f"{name}.md"
    json_path = ANCHORS_DIR / f"{name}.json"
    if not md_path.exists() or not json_path.exists():
        raise FileNotFoundError(
            f"anchor pair missing: {md_path.name} / {json_path.name} "
            f"(see anchors/README.md)"
        )
    md_text = md_path.read_text(encoding="utf-8")
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    tier = meta.get("tier", name.split("_")[0])
    return Anchor(
        name=name,
        tier=tier,
        excerpt=md_text,
        scores=meta["scores"],
        verdict=meta.get("verdict", ""),
        label=meta.get("label", name),
    )


def load_anchors(anchors_dir: Path | None = None) -> AnchorsBundle:
    """从 anchors 目录加载全部 6 篇锚点。"""
    base = anchors_dir or ANCHORS_DIR
    anchors: list[Anchor] = []
    for tier, names in TIER_FILES.items():
        for name in names:
            md_path = base / f"{name}.md"
            json_path = base / f"{name}.json"
            if not md_path.exists() or not json_path.exists():
                # 用户可自定义锚点目录——只装部分也可
                continue
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            anchors.append(
                Anchor(
                    name=name,
                    tier=meta.get("tier", tier),
                    excerpt=md_path.read_text(encoding="utf-8"),
                    scores=meta["scores"],
                    verdict=meta.get("verdict", ""),
                    label=meta.get("label", name),
                )
            )
    return AnchorsBundle(anchors=tuple(anchors))


def render_for_prompt(
    bundle: AnchorsBundle,
    *,
    excerpt_chars: int = 600,
) -> dict[str, str]:
    """为 scorer prompt 渲染三段锚点摘要。

    Args:
        bundle: 加载好的锚点集合
        excerpt_chars: 每篇截取的字符数（节省 prompt token）

    Returns:
        {'good': '...', 'mid': '...', 'bad': '...'} 三段格式化文本
    """
    parts: dict[str, list[str]] = {"good": [], "mid": [], "bad": []}
    for anchor in bundle.anchors:
        excerpt = anchor.excerpt
        if len(excerpt) > excerpt_chars:
            excerpt = excerpt[:excerpt_chars] + "..."
        scores = anchor.scores
        parts[anchor.tier].append(
            f"--- {anchor.label} ---\n"
            f"信息 {scores['info']}/可读 {scores['fun']}/观点 {scores['view']}"
            f" = 总 {scores['total']}\n"
            f"评语：{anchor.verdict}\n"
            f"\n{excerpt}"
        )
    return {k: "\n\n".join(v) for k, v in parts.items()}