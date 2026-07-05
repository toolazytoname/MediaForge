# 锚点样例（gate/anchors/）

> ⚠️ **占位 - 需用户校准**（M2-2 完成于 2026-07-05）
>
> 这是质量门禁评分 prompt 用的 6 个校准锚点（HARD_PARTS §3 要点 2）。
> 当前内容是 MediaForge 团队自写的占位样例，用于跑通端到端流水线。
> **请用户在第一次 gate 真实冒烟前用真实内容校准**——人审对一组 10 篇样文的
> 排序与 gate 评分的 Spearman 相关系数应 > 0.6（HARD_PARTS §3 验收标准）。

## 文件格式

每对文件：

- `<tier>_<n>.md` — 短篇 canonical 摘录（800-1500 字，模拟真实长文头部+中段+结论）
- `<tier>_<n>.json` — 人工标注评分

```
anchors/
├── README.md
├── good_01.md  good_01.json    (目标 9 分级：信息 9/有趣 8/观点 9)
├── good_02.md  good_02.json    (目标 9 分级：信息 9/有趣 9/观点 9)
├── mid_01.md   mid_01.json     (目标 6 分级：信息 7/有趣 6/观点 6)
├── mid_02.md   mid_02.json     (目标 6 分级：信息 8/有趣 5/观点 5)
├── bad_01.md   bad_01.json     (目标 3 分级：信息 4/有趣 4/观点 3)
└── bad_02.md   bad_02.json     (目标 3 分级：信息 4/有趣 2/观点 3)
```

## 评分口径

三维度（与 TECH_SPEC §3 contents.gate_scores 一致）：

- `info`（信息量 0-10）：事实密度、可验证性、数据/案例支撑
- `fun`（可读性 0-10）：表达流畅、结构清晰、无 AI 套话
- `view`（观点 0-10）：有立场、有判断、不骑墙、给行动建议

`total = info + fun + view`。

## 校准流程

1. 准备 10 篇质量参差的真实或半真实长文
2. 人工排序（最好→最差）
3. 用 anchor prompts 跑门禁打分
4. 计算人工排序 vs gate 评分的 Spearman 等级相关系数
5. 系数 < 0.6 → 调整 anchor 内容 / 评分 prompt，重新跑
6. 系数 ≥ 0.6 → anchor 定稿，本目录从"占位"升格为"已校准"