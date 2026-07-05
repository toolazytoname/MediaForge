---
name: m2-5-review-stage
description: MediaForge M2-5 review 阶段实施心得——REVIEW.md 模板/解析/通知/编排的核心设计要点与踩过的坑
metadata:
  type: project
---

MediaForge M2-5（审核清单）关键设计点与坑（commit da805dc）：

## 核心契约
- REVIEW.md 在 `output/<date>/REVIEW.md`，与 `<date>/<content_id>/canonical.md` 同级
- 模板节格式：`## [c_xxx] 标题` + bullet metadata + `- [ ] approve` + `- [-] reject: <理由>`
- 决策规则：`[x]`→approved；`[-] reject: <非空理由>`→rejected_by_human；两者同时 reject 胜出（保守）
- 走 db.transition 走状态机乐观锁

## 关键防线（必须保留）

1. **模板占位 ≠ 决策**：`run_review` 顺序是"先读旧+落库 → 再写新"。若 reader 把刚生成的 `- [-] reject:`（空理由）当成真实决策，第二次跑会把刚生成的清单全部 reject 掉。**reject 必须有非空理由**才算决策。

2. **`_REJECT_RE` 的冒号必须 required**：`reject:?` 中 `?` 让冒号可选，会让 `(.+?)` 吃尾冒号变成 reason=':'。用 `reject:`（必须含冒号）。

3. **id 正则要含下划线**：内容 id 实际是 8 hex（`new_id` 生成），但生产里可能有人手编辑的"易读 id"如 `c_smoke_deriv1` 含多个 `_`。regex 用 `c_[0-9a-z_]+` 而不是 `c_[0-9a-f]+` 或 `c_[0-9a-z]+`。

## 模块分工
- `checklist.py` — 生成 REVIEW.md（tmp→rename 幂等，按分数降序，cover 图可选）
- `reader.py` — 解析+落库（ReviewDecision frozen dataclass + apply_decisions）
- `notify.py` — webhook（httpx.post，飞书/TG 通用 `{"text": ...}`，失败仅 warn）
- `__init__.py` — `run_review(conn, ...)` 编排：read_and_apply → write_checklist → notify

## runner/cmd 编排顺序
- `cmd_review` 必须先 `read_and_apply`（处理旧标记）再 `write_checklist`（生成新清单）
- notify 仅在 `generated > 0` 时触发（不骚扰人）

## 测试模式
- 测试用 `tmp_path` 作为 output_root；`_make_content` 自动建对应真实文件（reviewer 不强求但 checklist 写入要安全）
- 共享 topic 默认 `_make_content` 自动 `t_<id_without_c_>` + insert_topic，避免 UNIQUE(topic_id) 冲突
- 关键边界：空 REVIEW.md / 内容不在 gated / 内容不存在 / 已处理过（幂等）

**Why**: 这些都是从实战 bug 修出来的。M2-5 第一次跑会撞上所有这些。
**How to apply**: M3+ 任何涉及"模板生成 + 人工编辑回读 + 状态机落库"的阶段（如 M3-3 webui 审核台、M6-3 自动审），都应复用同样模式：模板占位 ≠ 决策；reason 非空才算决策；reader regex 包含下划线。