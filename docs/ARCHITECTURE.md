# ARCHITECTURE — 系统架构设计

> 版本 v1.1 | 2026-07-06 | M0~M6 主线全达成后回顾修订。接口契约细节见 [TECH_SPEC.md](./TECH_SPEC.md)

## 1. 设计原则

1. **管道-过滤器架构**：8 个阶段，每个阶段是纯粹的"读状态 → 处理 → 写状态"，阶段间只通过 SQLite 状态机 + 文件系统通信，无内存共享
2. **幂等可重跑**：任何阶段崩溃后直接重跑，不会重复消费/重复发布（靠状态机 + 唯一约束保证）
3. **适配器模式隔离外部世界**：数据源、LLM、发布平台全部藏在适配器接口后面，换实现不动编排逻辑
4. **人在环上（human-on-the-loop）**：人审是一个普通的流水线阶段，而非特殊分支；后期"免审直发"只是把该阶段的策略从 `manual` 换成 `auto_above_score`
5. **失败默认安全**：任何不确定 → 不发布。发布是唯一不可逆操作，受三重保护（见 §6）

## 2. 系统总览

```
                        ┌─────────────────────────────────────────┐
                        │              cron / launchd              │
                        │  06:00 ingest+score   06:30 create+gate  │
                        │  09:00 review-notify  10:00+ schedule    │
                        │  */30 publish-due     23:00 collect      │
                        └────────────────┬────────────────────────┘
                                         │ 触发 CLI 子命令
                                         ▼
┌────────────────────────────────────────────────────────────────────┐
│                     pipeline.run (CLI 编排层)                       │
│                                                                    │
│  ingest ──► score ──► create ──► gate ──► review ──► schedule ──► publish ──► collect │
│    │          │          │         │         │          │            │          │     │
└────┼──────────┼──────────┼─────────┼─────────┼──────────┼────────────┼──────────┼─────┘
     │          │          │         │         │          │            │          │
     ▼          ▼          ▼         ▼         ▼          ▼            ▼          ▼
 sources/   topics/    creators/   gate/    review/   scheduler    publishers/        metrics/
 RSS,HN,    LLM评分    LLM API+    LLM      IM通知/    错峰排期     自写Playwright    平台数据
 GH趋势     去重      模板渲染+   批判     审核文件    可复现种子    +Xiaohongshu      回流
 DailyHot             MPT/Pixelle 评分                 (sha256)     Skills桥,X API    +周报
     │          │          │         │         │          │            │          │
     └──────────┴──────────┴─────────┴────┬────┴──────────┴────────────┴──────────┘
                                          ▼
                            ┌──────────────────────────┐
                            │  SQLite (state.db)        │
                            │  topics / contents /      │
                            │  publications / metrics   │
                            └──────────────────────────┘
                            ┌──────────────────────────┐
                            │  文件系统 output/         │
                            │  YYYY-MM-DD/<content_id>/ │
                            │  (稿件、图、视频、审核单)  │
                            └──────────────────────────┘
```

## 3. 模块职责与边界

### 3.1 sources/ — 选题采集

- 输入：无（外部世界）。输出：`Topic` 记录（status=`raw`）
- 每个数据源一个 `SourceAdapter` 子类：`RssSource`、`HackerNewsSource`、`GithubTrendingSource`、`DailyHotSource`
- **边界**：只负责"抓回来、标准化、去重入库"，不做任何价值判断
- 去重：`content_hash = sha256(normalize(title) + url_domain)` 唯一索引，重复插入静默跳过

### 3.2 topics/ — 选题评分

- 输入：status=`raw` 的 Topic。输出：status=`scored`（带分数）或 `rejected`
- LLM（便宜模型，如 Haiku）按内容支柱匹配度、时效性、可加工性打分
- 每日取 top-N（config `topics.daily_quota`，默认 5）晋升为 `selected`
- **边界**：不生成任何内容，只做排序和筛选

### 3.3 creators/ — 创作管道

- 输入：status=`selected` 的 Topic。输出：`Content` 记录（status=`draft`）+ `output/` 下的文件
- 两级结构：
  - `CanonicalCreator`：生成核心内容（深度长文 markdown）——这是唯一一次"真正的创作"
  - `DerivativeCreator`：从 canonical 派生平台原生格式（图卡、thread、视频脚本）——只做"翻译"，不做二次创作
- LLM 调用统一走 `creators/llm.py`（Claude / MiniMax API 封装，含重试/预算控制/JSON 修复）
- 视频口播稿走 `creators/video_script.py`（口播稿派生，60-90s 钩子前置）
- 视频生成走 `creators/video/` 抽象层：MPT 默认（量产）+ Pixelle-Video 第二引擎（精品，`mode=fixed` 注入我方脚本）
- 图卡渲染走 `creators/render.py`（Jinja2 + Playwright 1080×1440，可选 baoyu-image-gen 配图）
- **边界**：创作不自评。写完就交给 gate

### 3.4 gate/ — 质量门禁（本系统的灵魂）

- 输入：status=`draft` 的 Content。输出：status=`gated`（含评分）或 `discarded`
- 三步流程：
  1. **批判轮**：LLM 以"挑剔编辑"人设列出稿件的具体问题
  2. **重写轮**：创作模型带着批判意见重写一次（最多 1 轮，防止无限循环）
  3. **评分轮**：独立 LLM 会话按 PRD §3.3 三维评分（评分 prompt 与创作 prompt 隔离，避免自我偏袒）
- 阈值淘汰：总分 < 24 或单维 < 6 → `discarded`（保留文件供复盘，不删除）
- **边界**：门禁只否决不修改（重写是叫创作模块重写）

### 3.5 review/ — 人工审核

- 输入：status=`gated`。输出：status=`approved` / `rejected_by_human`
- 实现：生成 `output/YYYY-MM-DD/REVIEW.md` 审核清单（每条含预览+评分+一键决定标记），可选推送 IM 通知
- 人的操作：编辑 REVIEW.md，把 `[ ]` 改成 `[x]`（通过）或 `[-]`（打回）；下次 `review` 命令运行时读取并落库
- 策略可配置：`review.policy = manual | auto_above <score>`（M6 后可对高分内容免审）
- **边界**：不做编辑修改。人只有通过/打回二元权力，要改内容就打回重造

### 3.6 scheduler — 发布排期

- 输入：status=`approved`。输出：`Publication` 记录（status=`queued`，带 `scheduled_at`）
- 排期规则（纯函数，好测试）：
  - 平台黄金时段表（config）内随机取点，**避开整点**
  - 同平台同账号两次发布间隔 ≥ config `min_gap_hours`（默认 4h）
  - 同内容不同平台错开 ≥ 30min
- **边界**：只写排期表，不碰任何平台

### 3.7 publishers/ — 发布执行

- 输入：status=`queued` 且 `scheduled_at <= now` 的 Publication。输出：status=`published`（带平台返回的 URL/ID）或 `failed`
- 每平台一个 `PublisherAdapter` 子类：
  - `XApiPublisher`：X 官方 API v2（OAuth2 bearer + 链式 thread）
  - `ToutiaoPublisher`：自写 Playwright，图文长文（与 M0-0 决策一致——AiToEarn 整体方案放弃）
  - `XiaohongshuPublisher`：subprocess 封装 `white0dew/XiaohongshuSkills`（vendor pin commit），Plan B 是自写 Playwright
  - `DouyinPublisher`：自写 Playwright，**强制勾选 AI 生成标识**（PRD §3.4 不可省略）
  - 后续：`WechatMpPublisher`（官方 API 草稿箱 + 人工点发布）、`YoutubePublisher`（Postiz API）
- **三重安全锁**（缺一不发）：见 §6
- 失败重试：失败 → `failed` + IM 告警，**绝不**自动无限重试（风控敏感）；需重试由 Web UI 或 `reset` 命令人工触发
- **边界**：发布器不修改内容。格式不合规（超字数等）→ 报 `failed` 让上游修
- **超时清理**：publishing 状态超过 30min 的记录自动 → failed + 'manual check needed'（绝不自动重试，HARD_PARTS §1）

### 3.8 metrics/ — 数据回流

- 输入：status=`published` 且发布 > 24h 的 Publication。输出：`Metric` 记录（曝光/互动数据快照）
- 定时抓取各平台表现数据（X 走 API、头条/抖音走 Playwright 创作者后台、小红书暂无标准化命令）入库 `metrics` 表
- 每周一生成 `output/weekly-report.md`（发布数 / 门禁通过率 / 平台 top3 bottom3 / LLM 成本 / 门禁分-表现 Pearson r）
- M6-3 待前置条件满足后启动：高分门禁内容 auto-approved 免审直发
- **边界**：只读平台数据，绝不代表用户进行互动（点赞/回复自动化是高危风控行为）

### 3.9 webui/ — 本地 Web 控制台（图形化管控）

- 技术：FastAPI + Jinja2 服务端渲染 + htmx（**刻意不用** React/Vite 前后端分离——单人本地工具，SSR 足够，弱模型也容易维护）
- 与流水线的关系：**只是 SQLite 和 output/ 之上的一层视图 + 少量写操作**，不承载任何业务逻辑；流水线不依赖 UI，UI 挂了 cron 照跑
- 页面：
  1. **Dashboard**：各状态计数、今日流水线运行记录、LLM 本月成本、最近告警
  2. **选题池**：topics 列表（按状态过滤），手动"加急"某 topic（升为 selected）或废弃
  3. **审核台**（核心页面）：待审内容并排展示 canonical 预览 + 图卡缩略 + 门禁评分/评语，一键 通过/打回（打回填原因）
  4. **发布日历**：publications 周视图（7 列日格），可改时间、取消、把 failed 重置为 queued
  5. **内容详情**：单条内容全链路时间线（topic→创作→门禁→审核→各平台发布→数据）
  6. **设置**：只读展示 config（脱敏）+ 各平台登录态健康状态（头条 storage_state / 小红书 skills_path / X bearer_token 三类）
- **REVIEW.md 与 webui 并存**：M2-5 实现的 REVIEW.md 仍是 cron 主路径（人可手编）；M3-3 实现的 webui 是新增的图形化通道，两条路径都走 `db.transition()` 状态机
- 写操作全部复用 `db.py transition()`（状态机约束对 UI 同样生效）；发布三重锁对 UI 触发的操作同样生效
- 安全：默认只绑 `127.0.0.1:8787`，本机单人无认证；部署 VPS 需开 `webui.auth` basic auth

## 4. 内容状态机（核心不变量）

```
Topic:    raw → scored → selected → (消费后) consumed
                  └→ rejected

Content:  draft → gated → approved → (全部 publication 完成后) done
             │       │        └→ rejected_by_human
             │       └→ discarded (门禁淘汰)
             └→ failed (创作出错)

Publication: queued → publishing → published
                │          └→ failed (可人工重置为 queued)
                └→ cancelled
```

**不变量（测试必须覆盖）**：
- 状态只能沿箭头走，任何代码不得逆向或跳跃改状态（除人工 CLI `reset` 命令）
- 一个 Topic 最多产生一个 Content（1:1）；一个 Content 可有多个 Publication（1:N，每平台每账号一条）
- `publications` 表有 `UNIQUE(content_id, platform, account_id)` 约束 —— 这是防重复发布的最后防线

## 5. 技术选型与理由

| 决策 | 选型 | 理由 | 被否掉的方案 |
|------|------|------|-------------|
| 语言 | Python 3.11+ | 生态（social-auto-upload/MPT 都是 Python）、LLM SDK 成熟 | Node（发布组件生态弱） |
| 存储 | SQLite + 文件系统 | 单机单写者，零运维，事务够用 | Postgres（杀鸡牛刀）、纯 JSON 文件（无事务无约束） |
| 调度 | cron/launchd 起步 | KISS；每阶段是独立命令天然可 cron | n8n（V1 引入=运维负担）、Celery（无分布式需求） |
| LLM | Anthropic API 直连（Haiku 初筛/Sonnet 创作） | 纯 API 依赖，配 key 即跑，不依赖 Claude Code CLI | 依赖本地 Claude Code skills（被否：系统必须能脱离开发工具独立运行） |
| 图卡/封面渲染 | HTML 模板（Jinja2）+ Playwright 截图 | 零外部服务、离线可跑、风格由模板资产控制 | 调 Claude Code skills（开发工具依赖）、纯图像生成 API（成本+不可控） |
| 视频（量产） | MoneyPrinterTurbo（API 模式，Docker 部署） | 95k star 项目、活跃、MIT、有 HTTP API | 自写 ffmpeg 管线（重复造轮子） |
| 视频（精品/AI 生成类） | Pixelle-Video（第二引擎，mode=fixed 注入我方脚本） | 24k star，Web 端可见可控 | NarratoAI / OpenMontage（远期观察） |
| 国内发布 | 自写 Playwright + XiaohongshuSkills 桥（subprocess） | 头条/抖音无官方 API → 必须浏览器自动化；小红书借社区成熟方案 | AiToEarn 自部署（V1 评估放弃——自部署下国内平台无法无人值守）；social-auto-upload 的现成 uploader（V1 评估参考） |
| 海外发布 | X 官方 API 起步；扩展期上 Postiz | X API 免费档够用；Postiz 一次接入多平台 | 逐平台自接（工作量爆炸） |
| 通知 | 飞书/Telegram webhook（config 二选一） | 单人使用，webhook 最简 | 自建 Web UI（YAGNI） |
| 配置 | 单一 `config.yaml` + pydantic 校验 | 启动即验证，fail fast | 环境变量散落（难管理） |

## 6. 发布安全（三重锁）

发布是系统里唯一不可逆动作，必须同时满足：

1. **全局开关**：`config.publish.enabled == true`（默认 false，M4 前禁止打开）
2. **平台白名单**：目标平台在 `config.publish.allowed_platforms` 中
3. **状态与约束**：Publication 状态为 `queued`、关联 Content 为 `approved`、数据库唯一约束未违反

另外：
- `--dry-run` 标志贯穿所有发布代码路径，dry-run 下走完全流程但最后一步只打日志
- 发布前将状态置为 `publishing`（乐观锁：`UPDATE ... WHERE status='queued'` 影响行数为 0 则说明并发/重复，放弃）
- 发布后立即落库平台返回 ID；**落库失败比发布失败更严重**（会导致重发），所以先写"发布意图"日志再调平台接口

## 7. 部署形态

- **V1**：本机（macOS 或 Linux），定时驱动——
  - macOS：launchd plist（睡眠唤醒后会补跑）
  - Linux：cron + `pipeline/run.py` 入口的 flock 装饰器（防并发重叠）
  - 两者都装：`scripts/install_launchd.sh`（mac）+ `scripts/install_cron.sh`（Linux）幂等切换
- **V2**：迁移到一台常开 VPS/家用小主机；国内平台发布器因需要登录态，建议留在本地有头浏览器环境或配指纹浏览器
- MoneyPrinterTurbo / Pixelle-Video：各自 Docker Compose 部署在同机
- 日志：`logs/pipeline.log`（rotating），所有阶段共用结构化日志（json lines），排障靠 `content_id` 全链路追踪
- 备份：`scripts/backup_db.sh` 每日 `sqlite3 .backup` 命令（保证一致性，非 cp）→ `backups/` 保留 14 天

## 8. 目录与文件产物约定

```
output/
  2026-07-05/
    c_a1b2c3/                      # content_id 目录
      canonical.md                 # 核心长文
      meta.json                    # Content 元数据快照
      critique.md                  # 门禁批判意见（gated 后生成）
      toutiao.md                   # 头条短文
      xiaohongshu/
        slides.json                # 图卡结构化数据
        caption.md
        tags.txt
        cards/*.png                # 渲染后的 1080×1440 图卡
      x/
        thread.md                  # ≤10 条英文 thread
    REVIEW.md                      # 当日审核清单（M2-5 cron 路径）
  weekly-report.md                 # 周一生成的周报（M6-2）

secrets/
  cookies/<platform>_<account>.json  # Playwright storage_state，权限 600
  *.env                            # 平台凭据（gitignore）

locks/
  <stage>.lock                    # flock 防并发，同 stage 不重叠

backups/
  state-YYYY-MM-DD.db             # sqlite3 .backup 每日滚动 14 天
```
