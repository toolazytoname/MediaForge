# AGENTS.md — MediaForge (self0704)

## 这个项目是什么

MediaForge 正在从“AI 自媒体矩阵全自动流水线”重启为 **AI 原生个人创作工作台**：一个主题 → 一个创作项目 → 一篇有依据的主作品 → 多个仍可独立编辑的平台版本 → 人工批准后的安全交付与复盘。

Python 后端 + SQLite 状态机 + CLI 子命令 + Vue SPA。既有 pipeline 是可复用的后台能力，不是面向创作者的主导航。

## 产品重启指令（当前最高优先级）

在开始任何实现前，先完整阅读 [docs/PRODUCT_RESET_PLAN.md](docs/PRODUCT_RESET_PLAN.md)。它定义了当前产品方向和 R0–R14 的建设顺序。

- 北极星：单人创作者能在 30–60 分钟完成“一个主题 → 一篇主稿 → 微信公众号和头条两个可编辑草稿（含封面与插图）”。
- 先做图文创作闭环；小红书、视频号、Bilibili、数字人、更多平台、多账号和无人值守真发布全部后置。
- 产品入口应围绕“今天 / 灵感 / 项目 / 资产 / 发布 / 复盘”，不能把 ingest、score、gate、run 等内部状态机直接当普通用户导航。
- 手工写作和自动创作必须收敛到同一项目工作台，通过“自主程度”控制 AI 参与度；AI 改动必须可审阅、可撤销，不能静默覆盖正文。
- 现有 `topics → contents` 的冻结 1:1 契约暂不改。Project v0 先用 `output/projects/<project_id>/project.json` sidecar manifest 聚合已有内容与资产；需要迁移 schema 时先写 RFC 并等待用户确认。
- 在 R11 的四周真人使用实验达标以前，禁止把“支持的平台数、生成篇数、后台卡片数”当成功指标。

### 当前交接基线（2026-08-09）

- **R1–R9 已完成并提交**：六入口导航、Idea Inbox、Project 研究板、可撤销主稿共创、GPT Image 2 视觉资产、微信/头条独立版本与内容包审批均已落地。以 `docs/TASKS.md` 的完成记录和 git 历史为准。
- **R0 仍待真人验收**：必须使用用户真正准备发布的主题、3–5 个来源、目标读者、发布目的和个人观点；不得用 mock、占位或演示内容代替。
- 下一安全动作是执行 R0 的 60 分钟真人路径并落盘真实耗时、人工修改比例和摩擦点。R10 的真实交付、任何 schema 迁移、真实发布、删除/覆盖用户数据仍需单独确认。
- GPT Image 2 provider 已支持生成与编辑；真实调用的 `OPENAI_API_KEY` 只从环境变量读取。未配置时 UI 会明确提示不可用并保留失败审计。
- 当前交付基线：完整 Python 回归 **1685 passed, 13 skipped**；前端生产构建通过（仅有既知的大 chunk 警告）。
- `frontend/dist/` 是 Vite 生成物；源码变化后重建即可，不要手工维护旧 hash 资源。

## 会话重启指引（READ THIS FIRST）

每次会话开始，按顺序读这四个文件再开工，**不要通读整个 codebase**：

1. `docs/PRODUCT_RESET_PLAN.md` — 当前产品目标、边界和 R0–R14 建设顺序
2. `docs/TASKS.md` — 已实现能力、旧任务和恢复记录；当前以 R0 真人验收为唯一未完成的产品重启任务，不得机械认领遗留的第一个 `[ ]`
3. `docs/TECH_SPEC.md` — 数据模型与接口契约（实现必须严格遵守，不得擅自改 schema）
4. `docs/HARD_PARTS.md` — 你要做的任务如果在这里有对应条目，先读完再动手

> **记忆活在文件里，不活在上下文里。** 你做到哪、下一步做什么、不许碰什么，全部由上面四个文件 + git 历史决定，**不靠"记住"**。所以 `/clear`、换 subagent、换会话、换模型、进程崩溃——都不影响连续性：任何一个空白上下文读完这四个文件就能精确接续。要跑长程连续任务，见下方「自治连续执行」。

## 工作约定（强制）

1. **产品重启优先于遗留任务顺序**：R1–R9 已完成，当前只认领 R0 真人验收及其直接修复；不要机械领取旧 M* 清单中的未完成项，也不要未经确认进入 R10。完成任务后勾选并追加 `✅ 完成于 <日期>，commit <sha>，备注 <一句话>`。
2. **接口契约不可变**：`pipeline/models.py` 的字段、`SourceAdapter`/`PublisherAdapter` 的方法签名、SQLite 表结构，都在 TECH_SPEC.md 里定死了。如果实现中发现契约有问题，**停下来在 TASKS.md 里记录问题**，不要擅自修改契约。
3. **TDD**：每个任务先写测试（TASKS.md 里已给出测试要点），RED → GREEN → 重构
4. **不可变数据**：函数返回新对象，不原地修改传入参数（遵守全局 coding-style 规则）
5. **每个任务完成即 commit**，格式 `feat: <任务编号> <描述>`，不留悬空状态
6. **凭据安全**：所有密钥/cookie 只放 `secrets/`（已 gitignore）和环境变量，代码里出现硬编码密钥 = 任务不合格
7. **不要越权发布**：`publish` 相关代码在 M4 之前只做 dry-run，真实发布需要 config 里 `publish.enabled: true` 且该平台在 `publish.allowed_platforms` 白名单中
8. **遇到卡点**：先查 `docs/HARD_PARTS.md` 对应章节；解决不了就在 TASKS.md 该任务下记录 `⚠️ BLOCKED: <原因>`，跳到下一个不依赖它的任务。
9. **工作区保护**：共享工作区出现脏文件时，先识别归属并保留。没有用户明确授权，绝不执行 `git reset --hard`、`git clean -fd`、覆盖或删除任何已有文件。长程任务优先新建 git worktree；只能在该 worktree 内清理自己尚未提交的尝试。

## 自治连续执行（长程模式）

> 用户说「连续做完 XX」「长程跑下去」「一直做」时启用。核心原则：**每一轮都是无状态的（上下文里什么都不留），有状态的部分全落盘（TASKS.md + git）。** 任何一步崩溃，下一个空白上下文读文件即可精确接续——`/clear` 无害。

**单个任务的执行回路（每个 `[ ]` 任务都走一遍）：**

1. **崩溃检测**：`git status`。识别改动归属：共享工作区或归属不清时停止清理，记录并保留；仅在专用 worktree、且明确是本任务未提交尝试时，才可按用户授权恢复。禁止用破坏性 reset/clean 作为默认恢复手段。
2. **认领**：先读 `docs/PRODUCT_RESET_PLAN.md`，确认当前 R 阶段；再读 `docs/TASKS.md` 中已被明确迁入的对应任务及其 TECH_SPEC / HARD_PARTS 引用。
3. **实现**：派**实现 subagent**（全新上下文，只喂「本任务规格 + 契约红线」），照 TDD 改代码。主会话自己不写实现，只当协调器保持精简。
4. **校验**：派**校验 subagent**（全新上下文，只喂「本任务验收标准 + `git diff`」），先过客观闸再上评审：
   - 客观闸（任一不过即失败，不需 LLM 判断）：
     - `python -m pytest tests/ -q` 全绿
     - `grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空（成本护栏，HARD_PARTS §4）
     - `git diff --name-only` ⊆ 本任务声明改动的文件集（防「一口气改太多」）
     - `git diff` 未触及 `models.py` 字段 / SQL schema / Adapter 签名 / TECH_SPEC §3–5 契约行（防契约漂移）
   - LLM 评审（客观闸过了才做）：逐条核对本任务「验收标准」是否**真达成**（测试绿 ≠ 完成，验收满足才算，HARD_PARTS §10.5）；有没有 mock 掉状态机（禁止）。返回 `{pass: bool, blocking_issues: [...]}`。
5. **结算**：
   - 通过 → `[ ]`→`[x]` + 追加 `✅ 完成于 <日期>，commit <sha>，备注 <一句话>` + `git commit`（`feat:`/`fix:` + 任务编号）。
   - 不通过 → 回步骤 3，把校验的 `blocking_issues` 一并喂给实现 subagent 重做；连续 2 次不过就在该任务下写 `⚠️ BLOCKED: <原因>`，跳下一个任务。
6. **落盘即安全**：commit 完成后本轮状态已全部持久化，可以 `/clear` 或换上下文继续下一轮。

**高危任务例外**：涉及真实发布（`publish` 真发、非 dry-run）或删除/覆盖用户数据的任务，**不进自治流**——停下来让用户人工确认（工作约定第 7 条 + HARD_PARTS §1 防重复发布是全系统最高优先级）。校验/实现遇到这类任务，标注后跳过。

**隔离建议**：长程自治跑动前，优先在 git worktree 里跑（不碰用户当前工作区，做完再合），除非用户另有指示。


## 常用命令

```bash
source .venv/bin/activate
python -m pipeline.run <stage>     # ingest|score|create|gate|review|schedule|publish|collect
python -m pytest tests/ -x -q      # 跑测试
python -m pipeline.run status      # 查看流水线各状态内容数量
```

## 目录速览

```
pipeline/
  run.py            # CLI 入口（argparse 子命令）
  models.py         # 数据模型（dataclass，冻结不可变）
  db.py             # SQLite 封装 + 状态机迁移
  sources/          # 选题数据源适配器（SourceAdapter 子类）
  topics/           # 选题评分与去重
  creators/         # 创作管道（调用 Codex / 视频生成）
  gate/             # 质量门禁（多轮批判+评分）
  review/           # 人审交互（生成审核清单/读取审核结果）
  publishers/       # 发布适配器（PublisherAdapter 子类）
  metrics/          # 数据回流
  utils/            # 日志、重试、限流等公共件
tests/              # pytest，镜像 pipeline/ 结构
output/             # 每日产出 output/YYYY-MM-DD/<content_id>/
secrets/            # 凭据（gitignored）
docs/               # 全部文档
```
