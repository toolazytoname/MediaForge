# MediaForge — AI 自媒体矩阵全自动流水线

选题 → 创作（图文/视频）→ 质量门禁 → 人工审核 → 多平台定时发布 → 数据回流，全流程自动化。

## 项目定位

- **不是**再造一个发布工具：国内平台发布走 Playwright（自写 + XiaohongshuSkills 桥），海外走 X 官方 API / Postiz
- **是**一条自研的内容生产流水线（编排层）：选题引擎、创作调度、质量门禁、发布队列、数据反馈闭环
- 核心理念：**品质靠否决权** —— 系统敢于自动丢弃 70% 的产出，剩下的才值得署名

## 当前能力（截至 2026-07-06，M0~M6 主线全达成）

| 维度 | 状态 |
|------|------|
| 选题 | RSS / HackerNews / GitHub Trending / DailyHot，cheap LLM 评分 + 每日精选 |
| 创作 | canonical 长文 + 三平台派生（头条/小红书/X）+ 视频口播稿 + 1080×1440 图卡 |
| 门禁 | 批判→重写→评分三步（移植 TrendPublish 采纳协议），6 篇占位锚点 + 校准挂钩 |
| 人审 | REVIEW.md 降级 + Web 审核台（FastAPI + htmx） |
| 排期 | 黄金时段避整点 + 跨平台错峰 + 种子可复现 |
| 发布 | X 官方 API + 头条自写 Playwright + 小红书（XiaohongshuSkills 桥）+ 抖音（强制 AI 标识）|
| 视频 | MPT 默认（量产）+ Pixelle-Video 第二引擎（精品，mode=fixed 文案主权）|
| 控制台 | Dashboard + 选题池 + 审核台 + 周视图发布日历 + 设置 |
| 数据回流 | X/头条/抖音公开指标 + 周报（Pearson r 校准门禁）|

> 详细里程碑记录与 commit 锚点见 [docs/MILESTONES.md](./docs/MILESTONES.md)

## 文档导航

| 文档 | 内容 | 读者 |
|------|------|------|
| [CLAUDE.md](./CLAUDE.md) | 会话恢复指引 + 工作约定 | 每次开工的 AI 必读 |
| [docs/PRD.md](./docs/PRD.md) | 产品需求：目标、赛道、平台、变现 | 决策时看 |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | 系统架构：模块、数据流、状态机 | 写代码前必读 |
| [docs/TECH_SPEC.md](./docs/TECH_SPEC.md) | 技术规格：数据模型、接口契约、目录约定 | 实现每个模块时对照 |
| [docs/TASKS.md](./docs/TASKS.md) | 分里程碑任务清单（含验收标准） | 逐条执行 |
| [docs/MILESTONES.md](./docs/MILESTONES.md) | 已达成的里程碑（commit 锚点 + 真实冒烟记录）| 运营期参考 |
| [docs/HARD_PARTS.md](./docs/HARD_PARTS.md) | 难点攻坚 + 实施注意事项 | 卡住时先查这里 |
| [docs/research/opensource-survey.md](./docs/research/opensource-survey.md) | 开源方案调研结论 | 选型溯源 |

## 快速开始

```bash
# 0. 准备 venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # 填入你的配置

# 1. 初始化数据库 + 拉取今日选题候选
python -m pipeline.run init-db
python -m pipeline.run ingest
python -m pipeline.run score

# 2. 创作 → 门禁 → 审核（任选其一即可完成人工节点）
python -m pipeline.run create
python -m pipeline.run gate
python -m pipeline.run review                  # 生成 REVIEW.md
# 或
python -m pipeline.run webui                   # 启动 http://127.0.0.1:8787 Web 审核台

# 3. 排期 + 发布（默认 publish.enabled=false，仅 dry-run）
python -m pipeline.run schedule
python -m pipeline.run publish --dry-run
```

> 真实账号发布需要 `publish.enabled: true` + 平台白名单 + 登录态扫码（`pipeline.run login <platform> <account>`）

## 流水线一览

```
ingest → score → create → gate → review → schedule → publish → collect
(选题采集) (评分)  (创作)  (门禁)  (人审)   (排期)    (发布)    (数据回流)
                                            ↓
                                  python -m pipeline.run report weekly
                                            ↓
                                  output/weekly-report.md
```

每个阶段是独立的 CLI 子命令，由 cron / launchd / 手动驱动，阶段间通过 SQLite 状态机衔接，任何阶段崩溃可安全重跑（幂等）。
