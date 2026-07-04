# MediaForge — AI 自媒体矩阵全自动流水线

选题 → 创作（图文/视频）→ 质量门禁 → 人工审核 → 多平台定时发布 → 数据回流，全流程自动化。

## 项目定位

- **不是**再造一个发布工具：发布脏活交给开源组件（social-auto-upload / Postiz / AiToEarn）
- **是**一条自研的内容生产流水线（编排层）：选题引擎、创作调度、质量门禁、发布队列、数据反馈闭环
- 核心理念：**品质靠否决权** —— 系统敢于自动丢弃 70% 的产出，剩下的才值得署名

## 文档导航

| 文档 | 内容 | 读者 |
|------|------|------|
| [CLAUDE.md](./CLAUDE.md) | 会话恢复指引 + 工作约定 | 每次开工的 AI 必读 |
| [docs/PRD.md](./docs/PRD.md) | 产品需求：目标、赛道、平台、变现 | 决策时看 |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | 系统架构：模块、数据流、状态机 | 写代码前必读 |
| [docs/TECH_SPEC.md](./docs/TECH_SPEC.md) | 技术规格：数据模型、接口契约、目录约定 | 实现每个模块时对照 |
| [docs/TASKS.md](./docs/TASKS.md) | 分里程碑任务清单（含验收标准） | 逐条执行 |
| [docs/HARD_PARTS.md](./docs/HARD_PARTS.md) | 难点攻坚 + 实施注意事项 | 卡住时先查这里 |
| [docs/research/opensource-survey.md](./docs/research/opensource-survey.md) | 开源方案调研结论 | 选型溯源 |

## 快速开始（开发环境）

```bash
cd /Users/lazy/Code/crack/self0704
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # 填入你的配置
python -m pipeline.run init-db       # 初始化 SQLite
python -m pipeline.run ingest        # 拉取今日选题候选
```

## 流水线一览

```
ingest → score → create → gate → review → schedule → publish → collect
(选题采集) (评分)  (创作)  (门禁)  (人审)   (排期)    (发布)    (数据回流)
```

每个阶段是独立的 CLI 子命令，由 cron / launchd 定时驱动，阶段间通过 SQLite 状态机衔接，任何阶段崩溃可安全重跑（幂等）。
