# MediaForge Agent Handoff

`AGENT.md` 是给不自动读取 `AGENTS.md` 的编码代理的兼容入口。完整工作约束以 [AGENTS.md](AGENTS.md) 为准；Claude 适配以 [CLAUDE.md](CLAUDE.md) 为准。

开始前必须依次阅读：

1. [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)
2. [docs/TASKS.md](docs/TASKS.md)
3. [docs/TECH_SPEC.md](docs/TECH_SPEC.md)
4. [docs/HARD_PARTS.md](docs/HARD_PARTS.md)
5. [docs/product-validation/2026-08-12-confirmed-product-definition.md](docs/product-validation/2026-08-12-confirmed-product-definition.md)
6. [docs/product-validation/2026-08-12-confirmed-product-tasklist.md](docs/product-validation/2026-08-12-confirmed-product-tasklist.md)

当前状态：R1–R9 的底层能力已完成，旧 P0-A—P0-G 也已在独立 worktree 实施，但用户继续明确表示产品不会使用。2026-08-12 访谈把方向进一步收敛为：首页直接输入主题、想法和可选资料，一次生成带封面与上下文插图的完整文章；用户可对局部或全文批注，并以双栏 diff 审查 AI 修改；个人创作与自动化创作是两条入口。产品所有者已于 2026-08-12 授权 `DOC-02`，`PF-00` 已完成旧 P0 资产审计（见 `docs/product-validation/2026-08-12-pf00-asset-audit.md`）；下一步是 UX-00 真人原型确认，之后才进入 G1。不得自行领取旧 P0、R10、视频、平台扩张、知识库或真实发布任务。

此前记录的旧 P0 工程基线是完整 Python 测试 `1711 passed, 13 skipped`、跨进程发布锁连续 10 次通过、前端生产构建和 Anthropic 源码导入护栏通过；本次文档固化没有重跑，且不能据此宣称新产品路径通过。`publish.enabled=false`。`frontend/dist/` 由 Vite 生成，不手工维护旧 hash 文件。GPT Image 2 已用用户在设置页配置的 OpenAI-compatible relay 完成真实生成与编辑；`OPENAI_API_KEY` 与可选 `OPENAI_IMAGE_BASE_URL` 仅保存到权限 `0600` 的 gitignored `secrets/env.json`，不得写入文档、代码或 Git。

共享工作区已有用户未提交改动。没有用户明确授权，禁止执行 `git reset --hard`、`git clean -fd`、删除、覆盖或清理它们。长程工作请使用专用 git worktree，并且每个可验收任务独立测试、更新 `TASKS.md`、独立 commit。
