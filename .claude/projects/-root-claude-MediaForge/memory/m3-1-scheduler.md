---
name: m3-1-scheduler
description: MediaForge M3-1 scheduler 排期器实施心得——纯函数 plan() 的设计要点与踩坑
metadata:
  type: project
---

MediaForge M3-1（错峰排期）关键设计点与坑（commit 94f7798）：

## 核心契约
- 纯函数 `plan(approved_contents, platform_configs, existing_publications, now_iso, *, min_gap_hours, cross_platform_gap_minutes, tz_name) → PlanResult`
- 不写 DB；落库由 cmd_schedule 调 `db.insert_publication` + UNIQUE 防重
- 种子 = `int.from_bytes(sha256(content_id|platform)[:4], 'big')` → 可复现（HARD_PARTS §8）
- 存储 UTC ISO8601；展示本地（默认 Asia/Shanghai）

## 关键设计要点

1. **窗口采样 offset 进位**：`_sample_in_window` 取 `offset = rng.randint(0, duration-1)`，必须用绝对分钟 `(start_h*60+start_m + offset) // 60 % 60` 算 h/m，否则 start_m + offset ≥ 60 触发 `time() ValueError`。

2. **顺延策略**：每个窗口最多采样 20 次/日 → 14 天上限 → 找不到返回 None。`now_local` 用 UTC→本地换算后比较，避免过去时间被选中。

3. **同平台已有排期约束**：遍历时把新生成的 pub 也加进 `by_platform` dict，保证同批内的多条新排期也互不冲突。

4. **跨平台错开**：用 `last_for_content` 跟踪上一次同内容排期时间。

5. **平台无 windows → 跳过**（不抛错）：HARD_PARTS §2 容错。

## cmd_schedule 编排关键点
- 取 approved 内容 + 全部 status 的已有 publications（不只是 queued）
- UNIQUE(content_id, platform, account_id) 冲突 → 视为 `skipped` 而非 `failed`，exit 0（幂等成功）
- 真实失败（其他异常）才计入 `failed` + 决定 exit code

## 测试模式
- `_parse_window` / `_parse_iso_utc` / `_platform_windows` 等 helper 在 `__all__` 暴露给测试用
- 用 `_make_content` 自动建 topic（避免 UNIQUE(topic_id) 冲突）
- 固定种子 → 同输入同输出（test_same_input_same_output）

**Why**: M3-1 是发布前的关键过渡环节；错峰逻辑出错 = 高峰时段发帖被风控。
**How to apply**: 任何"模板化排期 + 规则约束 + 持久化"模块都应复用此模式——纯函数 + 种子可复现 + UNIQUE 兜底幂等 + 时区分层（UTC 存 / 本地算）。