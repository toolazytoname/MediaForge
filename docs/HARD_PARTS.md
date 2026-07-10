# HARD_PARTS — 难点攻坚与实施注意事项

> 版本 v1.1 | 2026-07-06
> 架构师预判的坑。**实现每个任务前，先查本文件有无对应章节。** 每个难点给出：为什么难、决策、实现要点、验证方法。

---

## §1 防重复发布（全系统最高优先级正确性问题）

**为什么难**：发布是不可逆动作。cron 重叠执行、进程中途被 kill、平台返回超时但实际发布成功——这三种情况都会导致重复发帖，矩阵账号重复内容 = 风控封号。

**决策**：数据库乐观锁 + 唯一约束 + 意图日志，三层防御。

**实现要点**：
1. 取任务时原子抢占：`UPDATE publications SET status='publishing', updated_at=? WHERE id=? AND status='queued'`，检查 `rowcount==1` 才继续，否则说明另一进程已抢走
2. `UNIQUE(content_id, platform, account_id)` 数据库层兜底
3. **先写意图再发布**：调用平台接口前先落一条日志 `INTENT publish p_xxx`；如果进程死在发布后落库前，重启时发现 `publishing` 状态超过 30 分钟的记录 → 标记 `failed` 并告警**人工核实平台上是否已发出**，绝不自动重试
4. cron 层再加一把文件锁（`flock`）：同一子命令不并发

**验证**：并发跑两个 `publish` 进程处理同一条 queued 记录，断言只有一个成功抢占。

---

## §2 国内平台 Playwright 发布的登录态与风控

**为什么难**：头条/小红书/抖音无个人开发者友好的官方发布 API，只能走浏览器自动化。cookie 会过期；平台检测自动化特征；页面结构随时改版。这是全系统**最脆弱**的部分，必须按"随时会坏"来设计。

**决策**：复用 social-auto-upload 的登录态方案（Playwright storage_state json），但发布器实现在我们自己的 `PublisherAdapter` 里包一层防腐层；把"坏了能立刻知道 + 半小时内能修"作为设计目标，而不是"永远不坏"。

**实现要点**：
1. **登录态获取**：提供 `python -m pipeline.run login <platform> <account>` 命令，打开有头浏览器让人扫码，成功后保存 `secrets/cookies/<platform>_<account>.json`。cookie 由人工触发刷新，系统只负责检测失效
2. **失效检测先行**：publish 前先用 cookie 访问个人主页做轻量校验，失效 → 该平台所有任务标 `failed(login_expired)` + IM 告警，**不要带着失效 cookie 反复撞**（触发风控）
3. **反检测基线**：`playwright-stealth`、真实 UA、固定 viewport、每账号独立 browser profile 目录、操作间随机 sleep 1-3s。不要追求更黑的对抗手段（收益递减且有封号风险）
4. **选择器防腐**：所有页面选择器集中在 `publishers/<platform>_selectors.py` 一个文件，页面改版只修一处；每个选择器配 fallback 与清晰的报错（"找不到发布按钮，页面可能已改版"）
5. **慢即是快**：发布全程 headless=False 也要能跑（调试模式）；每步截图存 `logs/screenshots/` 供事后排障
6. **频控**：单账号单平台每天 ≤ 3 帖（config），两帖间隔 ≥ 4h。宁可排到明天，不可当天硬塞

**验证**：无法完全自动化测试。M4 验收 = 用测试账号真实发布 3 天，每天检查是否有重复/失败/风控提示。

---

## §3 质量门禁的有效性（防"AI 给 AI 打高分"）

**为什么难**：LLM 评自己生成的内容普遍偏高分（自我偏袒）；评分 prompt 写不好会所有内容都 25-27 分，门禁失去区分度；这直接决定系统是"内容工厂"还是"垃圾工厂"。

**决策**：评分与创作用不同模型上下文 + 校准锚点样例 + 分布监控。

**实现要点**：
1. **隔离**：评分调用不带创作过程的任何上下文，只给最终稿 + 评分标准
2. **锚点校准**：`gate/anchors/` 放 6 篇人工标注的样例（2 篇 9 分级、2 篇 6 分级、2 篇 3 分级），评分 prompt 里带锚点摘要，要求"先对比锚点再打分"
3. **评分 prompt 结构**：先让模型列出"这篇最大的三个问题"再打分（强制批判先行，对抗好好先生倾向）；输出严格 JSON `{"info": n, "fun": n, "view": n, "problems": [...], "verdict": "..."}`
4. **分布监控**：`status` 命令输出最近 30 天门禁分数直方图；若 90% 内容都过/都不过 → 门禁失效，需要重新校准锚点
5. **人审数据回喂**：人打回的内容记录原因标签，每月人工 review 一次"门禁放过但人打回"的 case，更新锚点
6. 重写最多 1 轮（config `gate.max_rewrites`），防止无限循环烧钱

**验证**：拿 10 篇已知质量参差的文章（好中差各若干）跑门禁，断言排序与人工判断的 Spearman 相关 > 0.6。

---

## §4 LLM 成本失控防护

**为什么难**：全自动系统 = 没有人盯着账单。一个 bug（比如重试风暴、门禁循环）可能一夜烧掉一个月预算。

**决策**：单一入口 + 硬预算 + 审计表（TECH_SPEC §5.3 的 `complete()` 契约）。

**实现要点**：
1. 所有 LLM 调用必须走 `creators/llm.py::complete()`——CI 加检查：`grep -r "import anthropic" pipeline/ | grep -v llm.py` 必须为空
2. 每次调用后累计当月成本（查 `llm_calls` 表 sum），超 `budget.monthly_usd` 抛 `BudgetExceeded`，编排层收到后停止当日流水线 + IM 告警
3. 分级用模型：score 用 cheap 档（Haiku），create/gate 用 creative/critical 档（Sonnet）。**不要**全用最贵的
4. 单次调用 max_tokens 上限硬编码 8192，防 prompt bug 导致天量输出
5. 每周报表含成本行：本周 LLM 花费 / 每篇过审内容的平均成本

**验证**：mock 场景下把 monthly_usd 设为 0.01，断言第二次调用抛 BudgetExceeded。

---

## §5 幂等性实现模式（每个阶段通用）

**为什么难**："可安全重跑"说起来容易，每个阶段的幂等点不同，弱模型实现时容易漏。

**决策**：统一模式——**按状态取件、处理、原子转移状态；处理产物先写临时名、成功后 rename**。

各阶段幂等点速查：

| 阶段 | 幂等保证 |
|------|----------|
| ingest | content_hash UNIQUE，重复 INSERT OR IGNORE |
| score | 只取 status=raw；评分写入与状态转移同一事务 |
| create | 只取 status=selected；输出目录先写 `t_xxx.tmp/`，全部成功后 rename 为 `t_xxx/` 再转状态；重跑时发现 `.tmp` 目录直接删除重来 |
| gate | 只取 status=draft；评分落库与状态转移同一事务 |
| review | 读 REVIEW.md 时只处理数据库中仍为 gated 的条目（人重复标记无副作用） |
| schedule | 只取 approved 且无 publication 记录的 content；INSERT 带 UNIQUE 约束 |
| publish | §1 的三层防御 |
| collect | metrics 表允许多次快照（时间序列），天然幂等 |

**验证**：TECH_SPEC §9 的必测清单——每阶段跑两遍 = 跑一遍。

---

## §6 MoneyPrinterTurbo 集成（视频管线）

**为什么难**：MPT 是独立服务（FastAPI + 任务队列），生成一条视频 2-10 分钟且可能失败；素材依赖 Pexels API（免费但有 key 和限额）；中文 TTS 音色选择影响成品质感。

**决策**：M5 才做视频（图文闭环先跑通）。MPT 以 Docker 起在本机 8080，我们只写一个薄客户端。

**实现要点**：
1. `creators/video.py`：POST /api/v1/videos 提交任务 → 轮询 GET 任务状态（间隔 30s，超时 20min）→ 下载成品到 output 目录
2. 脚本先行：我们自己的 LLM 先产出口播稿（canonical 派生），传给 MPT 的 `video_script` 参数——**不要**让 MPT 自己生成文案（质量不过我们的门禁）
3. TTS 用 edge-tts 免费方案起步，音色固定写 config（`zh-CN-YunxiNeural` 之类），A/B 后再定
4. 素材：Pexels key 注册免费；关键词由 LLM 从脚本提取（英文关键词，Pexels 中文搜索很差）
5. MPT 服务挂了 → `CreateError`，该 content 的视频格式标记 failed，图文格式不受影响（格式级隔离）
6. 版本锁定：docker-compose 里 pin 具体 image tag，MPT 升级手动验证后再动

**验证**：单独脚本 `scripts/test_mpt.py` 提交一条测试视频端到端跑通。

---

## §7 备选方案登记（当前选型失效时的 Plan B）

| 组件 | 当前选型 | Plan B | 切换触发条件 |
|------|----------|--------|--------------|
| 国内发布（小红书） | XiaohongshuSkills（M4-3 已实装，subprocess 集成，pin commit 2026-05-21） | 自写 Playwright（patchright，参考 social-auto-upload 新版 uploader + AiToEarn electron 遗留代码） | mac 冒烟不通过，或连续 1 周失败率 > 30%，或项目停更 |
| 国内发布（头条/抖音） | 自写 Playwright（M4-3 头条 / M5-2 抖音已实装；AiToEarn/xhs-toolkit M0-0 评估放弃） | AiToEarn 仅海外平台重评 | — |
| 公众号图文 | 自研 lane + 官方 API 草稿箱（M0-0 决策：M2-2 已移植审稿协议/防幻觉条款；M0-0 不部署 TrendPublish；Backlog 待激活） | TrendPublish CLI dry-run 作对照产线 | 自研排版质量不达标 |
| 海外发布 | X 官方 API（M4-2 已实装） | Postiz 自托管（一次接入 YouTube/TikTok/IG 等） | 扩到 ≥ 3 个海外平台时直接上 Postiz |
| 国内发布（图文，B 路线扩展） | **MultiPost 浏览器扩展（Apache-2.0，RESTful API 触发；M11-0 决策，M11-E 集成中）** | 现有自写 headless（A 路线）降级 ‖ 参考 Wechatsync CLI/MCP（GPL-3.0，仅进程外调用不 vendor）作博客长尾 | 扩展桥不通 / 浏览器需常开不可接受 → 退 A 路线 |
| 国内发布（视频，B 路线扩展） | **MultiPost 视频扩展（半自动、真人会话、风控最低；M11-E）** | MPP（social-auto-upload 系，Playwright 无人值守，MIT）‖ 现有 headless | 需无人值守量产 → 上 MPP/headless（风控高，接受降级） |
| 无人值守兜底（A 路线 headless） | 现有自写 Playwright（M4-3/M5-2） | **移植 MPP `platform_configs.py` 配置化架构（MIT，加平台=改配置）** | 自写 headless 加平台成本过高时移植 MPP 架构 |
| 视频生成（量产） | MoneyPrinterTurbo（M5-1 已实装，工厂降级） | NarratoAI（解说类）/ 直接 ffmpeg + edge-tts 自拼 | MPT 停更或质量不满意 |
| 视频生成（精品/AI 生成类） | Pixelle-Video（M5-3 已实装为 VideoEngine 第二引擎，mode=fixed 注入我方脚本，404 重提交） | OpenMontage（远期观察）/ 人工 + Remotion | 生图成本失控或项目停更 |
| 数字人 | AIGCPanel（M5-3 缩减为速评后留 Backlog） | HeyGen 等商业 API | 本地部署质量/性能不达标 + 账号过带货门槛 + 平台虚拟人报备完成 |
| 热点源 | RSS + DailyHotApi 自部署 | newsnow 自部署 | DailyHotApi 接口挂 |
| 图像生成 | 不用（模板渲染兜底）；provider=none；可选 baoyu-image-gen subprocess（M2-4.5 子任务待激活，11 provider，见 evaluation-notes §5） | Gemini/OpenAI 图像 API | 模板卡片视觉疲劳、数据表明配图影响点击；或 baoyu-image-gen 升级破坏 CLI 签名（真集成时复核 HEAD） |

> 巨人肩膀原则：每个垂直环节动工前先查本表和 opensource-survey.md——**默认假设已有人造过这个轮子**。发现新的成熟项目 → 更新调研文档并在对应任务下记录，宁可多花 2 小时评估也不自写 2 周。

---

## §8 时区与调度细节

- 存储一律 UTC，**排期计算在本地时区**（平台黄金时段是本地概念），config 里 `timezone: Asia/Shanghai`
- 定时驱动按平台二选一：
  - macOS：launchd plist（睡眠错过的任务会在唤醒后补跑）；`launchd/` 目录放 plist 模板，`scripts/install_launchd.sh` 幂等安装
  - Linux：cron + 每个子命令入口的 `flock` 装饰器（`scripts/install_cron.sh` 备选安装）
- 发布时间随机化必须用 `random.Random(seed=sha256(content_id|platform))` —— 可复现（重跑 schedule 不会改变已排时间）
- cron 重叠防护：每个子命令启动时 `flock` 锁文件 `locks/<stage>.lock`，拿不到锁直接 `exit 0` 打印 `SKIP (lock held)`（HARD_PARTS §5）

---

## §9 凭据与安全清单

- `secrets/` 整目录 gitignore；`config.yaml` 也 gitignore（含 webhook 等），只提交 `config.example.yaml`
- Anthropic key 从环境变量 `ANTHROPIC_API_KEY` 读，不进 config 文件
- cookie 文件权限 `chmod 600`
- IM 通知内容不包含 cookie/token/完整错误堆栈（可能含敏感 header）
- 定期备份：`state.db` 每日 launchd 任务备份到 `backups/`（保留 14 天）——SQLite 单文件是唯一的真相源，丢了 = 全部发布历史丢失

---

## §10 给实现者（弱模型）的通用告诫

1. **不要发明契约之外的字段/状态/方法**。想加 → TASKS.md 记录，停下来
2. **不要在一个任务里"顺手"重构别的模块**
3. **不要 mock 掉正在测的东西**（mock LLM 可以，mock 状态机就是自欺）
4. **拿不准平台页面结构时**，用 `login` 命令开有头浏览器人工看一次，不要猜选择器
5. **每个任务的验收标准是唯一完成定义**——测试全绿 ≠ 完成，验收标准满足才是完成
6. 遇到本文档未覆盖的设计决策 → 在 TASKS.md 该任务下写 `⚠️ DECISION NEEDED: <问题>` 并停止该任务
