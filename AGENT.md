# MediaForge Agent Handoff

`AGENT.md` 是给不自动读取 `AGENTS.md` 的编码代理的兼容入口。完整工作约束以 [AGENTS.md](AGENTS.md) 为准；Claude 适配以 [CLAUDE.md](CLAUDE.md) 为准。

开始前必须依次阅读：

1. [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)
2. [docs/TASKS.md](docs/TASKS.md)
3. [docs/TECH_SPEC.md](docs/TECH_SPEC.md)
4. [docs/HARD_PARTS.md](docs/HARD_PARTS.md)

当前状态：R1–R9 已完成并提交，“主题项目 → 研究 → 主稿 → 视觉 → 微信/头条独立版本 → 内容包审批”的人工可控闭环已落地。R0 仍需用户提供真实主题、3–5 个来源、目标读者、发布目的和个人观点，完成 60 分钟真人验收。不得用演示数据代替 R0，也不要继续扩展旧后台流水线、视频、数字人、更多平台或无人值守发布；R10 的真实交付、schema 迁移、真实发布或破坏性动作必须停下确认。

交付前验证：完整 Python 测试、前端生产构建、Anthropic 源码导入护栏、浏览器黄金路径；`frontend/dist/` 由 Vite 生成，不手工维护旧 hash 文件。GPT Image 2 真实调用只从环境变量读取 `OPENAI_API_KEY`。

共享工作区已有用户未提交改动。没有用户明确授权，禁止执行 `git reset --hard`、`git clean -fd`、删除、覆盖或清理它们。长程工作请使用专用 git worktree，并且每个可验收任务独立测试、更新 `TASKS.md`、独立 commit。
