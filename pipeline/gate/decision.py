"""门禁判定逻辑（TrendPublish 修订采纳规则移植 + MediaForge 3 维评分）。

移植来源：TrendPublish `src/features/weixin-article/workflow.ts`
函数 `shouldAcceptArticleRevision(before, after)`（实际行号 ≈ 1112-1126
附近；evaluation-notes.md §1 复核记录：真实路径含 quality-review domain）。

四层防御（按优先级）：
  1. **新增 blocker 防御**：after 引入新的 blocker/fact/risk 严重问题，before 没有 → 拒绝
  2. **allow_publish 降级防御**：before 已通过门禁，after 反而不通过 → 拒绝
  3. **action 等级提升即采纳**（哪怕分数降）：after.action_rank > before.action_rank → 采纳
  4. **action 持平 → 比分数**：after.total >= before.total 才采纳

分类口径：
  - 不可重写（直接 discarded 或留人审）：fact / risk（severity=blocker）
  - 可重写：title / structure / tone / html（severity ≤ high）+ autoFixable=true 的特例

MediaForge 3 维评分（TECH_SPEC §3 contents.gate_scores）：
  - info / fun / view 各 0-10
  - total = sum
  - threshold_total（默认 24）+ threshold_each（默认 6）双重判定 gated
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


# ── Action 等级（对应 TrendPublish qualityActionRank）───────────

class Action(IntEnum):
    """修订采纳规则的 action 等级。

    移植 TrendPublish qualityActionRank(block=0/revise=1/dry-run-only=2/publish=3)，
    映射到 MediaForge 3 维评分语义：
      - BLOCK = 0（丢弃：内容有不可修复的事实/风险问题）
      - DISCARD = 1（丢弃：总分过低，无重写价值）
      - REVISE = 2（保留：可重写但本轮没改成功）
      - GATE = 3（采纳：通过门禁）
    """
    BLOCK = 0
    DISCARD = 1
    REVISE = 2
    GATE = 3


# ── 问题分类 ───────────────────────────────────────────────

# 不可重写的类别（HARD_PARTS §3：事实/风险类问题重写无意义）
UNFIXABLE_CATEGORIES: frozenset[str] = frozenset({"fact", "risk"})

# 可重写的类别（TrendPublish isSafeRevisionCandidate 默认放行）
REVISABLE_CATEGORIES: frozenset[str] = frozenset(
    {"title", "structure", "tone", "html", "image"}
)


@dataclass(frozen=True)
class Problem:
    """单条审稿问题（critic 与 scorer 共用）。"""
    category: str          # fact|title|structure|tone|html|image|risk
    severity: str          # low|medium|high|blocker
    message: str
    evidence: str = ""
    suggestion: str = ""
    auto_fixable: bool = False


def has_blocker(problems: tuple[Problem, ...]) -> bool:
    """是否有 blocker 级问题（fact/risk 默认 blocker；或显式 severity=blocker）。

    对应 TrendPublish hasBlockerIssue()。
    """
    for p in problems:
        if p.severity == "blocker":
            return True
        # fact / risk 类别默认 blocker（即便 LLM 没标）
        if p.category in UNFIXABLE_CATEGORIES and p.severity == "high":
            return True
    return False


def safe_to_revise(problems: tuple[Problem, ...]) -> bool:
    """这些问题是否可安全触发重写。

    对应 TrendPublish isSafeRevisionCandidate() 集合规则：
      - 没有 blocker → 可重写
      - 有 autoFixable=true 的问题 → 可重写（白名单旁路）
      - title/structure/tone/html/image 类问题 → 可重写
      - fact/risk 类（blocker）→ 不可重写，必须丢弃
    """
    if has_blocker(problems):
        return False
    if not problems:
        return True  # 无问题无须重写
    for p in problems:
        if p.auto_fixable:
            return True
        if p.category in REVISABLE_CATEGORIES:
            return True
    # 剩余都是 fact/risk 类的中低严重度 → 保守起见仍允许重写
    return True


# ── 修订采纳规则（核心）───────────────────────────────────

def should_accept_revision(
    before_score: int,
    after_score: int,
    before_problems: tuple[Problem, ...],
    after_problems: tuple[Problem, ...],
    before_allow_publish: bool,
    after_allow_publish: bool,
) -> bool:
    """移植 TrendPublish shouldAcceptArticleRevision 语义到 3 维评分。

    规则（按优先级短路）：
      1. 新增 blocker → False
      2. allow_publish 降级（true→false）→ False
      3. action 等级提升（按判定）→ True
      4. action 等级持平 → after_score >= before_score 才 True
      5. action 等级下降 → False

    注：本函数是低层纯逻辑。action 等级由调用方根据 before/after 的
    score+problems 推算（见 decide_action / decide_acceptance）。
    """
    # 1. 新增 blocker 防御
    if has_blocker(after_problems) and not has_blocker(before_problems):
        return False

    # 2. allow_publish 降级防御
    if before_allow_publish and not after_allow_publish:
        return False

    # 3-5. action 等级比较 + 分数兜底
    before_action = _action_from(
        before_score, before_problems, before_allow_publish
    )
    after_action = _action_from(
        after_score, after_problems, after_allow_publish
    )
    if after_action > before_action:
        return True
    if after_action < before_action:
        return False
    return after_score >= before_score


def _action_from(
    score: int,
    problems: tuple[Problem, ...],
    allow_publish: bool,
) -> Action:
    """从分数+问题+allow_publish 推算 action 等级。

    - 有 blocker → BLOCK（即便分数高也不采纳）
    - allow_publish=False 且分数不达基本线 → REVISE/DISCARD
    - allow_publish=True → GATE
    """
    if has_blocker(problems):
        return Action.BLOCK
    if allow_publish:
        return Action.GATE
    # 不通过：再分一档
    if score <= 12:  # 3 维 × 4 分以下，基本没有重写价值
        return Action.DISCARD
    return Action.REVISE


# ── 高层判定函数（外部使用）────────────────────────────────

@dataclass(frozen=True)
class GateDecision:
    """门禁对单条内容的最终判定。"""
    action: Action            # BLOCK/DISCARD/REVISE/GATE
    allow_publish: bool       # True = 通过门禁
    score: int                # total
    scores: dict              # {info, fun, view}
    verdict: str
    problems: tuple[Problem, ...]


def decide_gate(
    *,
    info: int,
    fun: int,
    view: int,
    problems: tuple[Problem, ...],
    threshold_total: int,
    threshold_each: int,
    verdict: str = "",
) -> GateDecision:
    """根据 3 维分数 + 阈值 + 问题列表判定 gated vs discarded。

    Args:
        info/fun/view: 各维度分
        problems: critic/scorer 返回的问题列表
        threshold_total: 总分门槛（默认 24）
        threshold_each: 单维度门槛（默认 6）
        verdict: scorer 给的一句话评语

    Returns:
        GateDecision
    """
    total = info + fun + view
    each_ok = info >= threshold_each and fun >= threshold_each and view >= threshold_each
    total_ok = total >= threshold_total
    allow = each_ok and total_ok and not has_blocker(problems)
    action = _action_from(total, problems, allow)
    return GateDecision(
        action=action,
        allow_publish=allow,
        score=total,
        scores={"info": info, "fun": fun, "view": view},
        verdict=verdict,
        problems=problems,
    )


def decide_revision_action(
    *,
    has_rewrite_issues: bool,
    problems: tuple[Problem, ...],
) -> Action:
    """决定是否触发重写（基于 critic 第一轮结果）。

    - has_blocker → BLOCK（直接 discarded，不重写）
    - 无问题 → GATE（去 scorer 评分确认）
    - 有可重写问题 → REVISE（触发 rewrite）
    - 仅不可重写类问题（且非 blocker）→ DISCARD
    """
    if has_blocker(problems):
        return Action.BLOCK
    if not problems:
        return Action.GATE  # 无问题直接进 scorer
    if safe_to_revise(problems):
        return Action.REVISE if has_rewrite_issues else Action.GATE
    return Action.DISCARD