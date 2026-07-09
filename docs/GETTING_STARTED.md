# GETTING_STARTED — 从零到第一篇内容上线

> 目标：照着这份文档一步步走，能在 30 分钟内把 MediaForge 从空仓库跑起来，
> 并产出第一篇过了质量门禁的 canonical 长文。

## 目录

- [§1 前置条件](#1-前置条件)
- [§2 克隆与虚拟环境](#2-克隆与虚拟环境)
- [§3 复制并编辑 config.yaml](#3-复制并编辑-configyaml)
- [§4 创建 secrets/ 目录](#4-创建-secrets-目录)
- [§5 设置 LLM API key](#5-设置-llm-api-key)
- [§6 初始化数据库 state.db](#6-初始化数据库-statedb)
- [§7 doctor 自检](#7-doctor-自检)
- [§8 跑第一轮流水线](#8-跑第一轮流水线)
- [§9 打开 Web 控制台](#9-打开-web-控制台)
- [§10 成本 baseline](#10-成本-baseline)
- [§11 ⚠️ 发布相关警告](#11-️-发布相关警告)
- [§12 下一步](#12-下一步)
- [§13 前端构建（M10 SPA，仅当你想改前端时需要）](#13-前端构建m10-spa仅当你想改前端时需要)

---

## §1 前置条件

- **Python 3.14**（项目用到 `datetime.now(timezone.utc)`、`str | None` 等新语法）
- **git** 2.30+
- **网络**：能拉取 RSS 源 + 调 LLM API
- **磁盘**：~500MB（venv + Chromium for Playwright）

## §2 克隆与虚拟环境

```bash
git clone <repo-url> MediaForge
cd MediaForge

python3.14 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## §3 复制并编辑 config.yaml

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 已在 `.gitignore`——**绝不提交**。打开后按需修改：

| 字段 | 作用 | 改不改 |
|------|------|--------|
| `pillars` | 内容支柱（评分 prompt 依据） | 可加可减 |
| `sources` | 选题数据源（默认含 `rss:hn`） | 默认即可 |
| `topics.daily_quota` | 每日精选上限 | 默认 5 |
| `topics.min_score` | 入选分数阈值 | 默认 6.0 |
| `gate.threshold_total` | 门禁总分阈值 | 默认 7.0 |
| `budget.monthly_usd` | LLM 月预算 | 默认 80 |
| `publish.enabled` | **发布总闸** | **保持 `false`**（见 §11） |
| `publish.allowed_platforms` | 允许发布的平台白名单 | 留空 `[]` |

**凭据不放 config 里**：所有 LLM key 走环境变量；平台 cookie 放 `secrets/`（§4）。

## §4 创建 secrets/ 目录

```bash
mkdir -p secrets
```

`secrets/` 已在 `.gitignore`。这是平台 cookie / OAuth token 等凭据的存放地。
**没启用发布前可以空着**；启用发布后才会用到。

## §5 设置 LLM API key

LLM 是创作管道（`create`）和门禁（`gate`）的核心，**没设 key 跑不动真 LLM**。

三种 provider 可叠加（实际读取逻辑见 `pipeline/creators/llm.py::setup_provider_from_env` + `pipeline/creators/image_gen.py::setup_provider_from_env`）：

### 优先：MiniMax-M3（Anthropic 兼容协议，便宜）

```bash
export MINIMAX_API_KEY=<your-key>
# 可选：自定义 base_url / model
export MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic   # 默认值
export MINIMAX_MODEL=MiniMax-M3                              # 默认值
```

### 备选：Anthropic 官方

```bash
export ANTHROPIC_API_KEY=<your-key>
```

### 图生成（M-x，MiniMax image-01）

```bash
export MINIMAX_IMAGE_API_KEY=<your-key>     # 可与 MINIMAX_API_KEY 同 key
# 可选：
export MINIMAX_IMAGE_BASE_URL=https://api.minimaxi.com/v1   # 默认
export MINIMAX_IMAGE_MODEL=image-01                         # 默认
export MINIMAX_IMAGE_TIMEOUT_S=90                           # 默认（docs 推荐 60–360s）
```

不设 `MINIMAX_IMAGE_*` 时 fallback 到 `MINIMAX_API_KEY`——一个 key 同时跑 chat + image。

> `MINIMAX_API_KEY` 和 `ANTHROPIC_API_KEY` 同时设置时优先 MiniMax。
> **别把 key 写进 config.yaml 或任何文件**——只走环境变量。

## §6 初始化数据库 state.db

```bash
python -m pipeline.run init-db
```

输出 `init-db: state.db ready`。幂等——重复跑无副作用。

## §7 doctor 自检

```bash
python -m pipeline.run doctor
```

正常输出（example）：

```
✅ config：已加载 ./config.yaml
❌ state.db：未找到 state.db；请运行 `python -m pipeline.run init-db`
❌ secrets：未找到 secrets 目录；请运行 `mkdir -p secrets`
❌ llm_key：未设置 LLM API key；请 export MINIMAX_API_KEY=...
✅ budget：已设置 $80.00/月
✅ publish.enabled：publish.enabled=false：发布总闸关闭，安全
$ echo $?  # → 1（有 ❌）
```

逐项按提示修完后应该全 ✅：

```
$ python -m pipeline.run doctor
✅ config：已加载 ./config.yaml
✅ state.db：state.db 存在
✅ secrets：secrets/ 存在
✅ llm_key：已设置 MINIMAX_API_KEY
✅ budget：已设置 $80.00/月
✅ publish.enabled：publish.enabled=false：发布总闸关闭，安全
$ echo $?  # → 0
```

**doctor 是体检不治疗**——它只报告，不创建文件、不改 config。

## §8 跑第一轮流水线

按依赖顺序跑（前一步产出是后一步输入）：

```bash
# 1) 拉取数据源 → 入库
python -m pipeline.run ingest
# 例：ingest: 12 fetched, 12 new, 0 dup

# 2) LLM 评分 + 选当日 selected
python -m pipeline.run score
# 例：score: 12 processed, 5 selected, 7 rejected, 0 url_dup_merged, 0 semantic_dup_merged

# 3) 为 selected topics 生成 canonical 长文
python -m pipeline.run create
# 例：create: 5 ok, 0 failed

# 4) 质量门禁（draft → gated/discarded）
python -m pipeline.run gate
# 例：gate: 5 processed, 3 gated, 2 discarded, 0 failed

# 5) 生成审核清单（人工 10 分钟 review）
python -m pipeline.run review
# 产出 output/<date>/REVIEW.md，每条 gated 内容一行
# 人工在 REVIEW.md 里把 `[ ] approve` 改成 `[x] approve`（或 `[-] reject: 原因`）
# 然后再跑一次 `python -m pipeline.run review` 让标记入 DB

# 6) 生成封面 + 文中插图（仅对 approved 内容，discarded 跳过省 $0.003/张）
python -m pipeline.run generate-images
# 读 canonical.md 里的 [IMAGE: ...] 占位 → 调 MiniMax image-01
# 落盘 output/<date>/<id>/cover.png + images/inline-N.png
# 写回 contents.cover_path / inline_images
```

**单条失败不阻断整批**（HARD_PARTS §5）——比如 gate 阶段一条 discarded 是正常流程，
不是错误；只有系统性失败（DB 损坏、预算超限）才退出。

**状态机保护**：所有阶段只能按 `pipeline/models.py` 定义的合法转移表推进。
非法转移抛 `IllegalTransition`（红字报错），DB 不变。

## §9 打开 Web 控制台

```bash
python -m pipeline.run webui
# 监听 http://127.0.0.1:8787
```

浏览器打开后你能看到：
- **Dashboard**：三表状态计数（topics/contents/publications）
- **选题池**：浏览 raw/scored/selected 各状态选题；promote / reject 按钮
- **审核台**：gated 内容卡片流，含 canonical 渲染 + 评分评语；approve / reject 按钮
- **发布日历**：周视图，按状态着色（queued / publishing / published / failed / cancelled）
- **设置**：config 脱敏展示 + 平台 cookie 健康状态

> Web UI 进程与 launchd 流水线**独立**——UI 挂了 cron 照跑，反之亦然。
> 这意味着你完全可以一边开 webui 看状态一边让它后台跑各阶段。

## §10 成本 baseline

> ✅ **已解锁（2026-07-07）**：Agnes-AI 真实跑通
>
> 关键发现：API host 是 `apihub.agnes-ai.com`（不是 `api.agnes-ai.com`，后者是 404 误域）。模型 `agnes-2.0-flash`，OpenAI 兼容协议。详见 M9-1（commit <TBD>）。

**冒烟复现脚本**：

```bash
# 1) source key（已 gitignore）：set -a; source secrets/agnes.env; set +a
# 2) 跑流水线：python -m pipeline.run ingest → score → create → gate
# 3) 取末行成本：python -m pipeline.run status | grep llm
```

**基线参考**：

| 日期 | Provider | ingest N | score selected | create ok/fail | gate gated/discarded | LLM 成本 | 备注 |
|------|----------|----------|----------------|----------------|----------------------|----------|------|
| 2026-07-05 | MiniMax-M3 (Anthropic 协议) | 38 | 5 | 3/2 | 0/3 | $0.0267 | 失败 2 条为 outline JSON 结构性错误；discarded 3 条 critic 抓 fact blocker |
| 2026-07-05（重试后）| MiniMax-M3 | 38 | 5 | 5/0 | 1/4 | — | 二次冒烟，complete_json 自动重试生效 |
| **2026-07-07** | **Agnes-AI agnes-2.0-flash** | **36** | **5** | **4/1** | **0/4** | **$0.42** | M9-1 接入；topic_dedup 29KB 超 agnes 上下文 404 走 M1-7 静默 fallback；timeout 1 条；4 条全 discarded（占位锚点严） |
| **2026-07-07** | **MiniMax image-01** | — | — | — | — | **$0/张（限时） / $0.003/张（标准）** | **M-x 接入**（commit 585b8f2）；独立 `pipeline/creators/image_gen.py`；只为 approved 内容生成（discarded 不调 API 省 $0.003 × 80% discard 率）；封面 16:9 + 文中插图 1:1 |

**M1-8 预筛评估（commit 5c74f0a）**：实测 score 单条 $0.000534、prefilter A $0.000294、prefilter B=10 $0.000181。
**结论**：
- MiniMax-M3 baseline $0.0267/日，月度 ≤ $1.0
- Agnes agnes-2.0-flash **实际价 0**（MODEL_PRICES 占位，平台可能免费期间），$0.42 是 placeholder 数字，真实成本按 agnes 牌价 / 实际扣费为准
- 若 agnes 仍免费，$80 月预算极宽松；若按 0.30/1.20 USD/Mtoken 计价（与 MiniMax 同档），$0.42 对应 ~6000 token 输出 + ~30000 token 输入

**已知限制**：
- Agnes agnes-2.0-flash 上下文窗口较小：M1-7 一次性喂 36 条 topic（29KB prompt）超限 → 静默 fallback 跳过语义去重。**M1-8 评估文档建议过按需 chunk**；当下单调降级不阻塞
- 网络抖动偶发 timeout（1/5 = 20%），RetryableError 自动重试 3 次（指数退避 1/2/4s）

## §11 ⚠️ 发布相关警告

**发布默认全部关闭**——本文档的 §1–§9 不涉及任何真实发布。

启用发布需要**全部**以下条件：

1. **登录平台账号**（扫码）：
   ```bash
   python -m pipeline.run login <platform> <account>
   # 例：python -m pipeline.run login xiaohongshu main
   # 会调起 Playwright 跳到平台登录页，扫码后 cookie 存到 secrets/
   ```

2. **config 显式开总闸**：
   ```yaml
   publish:
     enabled: true              # ← 必须显式 true
     allowed_platforms:        # ← 必须显式列出允许平台
       - xiaohongshu
       - toutiao
       - x
   ```

3. **真实跑前先 dry-run**：
   ```bash
   python -m pipeline.run publish --dry-run
   # 看哪些 queued 会被发，确认无误再去掉 --dry-run
   ```

### 发布安全防线（HARD_PARTS §1）

`pipeline/publishers/safe_publish.py` 三重锁自动生效，**不需要也不应该绕过**：

1. **配置锁**：`publish.enabled` 或 `allowed_platforms` 不满足 → 直接返回，不触 DB
2. **乐观锁**：`UPDATE WHERE status='queued' rowcount==1` → 抢锁失败 = 另一进程并发
3. **UNIQUE 兜底**：`publications.UNIQUE(content_id, platform, account_id)` → 数据库级防重复

**禁止绕过 `safe_publish` 自己拼发布逻辑**——这是全系统最高优先级的正确性保护。

### 凭据安全（HARD_PARTS §9）

- 所有 cookie / OAuth token 只放 `secrets/`（已 gitignore）和环境变量
- 代码里出现硬编码密钥 = 任务不合格
- IM 通知内容不含 cookie / token / api_key

## §12 下一步

跑通本文档后：

| 任务 | 用途 | 命令 |
|------|------|------|
| **M3-1 scheduler** | approved 自动排期到黄金时段 | `python -m pipeline.run schedule` |
| **M3-2 launchd** | 全流水线无人值守定时执行 | `bash scripts/install_launchd.sh` |
| **M4 发布通道** | 真发到平台（先 login 后开 `publish.enabled`） | `python -m pipeline.run publish` |
| **M6-1 collect** | 发布 24h/72h 后回流表现数据 | `python -m pipeline.run collect` |
| **M6-2 周报** | 每周一自动生成 `output/weekly-report.md` | `python -m pipeline.run report weekly` |

**Web UI 是日常运营主入口**：上述命令都能在 webui 里点按钮跑（见里程碑 7 的 U7-2「一键运行流水线」）。

---

## §13 前端构建（M10 SPA，仅当你想改前端时需要）

> ⚠️ **本节为占位**——M10 P1 任务尚未落地，前端目录 `frontend/` 还不存在（计划在 M10-7 由 `npm create vite@latest frontend -- --template vue-ts` 初始化）。**如果你 clone 下来的仓库 `frontend/dist/` 已有**（正式版会随 P1 完成提交），跳过本节直接 `python -m pipeline.run webui` 即可开箱即用。

首次构建（仅开发/修改前端时跑一次）：

```bash
cd frontend
npm ci              # 按 package-lock.json 装依赖
npm run build       # 产物输出到 frontend/dist/（默认提交到仓库）
```

开发模式（前后端联调）：

```bash
# 终端 1：起后端（默认 127.0.0.1:8787）
python -m pipeline.run webui

# 终端 2：起 Vite dev server（默认 5173，proxy /api→后端 8787）
cd frontend && npm run dev
```

技术栈：Vue 3 + Vite + TypeScript + Ant Design Vue + Pinia + Vue Router + ECharts。
后端契约 `/api/v1/*` 见 [`docs/TECH_SPEC.md §7`](./TECH_SPEC.md#7-web-控制台契约webui)。

---

## 故障排除

| 现象 | 原因 | 修法 |
|------|------|------|
| `doctor` 报 `llm_key: ❌` | 没设 env var | `export MINIMAX_API_KEY=...` 后重跑 |
| `create: BudgetExceeded` | 当月预算耗尽 | 调高 `budget.monthly_usd` 或等下月 |
| `gate: ... discarded` 全是 | 锚点样例偏离用户喜好 | 看 `pipeline/gate/anchors/` 替换样例 |
| `publish: 0 scheduled` | 无 approved 内容 | 先 review 把 gated 内容通过 |
| `webui` 起不来端口占用 | 8787 已被占 | `lsof -i :8787` 找进程，杀掉或改 `webui.port` |

更多坑见 [`docs/HARD_PARTS.md`](./HARD_PARTS.md)。