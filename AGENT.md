# MediaForge Agent Handoff

`AGENT.md` 是给不自动读取 `AGENTS.md` 的编码代理的兼容入口。完整工作约束以 [AGENTS.md](AGENTS.md) 为准；Claude 适配以 [CLAUDE.md](CLAUDE.md) 为准。

开始前必须依次阅读：

1. [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)
2. [docs/TASKS.md](docs/TASKS.md)
3. [docs/TECH_SPEC.md](docs/TECH_SPEC.md)
4. [docs/HARD_PARTS.md](docs/HARD_PARTS.md)

当前状态：本轮唯一任务入口是 [docs/CREATOR_OPERATIONS_TASKS.md](docs/CREATOR_OPERATIONS_TASKS.md)。用户已批准易用性、账号档案、多账号、微信/头条交付与按账号频率的运营开发。R1–R9 已完成；R0 未勾选。公众号草稿已送入 `prj_a63f79b2` 且正文已有配图，**未公开发布**。部署不得自动开启现有账号运营。读取顺序在四份产品文档之后加上 `CREATOR_OPERATIONS_TASKS.md`。

共享工作区已有用户未提交改动。没有用户明确授权，禁止执行 `git reset --hard`、`git clean -fd`、删除、覆盖或清理它们。长程工作请使用专用 git worktree，并且每个可验收任务独立测试、更新 `TASKS.md`、独立 commit。
