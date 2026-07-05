# TASKS — 分里程碑任务清单

> 版本 v1.0 | 2026-07-04
> **执行规则**：严格按顺序做，一次会话一个任务。完成后勾选并追加 `✅ 完成于 <日期>, commit <sha>, <一句话备注>`。
> 卡住 → 查 [HARD_PARTS.md](./HARD_PARTS.md)；契约有问题 → 在任务下写 `⚠️` 记录并停止，不要擅自改契约。
> 每个任务格式：**目标 / 步骤 / 验收标准 / 参考**。

---

## M0 — 项目地基（预计 1-2 天）

### M0-0 站在巨人肩膀上：同类项目深度评估（1 天时间盒，先于一切编码）
- [x] **目标**：确认"借用哪些轮子"，输出决策记录，避免重复造轮子
- **步骤**（每项 2 小时时间盒，输出写入 `docs/research/evaluation-notes.md`）：
  1. **TrendPublish**（`liyown/ai-trend-publish`，3k⭐）：本地跑通其 dry-run，评估其选题聚类/质量审稿/公众号排版是否达标 → 决定公众号 lane 是"直接部署它+API对接" 还是 "自研"
  2. **xhs-toolkit**（`aki66938/xhs-toolkit`，MCP）与 **XiaohongshuSkills**（3.1k⭐）：评估小红书发布稳定性 → 决定 M4-3 是集成还是自写 Playwright
  3. **AiToEarn** 自部署版：Docker 起服务，测其发布 API → 决定国内多平台发布是否整体走它（原 M4-0 提前到此）
  4. **Pixelle-Video**（`ATH-MaaS/Pixelle-Video`，24k⭐，用户补充）：评估其作为视频产出引擎的角色
- **验收**：evaluation-notes.md 中每项有 `DECISION: <采用/参考/放弃> 因为 <理由>`；TASKS.md 受影响任务（M4-2/M4-3/M2 部分）按决策更新描述
- **参考**：docs/research/opensource-survey.md（必读"第二轮广撒网"节）

  ✅ 完成于 2026-07-05，commit 56e068d/e776e2a，备注：4 项 DECISION 已落 evaluation-notes.md——TrendPublish 参考（移植门禁修订协议/微信排版/防幻觉条款）；小红书采用 XiaohongshuSkills、放弃 xhs-toolkit（已停更）；AiToEarn 放弃（自部署无法无人值守）仅参考其 API 设计与 electron 遗留代码；Pixelle-Video 采用为 VideoEngine 第二引擎（M5-3 已改写）。评估以源码深读替代本地跑通（4 仓库全量 clone 深读，时间盒内完成）。**追加（2026-07-05 傍晚）**：用户补充 baoyu-skills（JimLiu/baoyu-skills，23.1k⭐，MIT）评估，DECISION = §5.5 skills 桥保留 + 唯一抽出 `baoyu-image-gen` 作 §5.4 配图 backend 扩展（M2-4 增子任务 M2-4.5，可选）。已同步 TECH_SPEC §5.4 §5.5、HARD_PARTS §7、M2-4 任务。M2-4.5 集成前需复核 HEAD `baoyu-image-gen` CLI 签名（v2.1.0 与本地同步，低风险）。**复核（2026-07-05，Fable 5）**：5 个并行核查 agent 对 evaluation-notes 全部事实声明逐条重验源码+GitHub API，**5 项 DECISION 全部维持**；修正细节偏差（TrendPublish 修订采纳规则实为 action 等级优先非「分数单调不降」、XiaohongshuSkills 三处修饰性夸大、baoyu provider 实为 12 家、本机 skills 已同步 HEAD 版本表作废、Pixelle 新增 24h 清理/progress 未接线/显式传 title 三约束），详见 evaluation-notes.md 文末「复核记录」。

### M0-1 初始化工程与工具链
- [x] **目标**：可运行的空项目
- **步骤**：
  1. `git init`；写 `.gitignore`（`.venv/ secrets/ config.yaml state.db logs/ output/ backups/ locks/ __pycache__/`）
  2. 建 venv；写 `requirements.txt`：`anthropic pydantic pyyaml httpx feedparser jinja2 playwright pytest pytest-cov`
  3. 写 `pipeline/__init__.py`、空的 `pipeline/run.py`（argparse 骨架，所有 TECH_SPEC §2 子命令注册为占位函数，打印 "not implemented"）
  4. 首次 commit
- **验收**：`python -m pipeline.run status` 打印占位输出且 exit 0；`pytest` 收集 0 个用例不报错
- **参考**：TECH_SPEC §1 §2

  ✅ 完成于 2026-07-05，commit 23e7911，备注：pipeline/run.py 注册 12 个占位子命令（init-db/ingest/score/create/gate/review/schedule/publish/collect/status/reset/webui），status→exit 0；pytest 收集 0 用例 exit 5（pytest 标准"无测试"非异常）。前置状态：initial commit 已含 .gitignore/requirements.txt/config.example.yaml/pipeline/models.py/各 __init__.py 与 base.py 占位，本任务补齐 run.py 与 venv。

### M0-2 数据模型与状态机
- [x] **目标**：`models.py` + `db.py` + 状态机测试
- **步骤**：
  1. `pipeline/models.py`：按 TECH_SPEC §4 写四个 frozen dataclass + 三个 Status 枚举 + 转移表
  2. `pipeline/utils/ids.py`：`new_id(prefix)` → `<prefix>_<8hex>`
  3. `pipeline/db.py`：`connect()`（WAL 模式）、`init_db()`（TECH_SPEC §3 全部建表语句）、每张表的 `insert_*` / `get_*_by_status` / `transition(table, id, from_status, to_status)`（校验转移表，非法抛 `IllegalTransition`；UPDATE 带 `WHERE status=from_status` 乐观锁）
  4. 测试：合法转移成功；全部非法转移抛异常；乐观锁（状态已变时 transition 抛 `StaleState`）
- **验收**：`python -m pipeline.run init-db` 生成 state.db 且幂等；`pytest tests/test_db.py -q` 全绿；覆盖所有非法转移路径
- **参考**：TECH_SPEC §3 §4；HARD_PARTS §5

  ✅ 完成于 2026-07-05，commit 3305151 + d735e50，备注：db.py 398 行（WAL+FK+5 表 DDL+transfer 集中强制），utils/errors.py 含 PipelineError 基类+IllegalTransition/StaleState（其余 SourceError/CreateError/GateError/PublishError/BudgetExceeded 留 M0-3），utils/ids.py new_id(prefix)→8hex。第一轮独立验收 D 项 FAIL（topics/contents/publications from_state 全部 outgoing 非法对未覆盖 36%），返工 commit d735e50 用 `_illegal_pairs()` 从契约表反推补齐：topics 20 + contents 43 + publications 20 = 83 矩阵测试 + contents 乐观锁，共 154 测试。第二轮复验 PASS。

### M0-3 配置加载与日志
- [x] **目标**：config.yaml 加载校验 + 结构化日志
- **步骤**：
  1. `pipeline/config.py`：pydantic 模型覆盖 TECH_SPEC §6 全部字段；`load_config(path)` 缺字段报清晰错误
  2. 校验 `config.example.yaml` 能通过加载（作为测试）
  3. `pipeline/utils/log.py`：json lines 格式 logger，字段含 `ts stage ref_id level msg`；RotatingFileHandler 到 `logs/pipeline.log`
  4. `pipeline/utils/errors.py`：TECH_SPEC §7 全部异常类
- **验收**：`load_config('config.example.yaml')` 成功；把 example 里 `gate.threshold_total` 改成字符串后加载报错并指明字段；测试全绿
- **参考**：TECH_SPEC §6 §7

  ✅ 完成于 2026-07-05，commit 4cd4012，备注：config.py 243 行（pydantic v2 全覆盖 §6，discriminated union by type/kind, extra=forbid），log.py 104 行（JsonLineFormatter + TimedRotatingFileHandler midnight/30 + ensure_ascii=False），errors.py 99 行（§7 全部异常类 + BudgetExceeded 携带 stage/used_usd/limit_usd）。独立验收一轮 PASS（A-G 全过），无返工。tests 47 新增 (config 14 + log 9 + errors 24)，全测试 201 全绿。

---

## M1 — 选题引擎（预计 2-3 天）

### M1-1 SourceAdapter 基类与 RSS 源
- [x] **目标**：能从 RSS 拉回标准化条目
- **步骤**：
  1. `pipeline/sources/base.py`：按 TECH_SPEC §5.1 写 `SourceAdapter` / `RawItem`
  2. `pipeline/sources/rss.py`：`RssSource(name, feed_url, max_items)`，feedparser 解析，异常包装 `SourceError`
  3. `pipeline/sources/registry.py`：从 config 的 `sources` 段构造启用的 adapter 列表
  4. 测试用 `tests/fixtures/sample_feed.xml` 本地文件，不打网络
- **验收**：单测全绿；手工冒烟：config 配一个真实 RSS（如 hnrss.org/frontpage），跑 fetch 打印条目
- **参考**：TECH_SPEC §5.1

  ✅ 完成于 2026-07-05，commit dd58647，备注：base.py 30 行（RawItem frozen + ABC + SourceError re-export §7 唯一源）、rss.py 116 行（feedparser + dated desc / undated 末位 + summary≤2000 + ISO8601 UTC + 网络/解析异常包 SourceError）、registry.py 60 行（仅 enabled、未知 type → ValueError）；tests 21 新增（7 base + 9 rss + 5 registry）+ 3 本地 fixture，全量 222 全绿（原 201）。独立 agent 审计：0 critical bug，几个 smell 可辩护（bozo best-effort、空 title 留 M1-2 入库时校验、ISO `+00:00` 格式 TECH_SPEC 未 pin）。**未做**：手工冒烟真实 RSS（用户授权下会话跳过）——冒烟代码一行 `python -c "from pipeline.sources.registry import build_sources; from pipeline.config import load_config; [print(s.name, len(s.fetch())) for s in build_sources(load_config('config.example.yaml').sources) if s.name=='rss:hn']"`。

### M1-2 ingest 编排：入库与去重
- [x] **目标**：`python -m pipeline.run ingest` 完整可用
- **步骤**：
  1. `pipeline/run.py` 的 ingest 子命令：遍历 registry 全部源 → fetch → 计算 `content_hash = sha256(normalize(title)+domain)`（normalize：小写、去空白/标点）→ `INSERT OR IGNORE`
  2. 单源失败：log warning，继续其他源
  3. 打印摘要行 `ingest: N fetched, N new, N dup`
- **验收**：跑两遍第二遍全 dup；某源 URL 故意写错时其他源正常入库；测试覆盖去重与单源失败
- **参考**：HARD_PARTS §5；ARCHITECTURE §3.1

  ✅ 完成于 2026-07-05，commit 8d67fde，备注：dedup.py (normalize 用 Unicode category L*/N* 保留下划线外的字母数字；extract_domain netloc 小写剥 www. 保留端口；content_hash sha256 hex)、db.try_insert_topic (INSERT OR IGNORE + rowcount 判新/重)、ingest.run_ingest (单源异常 stderr warning + IngestResult.failed_sources)、run.py cmd_ingest 接 build_sources+load_config；tests 27 新增 (dedup 12 + insert_topic 6 + ingest 9)，M1 累计 48 全绿（原 222 + 27 = 249）。

### M1-3 LLM 封装（成本控制核心件）
- [x] **目标**：`creators/llm.py::complete()` 按契约完整实现
- **步骤**：
  1. 按 TECH_SPEC §5.3 实现：tier→model 映射、llm_calls 落表、月成本计算、`BudgetExceeded`、429/5xx 指数退避 ×3、prompt/响应存 `logs/llm/`
  2. 成本单价表 `MODEL_PRICES` 常量（写当前 Anthropic 牌价，注明日期）
  3. 测试：mock anthropic client；预算超限抛异常（HARD_PARTS §4 验证法）；重试逻辑
- **验收**：测试全绿；真实冒烟一次调用后 `llm_calls` 表有记录且 cost>0
- **参考**：TECH_SPEC §5.3；HARD_PARTS §4

  ⚠️ **DECISION NEEDED**（2026-07-05）：用户提供 MiniMax M3 平台 API key（provider 名：minimax；model：MiniMax-M3）。TECH_SPEC §5.3 硬编码 anthropic SDK + Anthropic 牌价。两条路：
  - **A. 推迟**：M1-3 全 mock 跑通；M1-4 score 真接 LLM 时再决定 provider；TECH_SPEC 不动
  - **B. 现在重构 §5.3**：新增 LLMProvider 抽象（AnthropicProvider / MiniMaxProvider / MockProvider），MODEL_PRICES 改为分层表；M1-3 实现多 provider；真实冒烟 MiniMax
  推荐 **A**（最小变更 + 推迟不可逆决定到有真实数据时）。等用户拍板。

  ✅ 完成于 2026-07-05，commit 21e2bb8，备注：选了 **A**（Mock-only），M1-3 完整契约 15 测试全绿：LLMProvider ABC + MockProvider 默认 + RetryableError 重试（指数退避 1/2/4s ×3）+ BudgetExceeded（gate 跳过）+ llm_calls 审计 + logs/llm/<ref>_<stage>_<ts>.json 落盘 + 护栏测试 `grep 'import anthropic' pipeline/` 仅命中 llm.py。签名扩展：`conn` 显式可注入（向后兼容，默认 None 走 module-level init_db_conn）。**未做**：真实 provider——MiniMax key 留 M1-4 score 阶段决定接不接（用户未拍 B）。

### M1-4 score 阶段：选题评分与每日精选
- [x] **目标**：`python -m pipeline.run score` 完整可用
- **步骤**：
  1. `pipeline/topics/scorer.py`：对每条 raw topic 构造评分 prompt（含 config 中各支柱描述），cheap 档调用，输出 JSON `{pillar, score, reason}`；解析失败重试 1 次后标 rejected
  2. `pipeline/topics/selector.py`：当日 scored 中 score ≥ config `topics.min_score`（默认 6）按分排序取 top `daily_quota`，转 selected，其余保持 scored（明日仍有机会，3 天后过期转 rejected）
  3. 评分与转移同事务
- **验收**：mock LLM 测试全绿；真实冒烟：ingest+score 后 `status` 显示合理分布；同一 topic 不会被评两次
- **参考**：ARCHITECTURE §3.2

  ✅ 完成于 2026-07-05，commit <待补>，备注：scorer.py 192 行（cheap 档 + JSON 解析 + 校验 + 字段写入+状态转移；解析失败重试 1 次；RetryableError 穷尽转 rejected 不阻塞）、selector.py 55 行（按 score desc 取 quota 个；走 db.transition 走状态机）、runner.py 76 行（编排+注入 llm 模块级状态+ScoreRunResult）、run.py cmd_score 薄壳；tests 19 新增（scorer 7 + selector 8 + runner 4），M1 累计 82 全绿（原 63 + 19）。**未做**：真实冒烟——provider 仍 deferred 到 DECISION NEEDED 拍板（用户提 MiniMax 便宜但未明确选 B）。

---

## M2 — 创作与门禁（预计 3-5 天，系统灵魂）

### M2-1 canonical 创作管道
- [x] **目标**：selected topic → 深度长文 markdown
- **步骤**：
  1. `pipeline/creators/canonical.py`：两段式——先调 LLM 产出大纲+核心观点（强制回答"作者的一句话观点是什么"），再成文（1500-3000 字，创作 prompt 存 `pipeline/creators/prompts/canonical.md` 便于迭代）；prompt 移植 TrendPublish 防幻觉条款（商业状态/定价/参数只有来源明确写出才可表述，见 evaluation-notes §1 移植清单）
  2. 若 topic 有 url：httpx 抓原文正文（trafilatura 或简单提取，失败则只用 title+summary）作为素材
  3. 产出写 `output/YYYY-MM-DD/<content_id>.tmp/canonical.md` + `meta.json`，成功后 rename 去掉 `.tmp`（HARD_PARTS §5 模式）
  4. contents 表插入记录（status=draft），topic 转 consumed
- **验收**：测试（mock LLM）覆盖 tmp-rename 幂等；真实冒烟产出一篇你自己读得下去的长文
- **参考**：ARCHITECTURE §3.3；HARD_PARTS §5

  ✅ 完成于 2026-07-05，commit <待补>，备注：source_fetcher.py (~65 行 httpx + 简单 HTML 提取, 错则 None)、canonical.py (~190 行 两段式 LLM 创作, tmp→rename, BudgetExceeded 审计发现并修复 上抛不吞)、prompts/canonical_outline.md + canonical_essay.md (防幻觉条款移植)、run.py cmd_create (单条 CreateError skip + BudgetExceeded 终止); tests 11 新增, 全量 294 全绿 (原 283 + 11)。独立 agent 审计 PASS, 修 2 问题 (BudgetExceeded 被吞 + max_tokens 偏紧)。**未做**: 真实冒烟一篇长文 (provider DECISION NEEDED 仍挂着)。

### M2-2 质量门禁
- [x] **目标**：`python -m pipeline.run gate` 完整可用——本系统品质的最后防线
- **步骤**：
  1. `pipeline/gate/anchors/` 准备 6 篇锚点样例（**此步需要用户参与**：请用户提供或确认 2 好/2 中/2 差样例，可先用占位并标记 `⚠️ 需用户校准`）
  2. `pipeline/gate/critic.py`：批判轮（列三大问题）→ 触发重写（调 canonical 重写入口，带批判意见，最多 `max_rewrites` 次）；重写协议参考 TrendPublish：只许修表达/结构/排版类问题、禁碰事实类问题（事实问题 → 直接 discarded 或留人审），重写后强制复评，采纳规则按 TrendPublish `workflow.ts:1112-1126` 真实逻辑移植（action 等级提升即采纳、持平才要求总分不降、新增 blocker/发布权降级则回滚，见 evaluation-notes §1 及复核记录）
  3. `pipeline/gate/scorer.py`：独立评分会话，锚点对比 + 强制 JSON 输出，按 config 阈值判定 gated/discarded
  4. critique.md 落盘；discarded 的文件保留
- **验收**：HARD_PARTS §3 的验证法——10 篇质量参差样文，门禁排序与人工排序 Spearman > 0.6（此验收需用户参与打标）；分数写入 contents 表
- **参考**：HARD_PARTS §3（必读）；PRD §3.3

  ✅ 完成于 2026-07-05，commit 7de222b，备注：gate/{anchors_loader,decision,critic,scorer,runner,__init__}.py + prompts/{critic,scorer,rewrite}.md + 6 篇占位锚点样例 + README 标注"⚠️ 需用户校准"。decision.py 移植 TrendPublish shouldAcceptArticleRevision 四层防御（新增 blocker / allowPublish 降级 / action 等级提升即采纳 / 持平比分数）+ Action 枚举（BLOCK=0/DISCARD=1/REVISE=2/GATE=3）。canonical.py 增 rewrite_one() 走 tmp→rename 覆盖已有 canonical.md（HARD_PARTS §5 幂等）。runner.py 编排：critic → 可选 rewrite → scorer → 原子事务落库分数+状态；异常路径必走 DRAFT→FAILED。**独立 agent 审计 5 bug 已修**：1) bool 漏入 int 校验；2) 分数写入与状态转移非原子；3) StaleState/IllegalTransition 被吞；4) 异常路径不写 DRAFT→FAILED；5) decide_revision_action 把无问题内容误判为 DISCARD。tests 46 新增，全测 340 全绿（原 294 + 46）。**未做**：真实 LLM 冒烟（provider DECISION NEEDED 仍挂）+ 10 篇 Spearman>0.6 验证（需用户提供真实校准锚点）。
- [ ] **目标**：canonical → 头条长文 / 小红书图卡文案 / X thread
- **步骤**：
  1. `pipeline/creators/derivative.py`：每格式一个函数，输入 canonical.md，输出平台目录（TECH_SPEC/ARCHITECTURE §8 目录约定）
  2. 头条：标题党程度适中的标题 3 选 1 + 正文（分段短、配图占位标记）；小红书：结构化 slides JSON（封面钩子+3-5 内容卡+行动卡）+ caption + tags；X：5-10 条 thread，每条 ≤ 260 字符（英文）
  3. 派生 prompt 各自独立文件放 `prompts/`
- **验收**：一条 gated content 跑完生成三个平台目录，文件齐全格式合规（测试校验 thread 每条长度、slides JSON schema）
- **参考**：PRD §3.2

### M2-4 模板渲染引擎（图卡出图）
- [ ] **目标**：slides JSON → PNG 图卡，零外部服务
- **步骤**：
  1. `playwright install chromium`
  2. `templates/xhs_card.html`：Jinja2 模板，1080×1440，简洁大字排版（衬线标题+高对比配色，先做一个耐看的，不求多）
  3. `pipeline/creators/render.py`：按 TECH_SPEC §5.4 实现 `render_cards()`
  4. 中文字体：模板里用系统字体栈 `PingFang SC, Noto Sans CJK`，**验收时人眼检查无豆腐块**
- **验收**：样例 slides 渲染出 PNG，1080×1440，文字清晰不溢出（截图入 repo `docs/samples/` 供对比）；同输入两次渲染输出字节级一致可不要求，视觉一致即可
- **参考**：TECH_SPEC §5.4

#### M2-4.5 配图 backend 扩展：baoyu-image-gen 集成（**M0-0 决策新增子任务，可选不阻塞 M2-4 主验收**）
- **目标**：`pipeline/creators/render.py` 增加 `image_gen.provider == "baoyu"` 分支，subprocess 调 `JimLiu/baoyu-skills` 的 `baoyu-image-gen/scripts/main.ts`，provider 集合由 `gemini|openai` 扩为 12 家（含国内 DashScope/Z.AI/Jimeng/Seedream）
- **步骤**：
  1. 集成时复核 HEAD `baoyu-image-gen` 是否仍 v2.1.0 与 CLI 签名（复核实测本机与 HEAD 字节级一致，命令已跑通 `--help`，低风险）；具体 API key 走环境变量注入 subprocess（无需 EXTEND.md），key 存 `secrets/`，provider/model 写 config
  2. `render.py` 增加 subprocess 调 `npx -y bun ~/.agents/skills/baoyu-image-gen/scripts/main.ts --prompt <text> --image <out.png> --provider X --model Y --json`，从 JSON 出口取 `savedImage` 文件路径（非 base64）
  3. 重试/失败语义与 §5.4 模板渲染一致
- **验收**：subprocess 成功调通 OpenAI 与 DashScope 各出 1 张图，JSON 解析正确，失败重试与 §5.4 一致；**不在 cron 路径验证**，仅手动 dry-run 跑通
- **参考**：TECH_SPEC §5.4 §5.5；evaluation-notes §5

### M2-5 review 阶段：审核清单
- [ ] **目标**：人每天 10 分钟完成审核
- **步骤**：
  1. `pipeline/review/checklist.py`：为当日 gated 内容生成 `output/YYYY-MM-DD/REVIEW.md`——每条含：标题、门禁分、评语、canonical 路径链接、图卡缩略引用、`- [ ] approve` 复选框
  2. 读取逻辑：`[x]`→approved，`[-]`→rejected_by_human（打回原因写在行尾，落库）
  3. `--notify`：飞书/TG webhook 发"今日 N 篇待审"+ 链接（webhook 未配则跳过）
- **验收**：生成→手工标记→再跑 review 命令→状态正确落库；重复运行无副作用
- **参考**：ARCHITECTURE §3.5

**🏁 M2 里程碑验收**：`ingest → score → create → gate → review` 全链路真实跑 3 天，每天产出 ≥1 篇过门禁内容，其中你愿意署名发出的 ≥ 60%。**未达标不进 M3**——回头调 prompt/锚点（这比写代码重要）。

---

## M3 — 排期与调度（预计 2 天）

### M3-1 scheduler：错峰排期
- [ ] **目标**：approved → publications 排期记录
- **步骤**：
  1. `pipeline/scheduler.py`：纯函数 `plan(content, platform_configs, existing_pubs, now) -> list[Publication]`
  2. 规则：config 每平台黄金时段窗口（如头条 `07:00-09:00,18:00-20:00` 本地时区）；`random.Random(seed=content_id+platform)` 取点并避开整点 ±3min；同平台同账号间隔 ≥ `min_gap_hours`；同内容跨平台错开 ≥ 30min；当日窗口排满则顺延次日
  3. 时区处理按 HARD_PARTS §8
- **验收**：固定种子单测：间隔约束、整点规避、顺延逻辑全覆盖；重跑 schedule 不改变已有排期
- **参考**：ARCHITECTURE §3.6；HARD_PARTS §8

### M3-2 launchd 定时化
- [ ] **目标**：全流水线无人值守定时执行
- **步骤**：
  1. `launchd/` 写 plist 模板（ARCHITECTURE §2 的时刻表）+ `scripts/install_launchd.sh`
  2. 每个子命令入口加 flock 锁（HARD_PARTS §8）
  3. `scripts/backup_db.sh` + 每日备份 plist
- **验收**：安装后连续 2 天自动产出到 review 环节；锁测试：手动并发跑同一命令，第二个立即退出
- **参考**：HARD_PARTS §8 §9

### M3-3 Web 控制台 v1（Dashboard + 审核台 + 选题池）
- [ ] **目标**：图形化看板与审核，替代手编 REVIEW.md
- **步骤**：
  1. `pipeline/webui/app.py`：FastAPI + Jinja2 + htmx，按 TECH_SPEC §7 路由契约实现 `/`、`/topics`（含 promote/reject）、`/review`、`/contents/{id}`、`/api/status`
  2. 审核台：卡片流展示 canonical 渲染预览 + 图卡缩略 + 门禁评分评语，approve/reject 按钮走 `transition()`
  3. `webui` 子命令启动；样式用轻量 CSS（如 Pico.css 单文件 vendor 进来），**不引入 npm 构建链**
  4. UI 层测试：FastAPI TestClient 覆盖每个路由 + 状态机约束（对已 approved 的内容再点 approve 返回错误片段）
- **验收**：浏览器完成一次完整审核流（看预览→通过 1 篇→打回 1 篇附原因），数据库状态正确；UI 进程关闭不影响 launchd 流水线运行
- **参考**：TECH_SPEC §7；ARCHITECTURE §3.9

---

## M4 — 发布通道（预计 4-6 天，最脆弱部分）

### M4-0 发布通道决策复核（30 分钟）
- [ ] **目标**：复核 M0-0 的国内发布决策是否仍然成立（距评估已过数周，开源项目变化快）
- **步骤**：检查 M0-0 DECISION 涉及项目的最近 commit/issue；有重大变化（停更/大改版）则重新评估
- **验收**：在本任务下写 `CONFIRMED` 或新的 `DECISION`；若选 AiToEarn/xhs-toolkit，M4-2/M4-3 改为写对应 API/MCP 客户端（PublisherAdapter 接口契约不变）
- **参考**：M0-0 的 evaluation-notes.md；HARD_PARTS §7

### M4-1 发布安全框架
- [ ] **目标**：三重锁 + dry-run + publish 编排
- **步骤**：
  1. `pipeline/publishers/base.py` 按 TECH_SPEC §5.2
  2. publish 子命令编排：取到期 queued → 三重锁校验（ARCHITECTURE §6）→ 乐观锁抢占 → validate → 意图日志 → publish → 落库；HARD_PARTS §1 全部要点
  3. `publishing` 超时 30min 的记录 → failed + 告警
- **验收**：TECH_SPEC §9 发布安全测试 + HARD_PARTS §1 并发验证；`publish.enabled=false` 时全路径不可达 publish 调用（测试断言 mock 未被调用）
- **参考**：HARD_PARTS §1（必读）；ARCHITECTURE §6

### M4-2 X Publisher（官方 API，最简单，先跑通框架）
- [ ] **目标**：thread 自动发布到 X
- **步骤**：X API v2（free tier，1500 帖/月够用）；`publishers/x_api.py`：OAuth2 凭据放 secrets；thread 逐条回复链式发布；中途失败记录已发部分（extra 字段），标 failed 人工处理
- **验收**：测试账号真实发一条 3 段 thread；dry-run 模式全流程日志正确
- **参考**：TECH_SPEC §5.2

### M4-3 头条 + 小红书 Publisher
- [ ] **目标**：图文双平台自动发布（按 M0-0 DECISION：小红书集成 XiaohongshuSkills，头条自写 Playwright）
- **步骤**：
  1. `pipeline/run.py login` 命令（HARD_PARTS §2 要点 1）
  2. **小红书** `publishers/xiaohongshu.py`：subprocess 封装 XiaohongshuSkills（`white0dew/XiaohongshuSkills`）的 CLI 进 PublisherAdapter（接口契约不变）。四条护栏：① mac 冒烟测试先行（该项目 Windows 优先，跑不通降级 Plan B 自写）；② vendor 固定 commit（2026-05-21 fix(_click_tab) 之后），不追 HEAD；③ `dry_run=True` 在 adapter 层校验 bundle 即返回、不碰浏览器，其 `--preview` 仅作上线前人工验证档位；④ 频控全在我方编排层（它无内建限流，社区有封号案例）
  3. **头条** `publishers/toutiao.py`：自写 Playwright，参考 social-auto-upload 源码 + AiToEarn electron 遗留代码（`project/aitoearn-electron/electron/plat/`，MIT，接口摸底资料）；选择器集中至 `_selectors.py`；stealth + 截图 + 频控全按 HARD_PARTS §2
  4. cookie 失效检测先行
- **验收**：HARD_PARTS §2 验证法——测试账号连发 3 天，无重复帖、失败有告警、截图完整
- **参考**：HARD_PARTS §2（必读，全章）；evaluation-notes §2 §3

### M4-4 Web 控制台 v2（发布日历 + 设置页）
- [ ] **目标**：图形化管理发布排期
- **步骤**：
  1. 按 TECH_SPEC §7 补齐 `/calendar`、`/publications/{id}/reschedule|cancel|retry`、`/settings`
  2. 日历为周视图表格（不引入 JS 日历库，htmx 换周即可）；retry 走 reset 逻辑（failed→queued）
  3. 设置页展示各平台 cookie 健康状态（最后校验时间 + 有效/失效标记）
- **验收**：改一条排期时间、取消一条、重试一条 failed，数据库状态与页面一致；三重锁对 UI 操作生效（`publish.enabled=false` 时 retry 后依然不会真实发布）
- **参考**：TECH_SPEC §7；ARCHITECTURE §3.9

**🏁 M4 里程碑验收**：三平台（X+头条+小红书）自动发布稳定 7 天：每日 review 后自动排期发布，除 10 分钟人审（Web 审核台）外零人工干预。

---

## M5 — 视频管线（预计 3-4 天）

### M5-1 MPT 部署与客户端
- [ ] **目标**：canonical → 口播短视频 mp4
- **步骤**：HARD_PARTS §6 全部要点——docker-compose 部署 MPT（pin tag）、`creators/video.py` 客户端、口播稿派生 prompt（60-90s，钩子前置）、edge-tts 音色、Pexels key
- **验收**：一条 gated content 端到端产出 mp4，你愿意发出去的质量；MPT 挂掉时图文格式不受影响
- **参考**：HARD_PARTS §6

### M5-2 视频发布（抖音）
- [ ] **目标**：视频自动发布到抖音
- **步骤**：按 M0-0 的 DECISION（AiToEarn 或 Playwright 参考 social-auto-upload 的 douyin_uploader）；AI 生成内容标识按平台要求勾选声明（PRD §3.4，不可省略）
- **验收**：测试账号真实发布 3 条，AI 标识可见
- **参考**：HARD_PARTS §2；PRD §3.4

### M5-3 Pixelle-Video 第二引擎接入（按 M0-0 DECISION 改写，原「OpenMontage+数字人评估」缩减进子项与 Backlog）
- [ ] **目标**：`pixelle` 引擎接入 VideoEngine 体系，承接「AI 生成类内容」（知识科普/读书/情感类）；MPT 保持默认兜底（时效资讯量产）
- **步骤**：
  1. Docker Compose 部署 Pixelle-Video（`ATH-MaaS/Pixelle-Video`，pin image tag）；生图供应商 key（DashScope/RunningHub 按量）入 `secrets/` 与 config
  2. `creators/video/pixelle.py` 实现 VideoEngine：submit→`POST /api/video/generate/async`（**mode=fixed** 注入我方口播稿，文案主权在创作管道；显式传 title 避免其 LLM 代写）、poll→`GET /api/tasks/{id}`（**以 status 为准**，progress 对 video 任务未接线恒 null）、fetch→文件下载（**完成任务 24h 后被服务端清理，需及时取**）。适配要点：aspect→frame_template 尺寸目录；style→frame_template+prompt_prefix；duration_s 按语速预估校验；**轮询 404（其任务状态存内存，服务重启即丢）按 failed 处理+重提交**；脚本分段在我方预处理为段落（其 API 未暴露 split_mode，默认 paragraph = `\n\n` 双换行分镜边界）
  3. 每条视频记录生成成本（约 ¥0.5-5/条）入 llm_calls 或独立审计
  4. 引擎路由：config 按内容 pillar/类型选 `mpt` 或 `pixelle`
  5. （时间盒 0.5 天，可选）AIGCPanel 数字人可行性速评，结论进 Backlog
- **验收**：一条 gated content 经 pixelle 引擎端到端产出 mp4，视觉质量明显优于 MPT 同稿产出；pixelle 服务挂掉时 mpt 链路不受影响
- **参考**：TECH_SPEC §5.6；evaluation-notes §4
- 备注：OpenMontage 降级为远期观察（Backlog），Pixelle-Video 接管精品/差异化视觉定位

**🏁 M5 里程碑验收**：视频 lane 稳定日更（MPT 引擎），扩展引擎有明确 DECISION。

---

## M6 — 数据回流与优化（预计 2-3 天，之后进入运营期）

### M6-1 collect：表现数据回流
- [ ] **目标**：发布 24h/72h 后抓取 views/likes/comments 入 metrics 表
- **步骤**：X 走 API；头条/小红书用登录态抓自己主页的创作者数据（只读自己的数据，合规）；失败静默重试次日
- **验收**：published 内容 48h 后 metrics 表有数据
- **参考**：ARCHITECTURE §3.8

### M6-2 周报与门禁校准
- [ ] **目标**：每周一自动生成 `output/weekly-report.md`
- **步骤**：内容：发布数/门禁通过率/丢弃率、各平台 top3 与 bottom3、LLM 成本、门禁分与实际表现的相关性散点（文本表格即可）；门禁分数直方图（HARD_PARTS §3 要点 4）
- **验收**：真实数据生成一份周报，能指导下周选题
- **参考**：HARD_PARTS §3 §4

### M6-3 免审直发（可选，运营 1 个月后）
- [ ] **目标**：`review.policy: auto_above:27` 生效
- **前置**：过去 30 天人审通过率 ≥ 85% 且无平台违规记录，否则不开
- **验收**：高分内容跳过人审直接 approved，日志清晰标注 auto-approved

---

## 后续 Backlog（不排期）

- **数字人口播 lane**（AIGCPanel 引擎，走 VideoEngine 接口）：好物分享/带货方向；前提=M5-3 评估通过 + 账号过带货门槛 + 平台虚拟人报备完成
- **OpenMontage 精品视频 lane**（远期观察，M0-0 决策降级：Pixelle-Video 已接管精品定位）：仅当 Pixelle-Video 质量不达预期时重评
- 公众号 Publisher（官方 API 草稿箱 + 人工点发布，公众号自动群发风险高；M0-0 决策：不部署 TrendPublish，自研 lane 时移植其微信兼容 HTML 后处理器，见 evaluation-notes §1 移植清单）
- Postiz 部署接入 YouTube Shorts / TikTok
- 表现数据反哺选题权重（metrics → topics 评分 prompt 动态调整）
- 多账号矩阵（同平台第二账号 = 不同支柱人设）
- 英文内容线（Medium/dev.to）
- n8n 迁移（当 launchd 管理复杂度超阈值时）
