# MediaForge Agent Handoff

`AGENT.md` 是给不自动读取 `AGENTS.md` 的编码代理的兼容入口。完整工作约束以 [AGENTS.md](AGENTS.md) 为准；Claude 适配以 [CLAUDE.md](CLAUDE.md) 为准。

开始前必须依次阅读：

1. [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)
2. [docs/TASKS.md](docs/TASKS.md)
3. [docs/TECH_SPEC.md](docs/TECH_SPEC.md)
4. [docs/HARD_PARTS.md](docs/HARD_PARTS.md)

当前状态：R1–R9 的底层能力已完成并提交，代理项目 `prj_a63f79b2` 也曾组合出主稿、真实图片、双平台稿、审批和 ZIP。但 2026-08-10 用户以普通创作者身份验收后明确判定产品仍然不会用。页面真人路径进一步复现“AI 先起草”没有被执行、初稿被隐藏研究门槛阻塞、局部错误清空工作台、完成态只见禁用表单和 ZIP 等问题。**R0 创作者验收失败，不是只差作者签字。** 当前唯一优先级是 `docs/product-validation/2026-08-10-creator-workflow-remediation.md` 的 P0-A 至 P0-G；不得进入 R10、继续扩平台、视频、数字人或无人值守发布。

当前验证基线：完整 Python 测试 `1711 passed, 13 skipped`，跨进程发布锁连续 10 次通过，前端生产构建、Anthropic 源码导入护栏和浏览器黄金路径通过；`publish.enabled=false`。`frontend/dist/` 由 Vite 生成，不手工维护旧 hash 文件。GPT Image 2 已用用户在设置页配置的 OpenAI-compatible relay 完成真实生成与编辑；`OPENAI_API_KEY` 与可选 `OPENAI_IMAGE_BASE_URL` 仅保存到权限 `0600` 的 gitignored `secrets/env.json`，不得写入文档、代码或 Git。

共享工作区已有用户未提交改动。没有用户明确授权，禁止执行 `git reset --hard`、`git clean -fd`、删除、覆盖或清理它们。长程工作请使用专用 git worktree，并且每个可验收任务独立测试、更新 `TASKS.md`、独立 commit。
