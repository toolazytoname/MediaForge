# CLAUDE.md — MediaForge (self0704)

## 这个项目是什么

AI 自媒体矩阵全自动流水线：选题 → 创作 → 质量门禁 → 人审 → 多平台发布 → 数据回流。
Python 后端 + SQLite 状态机 + CLI 子命令 + cron 调度。发布端复用开源组件，本仓库只写编排层。

## 会话重启指引（READ THIS FIRST）

每次会话开始，按顺序读这三个文件再开工，**不要通读整个 codebase**：

1. `docs/TASKS.md` — 当前任务清单与恢复点，认领第一个 `[ ]` 未完成任务
2. `docs/TECH_SPEC.md` — 数据模型与接口契约（实现必须严格遵守，不得擅自改 schema）
3. `docs/HARD_PARTS.md` — 你要做的任务如果在这里有对应条目，先读完再动手

> **记忆活在文件里，不活在上下文里。** 你做到哪、下一步做什么、不许碰什么，全部由上面三个文件 + git 历史决定，**不靠"记住"**。所以 `/clear`、换 subagent、换会话、换模型、进程崩溃——都不影响连续性：任何一个空白上下文读完这三个文件就能精确接续。要跑长程连续任务，见下方「自治连续执行」。

## 工作约定（强制）

1. **严格按 TASKS.md 顺序执行**，做完勾选并在任务下方追加一行 `✅ 完成于 <日期>，commit <sha>，备注 <一句话>`。默认「一次会话一个任务」是给单独人工驱动时的稳妥节奏；**用户明确要长程连续时，走下方「自治连续执行」协议**，可一口气做多个任务，安全性由「每任务独立校验 + 独立 commit + 状态落盘」保证，而非靠限制任务数。
2. **接口契约不可变**：`pipeline/models.py` 的字段、`SourceAdapter`/`PublisherAdapter` 的方法签名、SQLite 表结构，都在 TECH_SPEC.md 里定死了。如果实现中发现契约有问题，**停下来在 TASKS.md 里记录问题**，不要擅自修改契约。
3. **TDD**：每个任务先写测试（TASKS.md 里已给出测试要点），RED → GREEN → 重构
4. **不可变数据**：函数返回新对象，不原地修改传入参数（遵守全局 coding-style 规则）
5. **每个任务完成即 commit**，格式 `feat: <任务编号> <描述>`，不留悬空状态
6. **凭据安全**：所有密钥/cookie 只放 `secrets/`（已 gitignore）和环境变量，代码里出现硬编码密钥 = 任务不合格
7. **不要越权发布**：`publish` 相关代码在 M4 之前只做 dry-run，真实发布需要 config 里 `publish.enabled: true` 且该平台在 `publish.allowed_platforms` 白名单中
8. **遇到卡点**：先查 `docs/HARD_PARTS.md` 对应章节；解决不了就在 TASKS.md 该任务下记录 `⚠️ BLOCKED: <原因>`，跳到下一个不依赖它的任务

## 自治连续执行（长程模式）

> 用户说「连续做完 XX」「长程跑下去」「一直做」时启用。核心原则：**每一轮都是无状态的（上下文里什么都不留），有状态的部分全落盘（TASKS.md + git）。** 任何一步崩溃，下一个空白上下文读文件即可精确接续——`/clear` 无害。

**单个任务的执行回路（每个 `[ ]` 任务都走一遍）：**

1. **崩溃检测**：`git status`。若工作区脏 **且** 当前任务未打勾 → 说明上一次做到一半死了 → `git reset --hard && git clean -fd` 清干净，从头做该任务。（这是「绝不留悬空状态」的硬保证，靠协议而非记忆。）
2. **认领**：读 `docs/TASKS.md`，取第一个 `[ ]` 任务；读它引用的 TECH_SPEC / HARD_PARTS 章节。
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
  creators/         # 创作管道（调用 Claude / 视频生成）
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
