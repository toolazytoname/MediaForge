# MediaForge Agent Handoff

`AGENT.md` 是给不自动读取 `AGENTS.md` 的编码代理的兼容入口。完整工作约束以 [AGENTS.md](AGENTS.md) 为准；Claude 适配以 [CLAUDE.md](CLAUDE.md) 为准。

开始前必须依次阅读：

1. [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)
2. [docs/TASKS.md](docs/TASKS.md)
3. [docs/TECH_SPEC.md](docs/TECH_SPEC.md)
4. [docs/HARD_PARTS.md](docs/HARD_PARTS.md)

当前状态：R1–R9 已完成并提交，R0 的代理黄金路径也已用真实项目 `prj_a63f79b2` 走通：5 个真实来源 → AI 可审阅主稿 → GPT Image 2 真实生成与编辑 → 微信/头条独立 v4 → 可追责审批 → 本地 ZIP。完整证据见 `docs/product-validation/r0-real-theme-script.md`。R0 暂不勾选，只等待用户阅读稿件并决定是否愿意署名；不要继续扩展旧后台流水线、视频、数字人、更多平台或无人值守发布。R10 平台草稿交付、schema 迁移、真实发布或破坏性动作必须停下确认。

当前验证基线：完整 Python 测试 `1711 passed, 13 skipped`，跨进程发布锁连续 10 次通过，前端生产构建、Anthropic 源码导入护栏和浏览器黄金路径通过；`publish.enabled=false`。`frontend/dist/` 由 Vite 生成，不手工维护旧 hash 文件。GPT Image 2 已用用户在设置页配置的 OpenAI-compatible relay 完成真实生成与编辑；`OPENAI_API_KEY` 与可选 `OPENAI_IMAGE_BASE_URL` 仅保存到权限 `0600` 的 gitignored `secrets/env.json`，不得写入文档、代码或 Git。

共享工作区已有用户未提交改动。没有用户明确授权，禁止执行 `git reset --hard`、`git clean -fd`、删除、覆盖或清理它们。长程工作请使用专用 git worktree，并且每个可验收任务独立测试、更新 `TASKS.md`、独立 commit。
