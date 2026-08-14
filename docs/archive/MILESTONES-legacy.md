# MILESTONES — 里程碑归档

> 版本 v1.0 | 2026-07-06
> M0~M6 主线达成的关键能力 / commit 锚点 / 真实冒烟结果 / 集成 TODO。详细任务过程见 [TASKS.md](./TASKS.md)，契约细节见 [TECH_SPEC.md](./TECH_SPEC.md)。

## 摘要（2026-07-06）

| 维度 | 数值 |
|------|------|
| 主线任务 | 25 个全部完成；M6-3 标记未启动（前置不满足——需 30 天真实运营数据）|
| 代码量（pipeline/） | ~4765 行 / 70 个文件 |
| 测试（tests/） | 764 pytest 全绿，2.7 分钟跑完 |
| 覆盖率 | 77%（TECH_SPEC §9 目标 80%，缺口在真实平台 Playwright 路径——已有 fake-server e2e 兜底）|
| 发布平台 | X（官方 API）/ 头条（自写 Playwright）/ 小红书（XiaohongshuSkills 桥）/ 抖音（自写 Playwright）|
| 视频引擎 | MPT（默认，量产）+ Pixelle-Video（第二引擎，精品）|
| 部署形态 | 单机 macOS launchd / Linux cron + flock；Web 控制台 127.0.0.1:8787 |
| 总耗时 | M0~M6 主线 ≈ 7 个开发日（2026-07-04 ~ 2026-07-06）|

---

## M0 — 项目地基

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M0-0 开源评估 | `56e068d` / `e776e2a` | 5 项 DECISION 落 `docs/research/evaluation-notes.md`（TrendPublish 参考 / XiaohongshuSkills 采用 / AiToEarn 放弃 / Pixelle-Video 视频第二引擎 / baoyu-skills §5.5 桥）|
| M0-1 工程脚手架 | `23e7911` | `.gitignore` / `requirements.txt` / `pipeline/run.py`（12 个占位子命令）/ venv |
| M0-2 数据模型 + 状态机 | `3305151` + `d735e50` | 4 frozen dataclass + 3 状态枚举 + `transition()` 强制转移表（154 矩阵测试）|
| M0-3 配置 + 日志 | `4cd4012` | pydantic v2 校验 config / JsonLineFormatter + TimedRotatingFileHandler / 6 异常类 |

**测试快照**：M0 末 201 全绿。

---

## M1 — 选题引擎

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M1-1 SourceAdapter + RSS | `dd58647` | `SourceAdapter` ABC + `RssSource`（feedparser + ISO8601 UTC + 异常包 SourceError）+ `registry` |
| M1-2 ingest 编排 | `8d67fde` | `content_hash = sha256(normalize(title)+domain)` 去重 + `try_insert_topic` INSERT OR IGNORE + 单源失败 warn 续行 |
| M1-3 LLM 封装 | `21e2bb8` | `LLMProvider` 抽象 + `MockProvider` 默认 + 429/5xx 指数退避 ×3 + `BudgetExceeded` + `llm_calls` 审计 + logs/llm/ 落盘 |
| M1-4 score 阶段 | `bfa6893` | cheap 档评分 + JSON 解析 + 字段写入同事务 + 每日 top-N 选 selected |

**真实冒烟**（M1-3 / M1-4）：mock LLM 全绿；真实 LLM provider 决策 deferred 到 M2-1.5（用户提 MiniMax 但未拍 B，Mock 走通）。

---

## M2 — 创作与门禁（系统灵魂）

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M2-1 canonical 长文 | `8e42670` | 两段式 LLM 创作（大纲+核心观点 → 成文）+ 源文 httpx 抓取 + tmp→rename 幂等 + 防幻觉条款移植 |
| M2-2 质量门禁 | `7de222b` + `63154d4` + `6bd4568` | critic 批判→rewrite→scorer 三步 + TrendPublish shouldAcceptArticleRevision 四层防御（新增 blocker / allowPublish 降级 / action 等级提升即采纳 / 持平比分数）+ JSON 失败自动重试 + MiniMaxProvider 接入 |
| M2-3 派生格式 | `98a49de` | toutiao 短文 / 小红书 5-7 张 slide+caption+tags / X 5-10 条英文 thread；formats 字段合并语义不覆盖 |
| M2-4 图卡渲染 | `3159fae` | Jinja2 + Playwright 1080×1440，3 类型差异化（cover 深蓝→红渐变 / content 米底红线 / action 深蓝金），5 张样例图入 `docs/samples/` |
| M2-5 审核清单 | `da805dc` | REVIEW.md 生成 + reader（防模板占位误判）+ IM 通知（webhook 失败仅 warn 不阻断）+ 关键 bug：regex 含下划线 / 模板占位防线 / reject 必须有非空理由 |

**M2-4.5 配图 baoyu 集成**：留 Backlog 子任务，集成前需复核 HEAD CLI 签名。

**真实冒烟**（M2-1 / M2-2 冒烟一 + M2-2.5 冒烟二）：
- 第一轮（MiniMaxProvider 接入后）：ingest 38 → score 5 selected → create 3/5 ok（2 fail outline JSON 结构性错误）→ gate 3 discarded（critic 抓 fact blocker 验证门禁真在工作）
- 第二轮（JSON 自动重试后）：ingest 38 → score 5 selected → **create 5/5 ok** → gate 4 discarded + 1 failed

**未做**：M2-2 门禁排序 Spearman>0.6 验证（需用户提供 10 篇质量参差样文 + 人工打标）。

---

## M3 — 排期与调度

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M3-1 scheduler 错峰排期 | `94f7798` | 纯函数 `plan()` + sha256(content_id\|platform) 种子可复现 + 窗口内随机取点 20 次/窗口/日 + 顺延最多 14 天 + UTC 存 + 本地展示 |
| M3-2 launchd 定时化 | `5c7dcdb` | 11 个子命令的 flock 装饰器（`SKIP (lock held)` exit 0）+ 7 个 launchd plist + 3 个 scripts（install_launchd / install_cron / backup_db）|
| M3-3 Web 控制台 v1 | `ba7310b` | FastAPI + Jinja2 + htmx，TECH_SPEC §7 全部 11 路由齐全：Dashboard / 选题池（promote/reject）/ 审核台（approve/reject + reason 写 gate_verdict）/ 发布日历（reschedule/cancel/retry）/ 内容详情 / settings / api/status |

**真实冒烟**：
- M3-1：c_smoke_deriv1 → x/toutiao/xiaohongshu 三平台各 1 条 queued，UTC 换算到本地全部落在黄金时段窗口内；二次跑 0 scheduled, 3 skipped, 0 failed（UNIQUE 冲突语义修正）
- M3-2：父进程 acquire(locks/status.lock) → 子进程 `pipeline.run status` 输出 `status: SKIP (lock held)` + rc=0

---

## M4 — 发布通道（最脆弱部分）

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M4-0 决策复核 | `8537b76` | 5 项 DECISION 全部 CONFIRMED（TrendPublish / XiaohongshuSkills / AiToEarn / Pixelle-Video / baoyu-skills）|
| M4-1 发布安全框架 | `cf10241` | `safe_publish.py` 三层防御（config 锁 + 乐观锁 + UNIQUE）+ INTENT 日志 + publishing 超时 30min → failed + 'manual check needed'（绝不自动重试）|
| M4-2 X Publisher | `e1a2c82` + `70c7c7f` | `XApiPublisher`（OAuth2 bearer + 链式 thread + mid-failure partial 含平台 URL 给人工删帖用）|
| M4-3 头条 + 小红书 | `d48da9e` + `f4d86e5` | ToutiaoPublisher（自写 Playwright + 选择器防腐层 `toutiao_selectors.py`）+ XiaohongshuPublisher（subprocess 封装 XiaohongshuSkills，pin commit 2026-05-21）+ cookie_health 共享 + `pipeline.run login` 命令 |
| M4-4 Web 控制台 v2 | `6e44db9` | 周视图日历（htmx 换周，7 列日格）+ settings cookie 健康状态（不实际探活避免 hang）|

**Linux 真实端到端**（M4-3 commit f4d86e5）：
- 修正 M0-0 评估误差——XiaohongshuSkills 实际是 Python 脚本（不是 bun/TS），CLI 改用 `python scripts/publish_pipeline.py --title ... --content-file ... --images ... --headless --account ...`；tags 嵌入 content 最后一行 `#t1 #t2`
- fake_toutiao_server.py 模拟 mp.toutiao.com 最小子集 + 真启 chromium 跑 ToutiaoPublisher.publish() → 断言 mid 提取 + LoginExpired 检测命中
- 2 个真 e2e 测试（test_real_publish_end_to_end / test_real_health_probe_via_check_health_detects_login_page）端到端跑通

**未做（用户上线前必做）**：
1. `git clone https://github.com/white0dew/XiaohongshuSkills ~/.agents/skills/xiaohongshu-skills`
2. `python -m pipeline.run login xiaohongshu main` 扫码
3. `python -m pipeline.run login toutiao main` 扫码
4. 测试账号连发 3 天验收（HARD_PARTS §2 验证法）

---

## M5 — 视频管线

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M5-1 MPT 客户端 | `3463f3b` | `MPTEngine`（submit/poll/fetch + run_to_completion 一站式；poll 单次失败重试一次；超时 20min → CreateError）+ `derive_video_script`（canonical → LLM → {script, keywords, duration_s, hook_score} 60-90s 钩子前置 + 关键词强制英文）|
| M5-2 抖音视频发布 | `d31bf2f` | `DouyinPublisher`（Playwright 自写）+ `douyin_selectors.py` 防腐层 + **强制 AI 标识**（publish 时必勾「内容含 AI 生成」+ 选占比，找不到勾选框直接抛 PublishError 不静默忽略；ai_ratio 构造时校验只接受 low/medium/high）|
| M5-3 Pixelle-Video 第二引擎 | `731cc75` | `PixelleEngine`（submit/poll/fetch + run_to_completion，**mode="fixed" 跳过 Pixelle LLM 写稿（文案主权）**；title 走 req.style["title"] 注入；aspect → frame_template 映射；text 双换行分段 = 分镜边界；progress 强制 None；**404 → CreateError("task lost") 让编排层立即重提交（不静默重试）**；COMPLETION_TTL_HOURS=24 警示）|

**真实冒烟**（M5-1）：uvicorn 子进程 fake MPT → 真 httpx → submit/poll/fetch → mp4 magic 字节验证。
**真实冒烟**（M5-2）：fake creator.douyin.com Playwright 真跑 publish 全流程 → 上传视频 → 填标题 → **勾 AI 标识** → 提交 → video_id 提取 → ai_checked=true 留档。

**未做**：
- M5-1 docker-compose 部署 MPT + 真账号 Pexels key
- M5-2 真抖音账号连发 3 条 AI 标识必勾验证
- M5-3 docker-compose 部署 Pixelle-Video + 真账号 DashScope key
- AIGCPanel 数字人速评留 Backlog（M5-3 时间盒外）

---

## M6 — 数据回流与优化

| 任务 | 关键 commit | 核心产出 |
|------|------------|----------|
| M6-1 collect | `fe94a04` | 4 个 collector（X API v2 + 头条/抖音 Playwright 创作者后台 + 小红书占位）+ runner 编排 + 401/403/429/网络异常静默返回 None 不阻断其他 publication |
| M6-2 周报 | `8339f4d` | `collect_weekly_report` + `render_markdown` + `write_weekly_report`，4 段：概览 / 各平台 top3+bottom3 / LLM 成本按 stage / 门禁校准（ASCII 直方图 + Pearson r，\|r\|<0.2 提示重新校准锚点）|
| M6-3 免审直发 | ⏸️ 未启动 | 前置：过去 30 天人审通过率 ≥ 85% 且无平台违规记录（开发环境无真实运营数据）|

**M6 集成 TODO**（用户上线前）：
- 配置 launchd/cron 每周一自动跑 `python -m pipeline.run report weekly`
- 头条/抖音/小红书的真实后台页面改版时修订 `_parse_*_manage_html` 启发式（M6-1）
- 抖音视频发布时间 1h 内的发布应在 24h 后才有足够数据（M6-1）

---

## 跳过的真实环境验证（用户上线前必做）

| 任务 | 缺失的验证 | 依赖 |
|------|-----------|------|
| M2-2 | 门禁排序与人工排序 Spearman > 0.6（HARD_PARTS §3）| 10 篇质量参差样文 + 人工打标 |
| M4-2 | 真实 X 账号发 thread | X Developer Portal 手动建 OAuth2 App |
| M4-3 | 三平台（X+头条+小红书）连发 3 天（HARD_PARTS §2）| 测试账号扫码登录 |
| M5-1 | 真实 mp4 质量验收 | MPT Docker + Pexels key |
| M5-2 | 抖音连发 3 条 AI 标识验证 | 真实抖音账号 |
| M5-3 | Pixelle-Video 真实 mp4 质量验收 | Pixelle Docker + DashScope key |
| M6-3 | 免审直发 | 30 天真实发布数据 + 人审通过率 |

---

## Backlog 状态

8 项已登记，**全部不排期**——按需激活：

1. 公众号 Publisher（M0-0 决策中的预留任务，移植 TrendPublish 微信排版/审稿协议）
2. Postiz 部署（YouTube Shorts / TikTok）
3. 表现数据反哺选题权重（metrics → topics 评分 prompt）
4. 多账号矩阵（同平台第二账号 = 不同支柱人设）
5. 英文内容线（Medium / dev.to）
6. n8n 迁移（launchd 管理复杂度超阈值时）
7. 数字人口播 lane（AIGCPanel，M5-3 速评留 Backlog）
8. OpenMontage 精品视频 lane（M5-3 决策降级：Pixelle-Video 已接管精品定位）

详见 [TASKS.md](./TASKS.md) 末尾"后续 Backlog（不排期）"节。
