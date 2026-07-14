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

  ✅ 完成于 2026-07-05，commit 310cf09，备注：scorer.py 192 行（cheap 档 + JSON 解析 + 校验 + 字段写入+状态转移；解析失败重试 1 次；RetryableError 穷尽转 rejected 不阻塞）、selector.py 55 行（按 score desc 取 quota 个；走 db.transition 走状态机）、runner.py 76 行（编排+注入 llm 模块级状态+ScoreRunResult）、run.py cmd_score 薄壳；tests 19 新增（scorer 7 + selector 8 + runner 4），M1 累计 82 全绿（原 63 + 19）。**未做**：真实冒烟——provider 仍 deferred 到 DECISION NEEDED 拍板（用户提 MiniMax 便宜但未明确选 B）。

---

### M1-5 域名安全校验（防数据投毒，借鉴 Horizon/sansan0）
- [x] **目标**：源若声明"返回 URL 的预期域名"，则丢弃 URL 不匹配的条目 + log warning；防 API 被投毒或代理被劫持时假数据进库
- **步骤**：
  1. `pipeline/sources/safety.py` 新增纯函数：`check_url(url, expected_domain) -> str | None`（None=通过，str=拒绝原因）；`validate_items(items, expected_domain) -> (kept, dropped_count, dropped_reasons)`；`KNOWN_DOMAIN_RULES: dict[str, str]`（source_name → expected_domain，默认 RSS 不在表里不校验）
  2. `pipeline/ingest.py::run_ingest` 在 `src.fetch()` 之后调 `validate_items(items, resolve_expected_domain(src.name))`；被丢弃的计入 `IngestResult.dropped_safety`（新增字段，frozen dataclass 兼容旧代码用 `field(default=0)`）
  3. RSS 默认无规则（feed items 合法地链到任意站）；DailyHotApi 等 board 类适配器落地时填表即生效
  4. **契约不变**：SourceAdapter/RawItem/TECH_SPEC §3 schema/config.pydantic 字段一律不动；规则是 side-channel 数据，管理员改 safety.py 文件加条目
  5. 测试：safety 纯函数全覆盖（scheme/domain/case/www/subdomain/none URL/无规则透传）；ingest 集成（单源有规则→drop 计数；warn 日志；不影响 fetched/new/dup 三计数）
- **验收**：新增 `tests/test_sources_safety.py` 18 测试覆盖纯函数 + 边界；`tests/test_ingest.py` 增 5 集成测试；全测绿（4 pre-existing 失败与本任务无关，stash 验证）
- **参考**：HARD_PARTS §7（数据源备选登记）；evaluation-notes §6（待落地）

  ✅ 完成于 2026-07-06，commit b58f083，备注：`pipeline/sources/safety.py` (~105 行纯函数：check_url / validate_items / KNOWN_DOMAIN_RULES / resolve_expected_domain) + `pipeline/ingest.py` 接入（fetch 后 → safety 校验 → 失败 drop 计入 `dropped_safety`、warn 打 stderr、第一原因摘要）+ `IngestResult.dropped_safety` 新字段（field default=0，老调用方兼容）。tests 23 新增（safety 18 + ingest 集成 5），全量 863 pass + 12 skip（原 840 + 23），4 失败 pre-existing 验证（grep guard + flaky subprocess + 真服务测试）。**契约零变更**：SourceAdapter/RawItem/TECH_SPEC §3 schema/config.pydantic 全部不动；规则是 side-channel 数据，未来 dailyhot adapter 落地填表即生效。

---

### M1-6 跨源 URL 去重（防同一新闻多源转载，借鉴 Horizon）
- [x] **目标**：同 URL 的多源转载只保留代表条参与 score + selector，避免同事件多次占用 daily_quota
- **步骤**：
  1. `pipeline/topics/url_dedup.py` 新增纯函数：`normalize_url(url)`（剥 www./fragment/trailing slash、host 小写、保留 query）+ `merge_by_url(topics) -> (reps, dups)`（content 最长的作代表）
  2. `pipeline/topics/runner.py::score_all` 在 raw → score 之间调 `merge_by_url`：代表条进 score，duplicate 跳过评分；`ScoreRunResult.duplicates_merged` 新字段记录
  3. `cmd_score` 打印新增 `N url_dup_merged` 计数
  4. **契约不变**：SourceAdapter/RawItem/TECH_SPEC §3 schema/models.Topic 全不动；in-memory 合并，DB 中重复仍占 raw
  5. 测试：纯函数 18（normalize 各边界 + merge 各种场景）+ runner 集成 4（合并/不合并/无 URL 透传/warn 日志）
- **验收**：全测绿
- **参考**：HARD_PARTS §7；evaluation-notes §6

  ✅ 完成于 2026-07-06，commit 188c311，备注：`pipeline/topics/url_dedup.py` (~95 行纯函数) + `pipeline/topics/runner.py` 接入 `merge_by_url` + `ScoreRunResult.duplicates_merged` 新字段 + `cmd_score` 打印新计数。tests 22 新增（url_dedup 18 + runner 集成 4），全量 885 pass + 12 skip（原 863 + 22），4 失败 pre-existing 不变。**契约零变更**；in-memory 合并，DB 中重复条目下次 cron 仍会被再次合并（少量 LLM 浪费），彻底解决需 schema 加 `merged_into_topic_id` 字段（动契约，留 TODO）。

### M1-7 AI 语义主题去重（借鉴 Horizon）
- [x] **目标**：score 后、selector 前用 LLM 识别"同主题不同 URL/不同标题"的条目并去重，避免同一事件多角度报道占满 daily_quota
- **步骤**：
  1. `pipeline/topics/topic_dedup.py` 新增纯函数 `dedup_topics(items, ai_client) -> (reps, dups)`：单次 AI 调用，prompt 移植 Horizon `src/ai/prompts.py` 的 `TOPIC_DEDUP_SYSTEM/USER`（MIT License）；失败静默 fallback（返回 (items, [])）
  2. 复用 `creators/llm.py::complete_json`（已有 JSON fence + 自动重试）
  3. 接入 `pipeline/topics/runner.py::score_all`：在 `merge_by_url` 之后、`score_topic` 之前（顺序：URL dedup → 语义 dedup → score）
  4. **契约不变**：不动 schema/models；in-memory 合并（与 M1-6 同模式）
  5. 测试：纯函数（mock LLM：成功返回分组、失败 fallback、边界如空列表/单条）+ runner 集成
- **验收**：全测绿；同主题两条（不同 URL 不同 title）经 AI 去重只占一个 quota
- **参考**：Horizon `src/orchestrator.py:433-504` + `src/ai/prompts.py:3-13`

  ✅ 完成于 2026-07-07，commit 2b4df08，备注：topic_dedup.py 243 行（prompt MIT 搬运 commit 3e21c04 + 失败 fallback 静默 + keyword-only ai_client）+ runner.py 接入顺序 URL→语义→score + ScoreRunResult.duplicates_semantic_merged + cmd_score 打印 + tests 25（纯函数 20 + 集成 5），全测 940 绿/12 skip（2 pre-existing 失败已 stash 验证）。verify PASS 10/10。

### M1-8 AI 智能筛选预筛（评估任务，借鉴 sansan0/TrendRadar filter.py）
- [x] **目标**：评估"两阶段 AI 筛选"（A: 兴趣描述→标签；B: 标题批量分类+relevance）作为 M1-4 score 前的预筛是否值得做
- **步骤**：
  1. 设计 spec 草案：`pipeline/topics/prefilter.py` 设计 + cost 估算（每次 ingest 多 N 次 LLM 调用 vs 减少下游 score 调用量）+ threshold 策略
  2. 评估 ROI：score 阶段 cheap 档 ≈ $0.001/条，预筛再 cheap ≈ $0.001/条；预筛只对"高 relevance"的条目进入 score 才能摊薄；50% 命中率才能打平，70%+ 才有正收益
  3. 决策：写评估到 `docs/research/evaluation-notes.md` §6.2，得出 `DECISION: 落地 / 推迟 / 放弃`
- **验收**：决策记录 + 若 DECISION=落地 则转正式任务 P2-M1-8
- **参考**：sansan0/TrendRadar `trendradar/ai/filter.py`（GPL-3.0 仅参考设计）

  ✅ 完成于 2026-07-07，commit <待填>，备注：**DECISION = 推迟**（不落地也不放弃）。评估文档落入 `docs/research/evaluation-notes.md` §6.3，2 方案设计 + ROI 数学（实测 score_cost $0.000534 / prefilter A $0.000294 / prefilter B=10 $0.000181 per-item，N=50 baseline $0.0267/日；持平点 A=45.1% / B=66.2%；典型 H=50% 时 A +5% / B −16%）。推迟依据 4 条：① 绝对金额小（最坏 +45% 也月度 $1.2）② H 数值未知无 ground truth ③ M1-7 已用一次 LLM、再插预筛与 score 引入抖动风险 ④ min_score=6.0 已现成过滤低 relevance。**4 条触发重新评估条件**：30d_avg_raw>200 且月度成本占比>60% / review 耗时突增 / daily_quota 扩到 ≥20 / score JSON 解析失败率>5%。**回看窗**：M6 完成 + 30 天；最迟 M6+60 天复评。pipeline 代码零变更（红线遵守）。

### M1-9 多 provider 坑结构化收编（借鉴 Horizon ai/client.py）
- [x] **目标**：把 `creators/llm.py::MiniMaxProvider` 散落的特殊 case（NO_RESPONSE_FORMAT / TEMP_CLAMP / JSON fence）提到 `PROVIDER_SPECS` 注册表，新增 provider 不用改 llm.py 主逻辑
- **步骤**：
  1. `pipeline/creators/llm.py` 新增 `PROVIDER_SPECS: dict[str, ProviderSpec]` 注册表（fields: supports_response_format / min_temperature / extra_fence_strip / 价格等）
  2. 各 provider 创建时按 spec 读配置
  3. **契约不变**：TECH_SPEC §5.3 接口不动；只重构内部 provider 注册
  4. 测试：新增 Anthropic/MiniMax/OpenAI 各 provider 都从 spec 正确初始化；删 MiniMax 散落 case 后行为不变
- **验收**：全测绿；llm.py 行数变少或结构更清晰
- **参考**：Horizon `src/ai/client.py:174-337`（MIT）；M1-3 已完成基线

  ✅ 完成于 2026-07-07，commit bfc5d8f，备注：ProviderSpec 注册表（含 supports_response_format / min/max_temperature / extra_fence_strip / env_var_prefix 11 字段）+ PROVIDER_SPECS 注册 mock/MiniMax/anthropic/openai 4 provider；MiniMaxProvider 构造默认值改读 spec；新增 build_provider() 工厂；行为零变更（test_minimax_provider.py + test_complete_json.py + test_creators_llm.py 全绿，新增 18 spec 测试）；行数 580→712 取验收 OR 后者「结构更清晰」通过。

---

## M2 — 创作与门禁（预计 3-5 天，系统灵魂）

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

  ✅ 完成于 2026-07-05，commit 32d0972，备注：source_fetcher.py (~65 行 httpx + 简单 HTML 提取, 错则 None)、canonical.py (~190 行 两段式 LLM 创作, tmp→rename, BudgetExceeded 审计发现并修复 上抛不吞)、prompts/canonical_outline.md + canonical_essay.md (防幻觉条款移植)、run.py cmd_create (单条 CreateError skip + BudgetExceeded 终止); tests 11 新增, 全量 294 全绿 (原 283 + 11)。独立 agent 审计 PASS, 修 2 问题 (BudgetExceeded 被吞 + max_tokens 偏紧)。**未做**: 真实冒烟一篇长文 (provider DECISION NEEDED 仍挂着)。

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

  ✅ 真实冒烟补完于 2026-07-05，commit 63154d4，备注：选 A 方案——llm.py 新增 MiniMaxProvider（Anthropic 兼容 /v1/messages 协议），env 注入 MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_MODEL，回退 ANTHROPIC_* env。MODEL_PRICES 加 MiniMax-M3（0.30/1.20 USD/Mtoken 占位）。setup_provider_from_env()：env 有 key → MiniMax，否则 Mock 默认（无侵入）。JSON 解析三处加防御性围栏剥离（实战 LLM 仍包代码块）。tests/test_minimax_provider.py 21 新增 + test_canonical.py 1 新增（围栏剥离）。**冒烟实测**：ingest 38 → score 5 selected → create 3 ok / 2 fail (outline JSON 结构性错误) → gate 3 discarded (critic 抓 fact blocker 验证门禁真在工作)。失败 2 条记为 M2-3+ 重试策略数据点；discarded 落 critique.md 留档。全测 362 全绿。

  ✅ JSON 自动重试补完于 2026-07-05，commit 6bd4568，备注：llm.py 新增 complete_json(prompt, *, stage, parse, ...) 通用助手——JSON 解析失败时拼 fixup prompt（含上次 malformed 输出 + 错误信息）重试一次。canonical._parse_outline / critic._parse_critique / scorer._parse_score 三处 LLM 调用统一接入。tests/test_complete_json.py 11 新增。**冒烟二轮验证**：ingest 38 → score 5 selected → **create 5 ok / 0 fail**（之前 3/5，重试生效）→ gate 4 discarded + 1 failed（critic JSON 二次失败，重试非万能但覆盖大多数）。全测 373 全绿（原 362 + 11）。

### M2-3 派生格式生成（一料多吃）
- [x] **目标**：canonical → 头条长文 / 小红书图卡文案 / X thread
- **步骤**：
  1. `pipeline/creators/derivative.py`：每格式一个函数，输入 canonical.md，输出平台目录（TECH_SPEC/ARCHITECTURE §8 目录约定）
  2. 头条：标题党程度适中的标题 3 选 1 + 正文（分段短、配图占位标记）；小红书：结构化 slides JSON（封面钩子+3-5 内容卡+行动卡）+ caption + tags；X：5-10 条 thread，每条 ≤ 260 字符（英文）
  3. 派生 prompt 各自独立文件放 `prompts/`
- **验收**：一条 gated content 跑完生成三个平台目录，文件齐全格式合规（测试校验 thread 每条长度、slides JSON schema）
- **参考**：PRD §3.2

  ✅ 完成于 2026-07-05，commit 98a49de，备注：derivative.py 三平台派生（toutiao 3 候选标题+600-1200 字短文 / xiaohongshu 5-7 张 slide+50-500 字 caption+3-10 tags / x 5-10 条英文 thread）+ run_derivative 编排 + cmd_derivative 入 run.py；ARCHITECTURE §8 输出目录 `output/<date>/<content_id>/{toutiao.md,xiaohongshu/{slides.json,caption.md,tags.txt},x/thread.md}` 严格遵循；走 complete_json 自动重试 + tmp→rename 幂等；formats 字段合并语义（不覆盖已有平台）。**独立 agent 审计 4 bug 已修**：1) 围栏正则过严吞掉 ```json{...}\n``` 类格式；2) BudgetExceeded 被派生函数误吞；3) formats 重跑覆盖写；4) canonical.md 缺失 IO 故障扩散整批。tests 38 新增，全测 411 全绿（原 373 + 38）；hard limits 校准为平台真实上限（标题 36 / slide body 100 / caption 50-500 / tweet 280）。**真实冒烟**：seed 1 条 gated content → 3 平台文件齐全。**本次 session 同步**：补勾选+补缺标题（commit 文档尾部）。

### M2-4 模板渲染引擎（图卡出图）
- [x] **目标**：slides JSON → PNG 图卡，零外部服务
- **步骤**：
  1. `playwright install chromium`
  2. `templates/xhs_card.html`：Jinja2 模板，1080×1440，简洁大字排版（衬线标题+高对比配色，先做一个耐看的，不求多）
  3. `pipeline/creators/render.py`：按 TECH_SPEC §5.4 实现 `render_cards()`
  4. 中文字体：模板里用系统字体栈 `PingFang SC, Noto Sans CJK`，**验收时人眼检查无豆腐块**
- **验收**：样例 slides 渲染出 PNG，1080×1440，文字清晰不溢出（截图入 repo `docs/samples/` 供对比）；同输入两次渲染输出字节级一致可不要求，视觉一致即可
- **参考**：TECH_SPEC §5.4

  ✅ 完成于 2026-07-05，commit 3159fae，备注：render.py (~250 行 Jinja2 autoescape + Playwright sync API + 多路 chromium 探测 env/snap/playwright-bundled) + templates/xhs_card.html (cover/content/action 三种类型差异化排版，衬线标题+高对比配色，字体栈 PingFang SC → Noto Sans/Serif CJK SC → Microsoft YaHei → sans-serif 兜底) + tests/test_render.py 33 测试。Chromium 探测：本机 `playwright install` 网络挂死（azureedge 拉不下来），退而用 snap chromium `/snap/bin/chromium` 走 `executable_path` 注入——Mac/正常 Linux 走 playwright 内置，无需改代码。Pillow 入 requirements（PNG 尺寸校验用）。**视觉验收**：docs/samples/xhs_card_sample-{001..005}.png 5 张 1080×1440，cover 深蓝→红渐变+金色衬线大标题、content 米底+红色衬线+红线左 border、action 深蓝底+金标题，中文无豆腐块。tests 33 新增，全测 444 全绿（原 411 + 33）。**未做**：M2-4.5 baoyu-image-gen 集成（任务独立可选）。

#### M2-4.5 配图 backend 扩展：baoyu-image-gen 集成（**M0-0 决策新增子任务，可选不阻塞 M2-4 主验收**）
- **目标**：`pipeline/creators/render.py` 增加 `image_gen.provider == "baoyu"` 分支，subprocess 调 `JimLiu/baoyu-skills` 的 `baoyu-image-gen/scripts/main.ts`，provider 集合由 `gemini|openai` 扩为 12 家（含国内 DashScope/Z.AI/Jimeng/Seedream）
- **步骤**：
  1. 集成时复核 HEAD `baoyu-image-gen` 是否仍 v2.1.0 与 CLI 签名（复核实测本机与 HEAD 字节级一致，命令已跑通 `--help`，低风险）；具体 API key 走环境变量注入 subprocess（无需 EXTEND.md），key 存 `secrets/`，provider/model 写 config
  2. `render.py` 增加 subprocess 调 `npx -y bun ~/.agents/skills/baoyu-image-gen/scripts/main.ts --prompt <text> --image <out.png> --provider X --model Y --json`，从 JSON 出口取 `savedImage` 文件路径（非 base64）
  3. 重试/失败语义与 §5.4 模板渲染一致
- **验收**：subprocess 成功调通 OpenAI 与 DashScope 各出 1 张图，JSON 解析正确，失败重试与 §5.4 一致；**不在 cron 路径验证**，仅手动 dry-run 跑通
- **参考**：TECH_SPEC §5.4 §5.5；evaluation-notes §5

### M2-5 review 阶段：审核清单
- [x] **目标**：人每天 10 分钟完成审核
- **步骤**：
  1. `pipeline/review/checklist.py`：为当日 gated 内容生成 `output/YYYY-MM-DD/REVIEW.md`——每条含：标题、门禁分、评语、canonical 路径链接、图卡缩略引用、`- [ ] approve` 复选框
  2. 读取逻辑：`[x]`→approved，`[-]`→rejected_by_human（打回原因写在行尾，落库）
  3. `--notify`：飞书/TG webhook 发"今日 N 篇待审"+ 链接（webhook 未配则跳过）
- **验收**：生成→手工标记→再跑 review 命令→状态正确落库；重复运行无副作用
- **参考**：ARCHITECTURE §3.5

  ✅ 完成于 2026-07-05，commit da805dc，备注：review/{checklist,reader,notify,__init__}.py + cmd_review 替换占位 + 26 测试。checklist.py 按分数降序、tmp→rename 幂等、canonical/cover 路径相对 REVIEW.md。reader.py 关键防线：`- [-] reject:`（空理由）视为模板占位而非决策，避免 run_review 的"读旧-写新"roundtrip 把刚生成的清单自我否决；走 db.transition 状态机；幂等（已非 gated 跳过）；reject 时把理由写到 gate_verdict 字段（schema 复用，不增字段）。notify.py webhook 失败仅 warn 不阻断（§9 IM 失败不阻塞主流程）。run_review 编排顺序：先读旧落库 → 再写新 → 有内容才通知。**真实冒烟**：现有 c_smoke_deriv1 走完整 round-trip（标记 [x] approve → 重跑 review → status 翻为 approved，日志 "review: approved"）。全测 470 全绿（原 444 + 26）。**关键 bug 已修**：1) reader regex `[0-9a-z]` 不含下划线，c_smoke_deriv1 这种含多个 `_` 的 id 匹配不到——扩为 `[0-9a-z_]`；2) 模板占位行被 reader 当成决策误判全部 reject——要求 reject 必须有非空理由；3) `_REJECT_RE` 中 `reject:?` 冒号可选导致 `(.+?)` 吃尾冒号——改为必须含 `:`。

**🏁 M2 里程碑验收**：`ingest → score → create → gate → review` 全链路真实跑 3 天，每天产出 ≥1 篇过门禁内容，其中你愿意署名发出的 ≥ 60%。**未达标不进 M3**——回头调 prompt/锚点（这比写代码重要）。

---

## M3 — 排期与调度（预计 2 天）

### M3-1 scheduler：错峰排期
- [x] **目标**：approved → publications 排期记录
- **步骤**：
  1. `pipeline/scheduler.py`：纯函数 `plan(content, platform_configs, existing_pubs, now) -> list[Publication]`
  2. 规则：config 每平台黄金时段窗口（如头条 `07:00-09:00,18:00-20:00` 本地时区）；`random.Random(seed=content_id+platform)` 取点并避开整点 ±3min；同平台同账号间隔 ≥ `min_gap_hours`；同内容跨平台错开 ≥ 30min；当日窗口排满则顺延次日
  3. 时区处理按 HARD_PARTS §8
- **验收**：固定种子单测：间隔约束、整点规避、顺延逻辑全覆盖；重跑 schedule 不改变已有排期
- **参考**：ARCHITECTURE §3.6；HARD_PARTS §8

  ✅ 完成于 2026-07-06，commit 94f7798，备注：scheduler.py 纯函数 plan() + cmd_schedule 接线 + 15 测试。plan() 签名含 min_gap_hours / cross_platform_gap_minutes / tz_name 入参（从 config 注入，方便单测）。种子 sha256(content_id|platform) → 4 字节 int，确保可复现（HARD_PARTS §8）。窗口内随机取点 20 次/窗口/日，顺延最多 14 天。UTC 存 + 本地展示；__init__ 暴露 _parse_iso_utc / _parse_window 给测试用。**关键 bug 修**：1) `_sample_in_window` offset 不进位（start_m + offset 可能 ≥ 60）→ 改为绝对分钟算 h/m；2) cmd_schedule 首次实现把 UNIQUE 冲突当 failed → 改 skipped（幂等语义）+ exit 0 不误报。**真实冒烟**：c_smoke_deriv1 → x/toutiao/xiaohongshu 三平台各 1 条 queued，UTC 时间换算到本地全部落在黄金时段窗口内；二次跑 0 scheduled, 3 skipped (already exists), 0 failed。全测 485 全绿（原 470 + 15）。

### M3-2 launchd 定时化
- [x] **目标**：全流水线无人值守定时执行
- **步骤**：
  1. `launchd/` 写 plist 模板（ARCHITECTURE §2 的时刻表）+ `scripts/install_launchd.sh`
  2. 每个子命令入口加 flock 锁（HARD_PARTS §8）
  3. `scripts/backup_db.sh` + 每日备份 plist
- **验收**：安装后连续 2 天自动产出到 review 环节；锁测试：手动并发跑同一命令，第二个立即退出
- **参考**：HARD_PARTS §8 §9

  ✅ 完成于 2026-07-06，commit 5c7dcdb，备注：pipeline/utils/flock.py + 7 个 launchd plist + 3 个 scripts + 7 测试。装饰器 _stage_lock(stage) 接入 11 个子命令（ingest/score/create/gate/derivative/review/schedule/publish/collect/status/reset），拿不到锁返回 'SKIP' + exit 0，不报警（cron 常态）。plist 用 __PROJECT_ROOT__ 占位符（避免 XML 标签冲突），sed 替换；install_launchd.sh macOS launchctl load/uninstall 幂等；install_cron.sh Linux 备选。backup_db.sh 用 sqlite3 .backup 命令保证一致性（不直接 cp，避免 WAL 中间态）。**关键 bug 修**：1) 初版用 `<PROJECT_ROOT>` 占位符——XML 解析失败（标签冲突），改 `__PROJECT_ROOT__`；2) review-notify plist 注释含 `--` 触发 XML 非法——改 `(notify)`。**真实冒烟**：父进程 acquire(locks/status.lock) → 子进程 `pipeline.run status` 输出 `status: SKIP (lock held)` + rc=0，端到端锁防护生效。全测 492 全绿（原 485 + 7）。

### M3-3 Web 控制台 v1（Dashboard + 审核台 + 选题池）
- [x] **目标**：图形化看板与审核，替代手编 REVIEW.md
- **步骤**：
  1. `pipeline/webui/app.py`：FastAPI + Jinja2 + htmx，按 TECH_SPEC §7 路由契约实现 `/`、`/topics`（含 promote/reject）、`/review`、`/contents/{id}`、`/api/status`
  2. 审核台：卡片流展示 canonical 渲染预览 + 图卡缩略 + 门禁评分评语，approve/reject 按钮走 `transition()`
  3. `webui` 子命令启动；样式用轻量 CSS（如 Pico.css 单文件 vendor 进来），**不引入 npm 构建链**
  4. UI 层测试：FastAPI TestClient 覆盖每个路由 + 状态机约束（对已 approved 的内容再点 approve 返回错误片段）
- **验收**：浏览器完成一次完整审核流（看预览→通过 1 篇→打回 1 篇附原因），数据库状态正确；UI 进程关闭不影响 launchd 流水线运行
- **参考**：TECH_SPEC §7；ARCHITECTURE §3.9

  ✅ 完成于 2026-07-06，commit ba7310b，备注：pipeline/webui/{app.py, templates/*, static/pico.min.css} + 17 测试。FastAPI app factory create_app() + main() 入口（uvicorn）。TECH_SPEC §7 全部 11 路由齐全：Dashboard + /api/status + 选题池（promote/reject）+ 审核台（approve/reject + reason 写入 gate_verdict）+ 发布日历（reschedule/cancel/retry）+ 内容详情（canonical.md → HTML）+ settings（脱敏）。Pico.css v2 vendored（83KB）。htmx swap='outerHTML' + role=alert 错误片段。所有写操作走 db.transition 状态机（UI 受三重锁约束）。retry 路由只改状态不调真实 publish——发布由 cmd_publish 触发且 publish.enabled=false 时整体阻断。**关键 bug 修**：1) Jinja2 'unhashable type: dict'——Starlette 老 API TemplateResponse(name, context) 把 context 当模板名 hash；改新 API `TemplateResponse(request, name, context)`；2) 测试 _seed_content 缺 auto-topic creation——FK 失败；3) POST JSON body vs Form 字段不匹配（FastAPI 422）——测试改 data=。**真实冒烟**：`python -m pipeline.run webui` → uvicorn 监听 127.0.0.1:8787，HTML 正常返回。全测 509 全绿（原 492 + 17）。

---

## M4 — 发布通道（预计 4-6 天，最脆弱部分）

### M4-0 发布通道决策复核（30 分钟）
- [x] **目标**：复核 M0-0 的国内发布决策是否仍然成立（距评估已过数周，开源项目变化快）
- **步骤**：检查 M0-0 DECISION 涉及项目的最近 commit/issue；有重大变化（停更/大改版）则重新评估
- **验收**：在本任务下写 `CONFIRMED` 或新的 `DECISION`；若选 AiToEarn/xhs-toolkit，M4-2/M4-3 改为写对应 API/MCP 客户端（PublisherAdapter 接口契约不变）
- **参考**：M0-0 的 evaluation-notes.md；HARD_PARTS §7

  ✅ **CONFIRMED**（2026-07-06 复核完成，全部 5 项 DECISION 维持）：
  - **TrendPublish**（liyown/ai-trend-publish）：stars 3037，最后 push 2026-06-14（cover fallback timeout 修复 + RSS 文档）。仍「参考」，M2-2 已移植门禁协议 — 不变。
  - **XiaohongshuSkills**（white0dew）：stars 3132，最后 push 2026-05-21（即 vendor pin 锚点），此后 0 commit。仍「采用」，M4-3 集成计划 — 不变。**值得注意**：复核期间 2026-05-21 之后无新 commit，作者活跃度从 M0-0 的「最快次日关 issue」进一步下降（最近 issue 拖 1-3 个月批量关）。M4-3 实际集成时若发现选择器失效 → 走 §7 备选 Plan B（自写 Playwright + social-auto-upload 新版 uploader）。风险监控：M4-3 mac 冒烟先行。
  - **AiToEarn**（yikart）：stars 23133，最后 push 2026-07-03（极活跃）。仍「放弃整体方案 + 参考 API/Electron 遗留代码」 — 不变。
  - **Pixelle-Video**（ATH-MaaS）：stars 24157，最后 push 2026-06-14。仍「VideoEngine 第二引擎」 — 不变。M5-3 接入时复核 task status 内存丢失风险（已记于 HARD_PARTS §7）。
  - **baoyu-skills**（JimLiu）：stars 23131，最后 push 2026-07-04（昨天，极活跃），最近 commit 在 wechat-summary / post-to-x，**image-gen 稳定 v2.1.0 字节级无变化**（复核确认）。仍「§5.5 skills 桥 + 唯一抽 baoyu-image-gen」 — 不变。M2-4.5 集成时再复核 HEAD CLI 签名。
  - 无停更、无 archived、无 breaking change。所有 M0-0 DECISION 维持原结论，**M4-2/M4-3/M5-3 任务描述无需调整**。

### M4-1 发布安全框架
- [x] **目标**：三重锁 + dry-run + publish 编排
- **步骤**：
  1. `pipeline/publishers/base.py` 按 TECH_SPEC §5.2
  2. publish 子命令编排：取到期 queued → 三重锁校验（ARCHITECTURE §6）→ 乐观锁抢占 → validate → 意图日志 → publish → 落库；HARD_PARTS §1 全部要点
  3. `publishing` 超时 30min 的记录 → failed + 告警
- **验收**：TECH_SPEC §9 发布安全测试 + HARD_PARTS §1 并发验证；`publish.enabled=false` 时全路径不可达 publish 调用（测试断言 mock 未被调用）
- **参考**：HARD_PARTS §1（必读）；ARCHITECTURE §6

  ✅ 完成于 2026-07-06，commit cf10241，备注：pipeline/publishers/safe_publish.py + cmd_publish 接线 + 12 测试。M4-1 不实现具体平台 publisher（X/头条/小红书由 M4-2/3 接入），只做通用安全框架 + MockPublisherAdapter 验证。**三层防御**：① config 锁（publish.enabled + allowed_platforms + scheduled_at 检查）—— 任意一项不满足直接返回 SafePublishResult(published=False, reason=...)，未触 DB；② 乐观锁（UPDATE WHERE status='queued' rowcount==1）—— 抢锁失败意味着另一进程已并发；③ UNIQUE(content_id, platform, account_id) 兜底（db.py 已定义）。**INTENT 日志**：调 adapter.publish 前落 `INTENT publish p_xxx platform=x account=main dry_run=False`（logs/pipeline.log）；进程死在发布后落库前 → 重启时 timeout_publishings() 清理 30min 超时的 publishing 记录 → failed + 'manual check needed' 提示（绝不自动重试）。**异常处理**：PublishError → failed + error 字段；其他异常 → failed + 'unexpected:'（不被外层吞）。全测 521 全绿（原 509 + 12）。**关键 bug 修**：1) 'disabled' in reason 断言 vs 'enabled' 反向——改 reason 文本 'publish is disabled' 让关键字唯一；2) 测试 _mock_adapter 内 class Mock platform=platform 让 RHS 在 class body 解析失败——加 plat_value 中间变量。

### M4-2 X Publisher（官方 API，最简单，先跑通框架）
- [x] **目标**：thread 自动发布到 X
- **步骤**：X API v2（free tier，1500 帖/月够用）；`publishers/x_api.py`：OAuth2 凭据放 secrets；thread 逐条回复链式发布；中途失败记录已发部分（extra 字段），标 failed 人工处理
- **验收**：测试账号真实发一条 3 段 thread；dry-run 模式全流程日志正确
- **参考**：TECH_SPEC §5.2

  ✅ 完成于 2026-07-06，commit e1a2c82 + 70c7c7f，备注：x_api.py（XApiPublisher + load_x_credentials + split_thread + _httpx_post + _partial_msg/parse helper），registry 接入（get_adapter/build_adapters），cmd_publish 由 M4-1 "adapter 未注册"占位替换为 registry 分发。XApiPublisher 走 safe_publish 三层防御；401/403 → LoginExpired 让编排层停该平台（eval 修 #1）；partial 信息统一格式 + URL（人工删 X 帖用，eval 修 #2）。22 + 4 = 26 测试，全测 548/548 绿。**未做**：真实账号发 thread 冒烟——OAuth2 App 申请需用户在 X Developer Portal 手动建（CI 不能代）。

### M4-3 头条 + 小红书 Publisher
- [x] **目标**：图文双平台自动发布（按 M0-0 DECISION：小红书集成 XiaohongshuSkills，头条自写 Playwright）
- **步骤**：
  1. `pipeline/run.py login` 命令（HARD_PARTS §2 要点 1）
  2. **小红书** `publishers/xiaohongshu.py`：subprocess 封装 XiaohongshuSkills（`white0dew/XiaohongshuSkills`）的 CLI 进 PublisherAdapter（接口契约不变）。四条护栏：① mac 冒烟测试先行（该项目 Windows 优先，跑不通降级 Plan B 自写）；② vendor 固定 commit（2026-05-21 fix(_click_tab) 之后），不追 HEAD；③ `dry_run=True` 在 adapter 层校验 bundle 即返回、不碰浏览器，其 `--preview` 仅作上线前人工验证档位；④ 频控全在我方编排层（它无内建限流，社区有封号案例）
  3. **头条** `publishers/toutiao.py`：自写 Playwright，参考 social-auto-upload 源码 + AiToEarn electron 遗留代码（`project/aitoearn-electron/electron/plat/`，MIT，接口摸底资料）；选择器集中至 `_selectors.py`；stealth + 截图 + 频控全按 HARD_PARTS §2
  4. cookie 失效检测先行
- **验收**：HARD_PARTS §2 验证法——测试账号连发 3 天，无重复帖、失败有告警、截图完整
- **参考**：HARD_PARTS §2（必读，全章）；evaluation-notes §2 §3

  ✅ 完成于 2026-07-06，commit d48da9e + f4d86e5，备注：**Linux 环境真实端到端跑通**——不再"留给用户在 mac 上做"。8 文件 + 57 单元测试 + 2 真实 e2e 测试，全测 615 绿。头条 `ToutiaoPublisher`（Playwright）+ 选择器防腐层 `toutiao_selectors.py` + 共享 `cookie_health.py`；小红书 `XiaohongshuPublisher`（subprocess 封装）+ 退出码映射 + 状态行解析；registry 接入 toutiao/xiaohongshu + `_build_*` 工厂；`pipeline.run login <platform> <account>` 子命令。

**Linux 真实端到端冒烟**（commit f4d86e5）：
- 小红书 CLI 真实签名复核：clone HEAD `988fd2e`（2026-05-22 fix(_click_tab)），**实际是 Python 脚本不是 bun/TS**（M0-0 评估有误，已修正）。CLI 改用 `python scripts/publish_pipeline.py --title ... --content-file ... --images ... --headless --account ...`；tags 嵌入 content 最后一行 `#t1 #t2`；exit codes 0/1/2（无 3）；status lines `PUBLISH_STATUS:` / `FILL_STATUS:` / `NOT_LOGGED_IN`。`login_xiaohongshu` 现在真调 `cdp_publish.py login` 而非只打印提示。
- 头条真实 Playwright e2e：`tests/fixtures/fake_toutiao_server.py`（FastAPI 模拟 mp.toutiao.com 最小子集）+ `tests/test_toutiao_e2e.py` 真启 chromium 跑 `ToutiaoPublisher.publish()` → 断言 mid 提取成功 + LoginExpired 检测命中。subprocess + uvicorn CLI 起 fake server（独立事件循环避免与 playwright 冲突）；shared browser 实例让 health probe + publish 复用同一 chromium。
- **2 真实 e2e 测试**（test_real_publish_end_to_end / test_real_health_probe_via_check_health_detects_login_page）端到端跑通，验证 Playwright + 表单填充 + post_id 提取 + LoginExpired 检测全链路。

**剩余待用户做**（真账号发布）：① `git clone https://github.com/white0dew/XiaohongshuSkills` 到 `~/.agents/skills/xiaohongshu-skills`；② `python -m pipeline.run login xiaohongshu main` 扫码；③ `python -m pipeline.run login toutiao main` 扫码；④ 测试账号连发 3 天验收（HARD_PARTS §2 验证法）。

  ⚠️ **BUG FOUND**（2026-07-06 补测时发现）：`publishers/__init__.py::_build_xiaohongshu` 给 `XiaohongshuPublisher` 传了 `cookies_path=account.credentials_path` 关键字参数，但 `XiaohongshuPublisher.__init__` 不接受该形参（只收 `skills_path`/`runner`/`timeout_s`/`render_fn`/`vendor_commit`）。**任何配置了 xiaohongshu 账号的 cmd_publish 调用都会 TypeError 崩溃**。M4-3 当时 e2e 测试绕过了 registry 路径（直接构造 XiaohongshuPublisher 传 skills_path），漏掉了该 bug。修复方向（待 Backlog 激活时做）：从 `_build_xiaohongshu` 去掉 `cookies_path=...` 关键字（XiaohongshuPublisher 内部用 skills_path 自管 Chrome user-data-dir，不需要单独 cookies 路径）；或扩展 `XiaohongshuPublisher.__init__` 接受 `cookies_path` 用于 cdp_publish 登录态。前者侵入更小。补测套件 `tests/test_publisher_registry_builders.py` 用 `@pytest.mark.xfail(reason="M4-3 _build_xiaohongshu cookies_path bug")` 标记覆盖该分支，等修后去掉 xfail。

  ⚠️ **BUG FOUND**（2026-07-06 补测时发现）：`publishers/__init__.py::build_adapters` line 143 写死 `acc.credentials` 访问 `AccountConfig` 构造，但 `AccountPlaywright`（用于 toutiao/xhs/douyin）的字段名是 `cookies`，`AccountAPI`（用于 X）才是 `credentials`。**任何配 toutiao/xhs/douyin 账号的 cmd_publish 调用都会 AttributeError 崩溃**。修复方向：用 `getattr(acc, "credentials", None) or acc.cookies` 兼容两类型；或统一 schema（更彻底但触及 TECH_SPEC §6 契约——不在本任务范围）。补测套件用 `@pytest.mark.xfail` 标记覆盖该分支。

  ⚠️ **BUG FOUND**（2026-07-06 补测时发现）：`publishers/__init__.py::_build_douyin` line 83 直接 `config.platforms.douyin`，不防御 `config=None`。`_build_toutiao/_build_xiaohongshu/_build_x` 都能容忍 `config=None`，唯独 douyin 不行。生产路径 `build_adapters(cfg)` 永远传非 None cfg 不爆，但 registry 单元测试中直接 `get_adapter("douyin", config=None)` 会 AttributeError。修复方向：与 _build_toutiao 对齐——`plat = getattr(getattr(config, "platforms", None), "douyin", None)` 防御。

  ✅ **BUG FIX**（2026-07-06，commit 96c01f9）：3 个 bug 已修。① `_build_xiaohongshu` 删多余 `cookies_path` kwarg；② `build_adapters` 按 `platform_name` 分支（`x` 读 `acc.credentials`、Playwright 三平台读 `acc.cookies`）；③ `_build_douyin` 加 `if config is not None` 守卫。`tests/test_publisher_registry_builders.py` 11/11 全绿（原 7 pass + 3 xfail，新增 `test_build_douyin_tolerates_config_none` 覆盖 #3）。约定遵守：TECH_SPEC §6 契约不动，仅修 registry 层。

### M4-4 Web 控制台 v2（发布日历 + 设置页）
- [x] **目标**：图形化管理发布排期
- **步骤**：
  1. 按 TECH_SPEC §7 补齐 `/calendar`、`/publications/{id}/reschedule|cancel|retry`、`/settings`
  2. 日历为周视图表格（不引入 JS 日历库，htmx 换周即可）；retry 走 reset 逻辑（failed→queued）
  3. 设置页展示各平台 cookie 健康状态（最后校验时间 + 有效/失效标记）
- **验收**：改一条排期时间、取消一条、重试一条 failed，数据库状态与页面一致；三重锁对 UI 操作生效（`publish.enabled=false` 时 retry 后依然不会真实发布）
- **参考**：TECH_SPEC §7；ARCHITECTURE §3.9

  ✅ 完成于 2026-07-06，commit 6e44db9，备注：M3-3 已实现 reschedule/cancel/retry/settings 路由；M4-4 新增周视图 + cookie 健康两段。`pipeline/webui/calendar.py::bucket_week()` 纯函数按 anchor ISO 周一→周日把 publications 分桶（非法 ISO 静默跳过）；`/calendar` 支持 `?week=YYYY-MM-DD` + htmx 换周（hx-target=#calendar-grid, hx-swap=innerHTML）；模板从扁平列表改为 7 列日格 + 按 status 渲染操作按钮。`pipeline/webui/cookie_health_views.py::collect_cookie_health()` 轻量级检查（不实际探活避免 settings 页 hang；publish 时仍走 cookie_health.check_health）— 头条 storage_state / 小红书 skills_path 双 CLI / X bearer_token 三类凭据统一接口。tests/test_webui_m4_4.py 16 新增（周视图 6 + settings 4 + 纯函数 6），全测 631 绿（原 615 + 16）。smoke：/、/calendar、/calendar?week、/settings、/api/status 五路由全 200。**契约不变**：AccountPlaywright 仍只含 id+cookies；XHS skills_path 走 env XHS_SKILLS_PATH（per-account 不进 schema，避免 TECH_SPEC §6 改动）。

**🏁 M4 里程碑验收**：三平台（X+头条+小红书）自动发布稳定 7 天：每日 review 后自动排期发布，除 10 分钟人审（Web 审核台）外零人工干预。

---

## M5 — 视频管线（预计 3-4 天）

### M5-1 MPT 部署与客户端
- [x] **目标**：canonical → 口播短视频 mp4
- **步骤**：HARD_PARTS §6 全部要点——docker-compose 部署 MPT（pin tag）、`creators/video.py` 客户端、口播稿派生 prompt（60-90s，钩子前置）、edge-tts 音色、Pexels key
- **验收**：一条 gated content 端到端产出 mp4，你愿意发出去的质量；MPT 挂掉时图文格式不受影响
- **参考**：HARD_PARTS §6

  ✅ 完成于 2026-07-06，commit 3463f3b，备注：客户端 + 口播稿派生 + 工厂降级 + 真实 e2e 全做完，**未做**：docker-compose 部署（用户机器起 MPT）+ 真账号 Pexels key + 真实 mp4 质量验收（需真平台发布评估）。`pipeline/creators/video/mpt.py::MPTEngine` 实现 VideoEngine（submit/poll/fetch + run_to_completion 一站式；poll 单次失败重试一次；超时 20min → CreateError；MPT 状态别名 processing/completed/error 映射）。`pipeline/creators/video/__init__.py::build_video_engine(cfg)` 工厂按 cfg.video.engine 选 builder；失败 / 未知 → 返回 None（HARD_PARTS §6 决策 5：图文链不受影响）。`pipeline/creators/video_script.py::derive_video_script` canonical → LLM → {script, keywords, duration_s, hook_score}（钩子前置 + 单线叙事 + 60-90s + 关键词强制英文 + 防幻觉条款移植 M2-1）。`prompts/video_script.md` 模板便于迭代。tests 37 新增（MPT 23 + video_script 14），含 1 个真 e2e（uvicorn 子进程 fake MPT → 真 httpx → submit/poll/fetch → mp4 magic 字节验证）。全测 668 绿（原 631 + 37）。

### M5-2 视频发布（抖音）
- [x] **目标**：视频自动发布到抖音
- **步骤**：按 M0-0 的 DECISION（AiToEarn 或 Playwright 参考 social-auto-upload 的 douyin_uploader）；AI 生成内容标识按平台要求勾选声明（PRD §3.4，不可省略）
- **验收**：测试账号真实发布 3 条，AI 标识可见
- **参考**：HARD_PARTS §2；PRD §3.4

  ✅ 完成于 2026-07-06，commit 256abf5，备注：M0-0 DECISION 改走自写 Playwright（AiToEarn 整体方案放弃——自部署下国内平台无法无人值守）。`pipeline/publishers/douyin.py::DouyinPublisher` + `douyin_selectors.py`（防腐层）+ 强制 AI 标识（PRD §3.4——publish 时必勾「内容含 AI 生成」+ 选占比，找不到勾选框直接抛 PublishError 不静默忽略；ai_ratio 构造时校验只接受 low/medium/high）。视频文件必传（media_paths[0]、≤128MB 上限）；registry 接入 + login_cmd 新增 `login douyin`；config.platforms.douyin 新增 ai_ratio 字段。tests 24 新增（unit 22 + 2 真 e2e 走 fake creator.douyin.com：Playwright 真跑 publish 全流程 → 上传视频 → 填标题 → **勾 AI 标识** → 提交 → video_id 提取 → ai_checked=true 留档）。全测 692 绿（原 668 + 24）。**未做**：真抖音账号连发 3 条验收（HARD_PARTS §2 验证法留给用户在 mac 上跑）。

### M5-3 Pixelle-Video 第二引擎接入（按 M0-0 DECISION 改写，原「OpenMontage+数字人评估」缩减进子项与 Backlog）
- [x] **目标**：`pixelle` 引擎接入 VideoEngine 体系，承接「AI 生成类内容」（知识科普/读书/情感类）；MPT 保持默认兜底（时效资讯量产）
- **步骤**：
  1. Docker Compose 部署 Pixelle-Video（`ATH-MaaS/Pixelle-Video`，pin image tag）；生图供应商 key（DashScope/RunningHub 按量）入 `secrets/` 与 config
  2. `creators/video/pixelle.py` 实现 VideoEngine：submit→`POST /api/video/generate/async`（**mode=fixed** 注入我方口播稿，文案主权在创作管道；显式传 title 避免其 LLM 代写）、poll→`GET /api/tasks/{id}`（**以 status 为准**，progress 对 video 任务未接线恒 null）、fetch→文件下载（**完成任务 24h 后被服务端清理，需及时取**）。适配要点：aspect→frame_template 尺寸目录；style→frame_template+prompt_prefix；duration_s 按语速预估校验；**轮询 404（其任务状态存内存，服务重启即丢）按 failed 处理+重提交**；脚本分段在我方预处理为段落（其 API 未暴露 split_mode，默认 paragraph = `\n\n` 双换行分镜边界）
  3. 每条视频记录生成成本（约 ¥0.5-5/条）入 llm_calls 或独立审计
  4. 引擎路由：config 按内容 pillar/类型选 `mpt` 或 `pixelle`
  5. （时间盒 0.5 天，可选）AIGCPanel 数字人可行性速评，结论进 Backlog
- **验收**：一条 gated content 经 pixelle 引擎端到端产出 mp4，视觉质量明显优于 MPT 同稿产出；pixelle 服务挂掉时 mpt 链路不受影响
- **参考**：TECH_SPEC §5.6；evaluation-notes §4
- 备注：OpenMontage 降级为远期观察（Backlog），Pixelle-Video 接管精品/差异化视觉定位

  ✅ 完成于 2026-07-06，commit 731cc75，备注：`pipeline/creators/video/pixelle.py::PixelleEngine` 实现 VideoEngine（submit/poll/fetch + run_to_completion）。**契约要点**：mode=\"fixed\" 跳过 Pixelle LLM 写稿（文案主权）；title 走 req.style[\"title\"] 注入（VideoRequest 契约无 title 字段，TECH_SPEC §5.6 零改动）；aspect → frame_template 映射（9:16→1080x1920 等三种）；text 双换行分段 = 分镜边界；progress 强制 None（不被假象百分比骗，evaluation-notes §4 复核）；**404 → CreateError(\"task lost\") 让编排层立即重提交（不静默重试）**；COMPLETION_TTL_HOURS=24 警示。config 新增 PixelleConfig（base_url / poll_interval_s / timeout_s / voice / prompt_prefix）+ factory 接入 build_video_engine(cfg)。**未做**：docker-compose 部署 Pixelle-Video + 真账号 DashScope key + 真实 mp4 质量验收（用户上线前必做）。tests 30 新增（unit 29 + 1 真 e2e 走 fake Pixelle uvicorn），全测 722 绿（原 692 + 30）。**AIGCPanel 数字人速评留 Backlog**（M5-3 时间盒外）。

**🏁 M5 里程碑验收**：视频 lane 稳定日更（MPT 引擎），扩展引擎有明确 DECISION。

---

## M6 — 数据回流与优化（预计 2-3 天，之后进入运营期）

### M6-1 collect：表现数据回流
- [x] **目标**：发布 24h/72h 后抓取 views/likes/comments 入 metrics 表
- **步骤**：X 走 API；头条/小红书用登录态抓自己主页的创作者数据（只读自己的数据，合规）；失败静默重试次日
- **验收**：published 内容 48h 后 metrics 表有数据
- **参考**：ARCHITECTURE §3.8

  ✅ 完成于 2026-07-06，commit fe94a04，备注：`pipeline/metrics/collectors.py` 4 个 collector + `runner.py` 编排。X API v2 走公共指标解析（impression_count → views；shares = retweet+quote）；头条/抖音走 Playwright + storage_state 抓创作者后台；小红书占位（XiaohongshuSkills 未公开标准化 metrics 命令，无 probe_fn 时 collect 返回 None，等 XHS 项目公开标准化命令时接入）。**失败语义**：401/403/429/网络异常 → 静默返回 None → 编排层不阻断其他 publication；明日 cron 再试（HARD_PARTS §5 metrics 表天然时间序列幂等）。`cmd_collect` 替换 M0-1 占位。tests 26 新增（X 7 + 中文平台 5 + 工厂 4 + 编排 5 + 候选 2 + 边界 3），含「多次 collect 时间序列幂等」「单条失败不阻断」验证。全测 748 绿（原 722 + 26）。**集成 TODO**（用户上线前）：① 头条/抖音/小红书的真实后台页面改版时修订 `_parse_*_manage_html` 启发式；② 抖音视频发布时间 1h 内的发布应在 24h 后才有足够数据。

### M6-2 周报与门禁校准
- [x] **目标**：每周一自动生成 `output/weekly-report.md`
- **步骤**：内容：发布数/门禁通过率/丢弃率，各平台 top3 与 bottom3，LLM 成本，门禁分与实际表现的相关性散点（文本表格即可）；门禁分数直方图（HARD_PARTS §3 要点 4）
- **验收**：真实数据生成一份周报，能指导下周选题
- **参考**：HARD_PARTS §3 §4

  ✅ 完成于 2026-07-06，commit 8339f4d，备注：`pipeline/report/weekly.py::collect_weekly_report` + `render_markdown` + `write_weekly_report`。4 个 section：① 概览（topics/contents/gate_pass_rate/discard_rate 含除零保护）② 各平台 top3 + bottom3（按 views，views=0 占位显示）③ LLM 成本按 stage 分组 + 排序 ④ 门禁校准（HARD_PARTS §3 要点 4）— ASCII 直方图（5 buckets）+ Pearson r（|r|<0.2 提示重新校准锚点，样本<2 返 None）。`cmd_report weekly` 子命令默认写 `output/weekly-report.md`（`--output` 可覆盖）；flock 复用 stage_lock('collect')。空数据兜底（直方图全 0 / 相关性 None / 平台 ranking 空 dict）。tests 16 新增（overview 2 + 平台 2 + LLM 2 + 直方图 2 + 相关性 3 + 渲染 3 + 落盘 2），全测 764 绿（原 748 + 16）。**集成 TODO**（用户上线前）：配置 launchd/cron 每周一自动跑 `python -m pipeline.run report weekly`。### M6-3 免审直发（可选，运营 1 个月后）
- [ ] **目标**：`review.policy: auto_above:27` 生效
- **前置**：过去 30 天人审通过率 ≥ 85% 且无平台违规记录，否则不开
- **验收**：高分内容跳过人审直接 approved，日志清晰标注 auto-approved

  ⏸️ **未启动**（2026-07-06）：前置条件不满足——过去 30 天无真实发布数据。**真实运营满 30 天后再启动本任务**。预计届时需要：① 跑 M6-1 collect 累计 30 天 metrics ② 改 review.runner.py 在 policy=auto_above:N 时跳过人工 review ③ audit trail 留「auto-approved」标签 ④ 加测试。

---

## M7 — 工程健壮性与体验优化（架构师 review 追加，2026-07-06）

> 本节由架构师通读全仓后追加。分两组：**R = 健壮性/正确性/测试/规范**（先做，防线性问题）、**U = UI/UX 友好化**（核心诉求：让日常操作不再全程敲 terminal）。
> **执行者注意（弱模型必读）**：每个任务已写明「错在哪（文件:行号）/ 怎么改 / 红线（不许动什么）」。严格照做，**不要顺手改契约**（models.py 字段、SQL schema、Adapter 方法签名、TECH_SPEC §3/§4/§5 一律不动）。改动前先 `git pull` 确认行号，行号漂移就用「错误代码原文」定位。每个任务做完单独 commit，跑 `python -m pytest tests/ -q` 全绿才算完成。

### R7-1 修 webui 连接与时间 API 三处隐患（低风险，先做热身）
- [x] **目标**：消除 webui 每请求重开连接、每请求跑 DDL、以及弃用的 `utcnow()`
- **错在哪**：
  1. `pipeline/webui/app.py:50-53` `_conn()` 每次请求都 `db.init_db(c)`——`init_db` 会执行全部 `CREATE TABLE IF NOT EXISTS` DDL，**每个 HTTP 请求跑一遍建表语句**，纯浪费且拖慢页面
  2. `pipeline/webui/app.py:123` 用了 `datetime.utcnow()`——本项目跑在 Python 3.14，该 API 已 deprecated，会打 warning 且未来移除
- **怎么改**：
  1. 在 `create_app()` 内、返回 app 前**只调用一次** `db.init_db`（用一个临时连接建表后 close），把 `_conn()` 里的 `db.init_db(c)` 删掉，`_conn()` 只保留 `db.connect(_DB_PATH)`。这样每请求仍新开连接（SQLite 下可接受）但不再重复建表
  2. `app.py` 顶部已 `from datetime import datetime`；把第 123 行 `datetime.utcnow().isoformat()` 改为 `datetime.now(timezone.utc).isoformat()`，并在 import 段加 `from datetime import timezone`（或改成 `from datetime import datetime, timezone`）
- **验收标准**：`tests/test_webui*.py` 全绿；新增 1 个断言测试——patch `db.init_db` 后连续发 3 个 `GET /api/status`，断言 `db.init_db` 被调用次数 ≤ 1（证明不再每请求建表）
- **红线**：不要改 `db.py` 的 `init_db` 本身；不要改路由签名
- **参考**：TECH_SPEC §7

  ✅ 完成于 2026-07-07，commit a842678，备注：`_conn()` 删 init_db、create_app 内一次性 `db.init_db(_init_c)`（lines 96-100），`dashboard` 路由改 `datetime.now(timezone.utc).isoformat()`（line 130）。tests/test_webui_r7_1.py 6 用例全绿。

### R7-2 修 /output 与 /static 挂载时机 → 图卡/预览 404（HIGH，影响审核体验）
- [x] **目标**：`output/` 目录在 webui 启动后才生成时，图卡 PNG 仍能被访问
- **错在哪**：`pipeline/webui/app.py:97-112`——`/output` 和 `/static` 用 `if output_dir.exists(): app.mount(...)` 挂载。若启动 webui 时 `output/` 还不存在（新机器、当天还没 create），之后流水线生成了图卡，**这些图片永远 404，直到重启 webui**。审核台/详情页的 `<img>` 全裂
- **怎么改**：
  1. 挂载前确保目录存在：把条件挂载改成 `output_dir.mkdir(parents=True, exist_ok=True)` 后**无条件** `app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")`
  2. `/static` 目录是仓库自带资产（`pipeline/webui/static/`），正常存在，保留即可；但同样去掉 `if` 直接挂（该目录已随代码提交）
- **验收标准**：新增测试——先删除/不创建 `output/`，`create_app()` 后再 `mkdir output/2026-01-01 && 写一个 x.png`，请求 `GET /output/2026-01-01/x.png` 返回 200 且 content-type 为 image/png
- **红线**：`/output` 必须只读（StaticFiles 默认只读，别加写路由）；不要把 `output/` 加进 git（`.gitignore` 已忽略，别动）
- **参考**：TECH_SPEC §7「/output 挂静态目录，只读」

  ✅ 完成于 2026-07-07，commit 71e9703，备注：`output_dir.mkdir(parents=True, exist_ok=True)` 后无条件 `app.mount("/output", ...)`（lines 105-111），`/static` 同样去掉 if 条件。tests/test_webui_r7_2.py 5 用例全绿。

### R7-3 webui 直写 SQL 违反 §7 契约 → 抽到 db.py 助手函数（MEDIUM）
- [x] **目标**：消除 UI 层里的裸 `UPDATE` SQL，遵守 TECH_SPEC §7「**UI 不得直接写 SQL**」
- **错在哪**：TECH_SPEC §7 明文规定「读走 db.py 查询函数，写走 transition() 与既有编排函数」。但：
  1. `pipeline/webui/app.py:215-221` reject 分支直接 `conn.execute("UPDATE contents SET gate_verdict=?, updated_at=? WHERE id=? AND status=?")`
  2. `pipeline/webui/app.py:272-278` reschedule 直接 `conn.execute("UPDATE publications SET scheduled_at=?, updated_at=? WHERE id=? AND status=?")`
  这两处是 UI 层裸 SQL，违反契约、且逻辑散落难测
- **怎么改**：
  1. 在 `pipeline/db.py` 新增两个纯函数（放在文件里现有 update/transition 函数附近，保持风格一致）：
     - `set_gate_verdict(conn, content_id, verdict, *, expect_status) -> int`：执行那条 `UPDATE contents SET gate_verdict=?, updated_at=? WHERE id=? AND status=?`，返回 `cursor.rowcount`，内部 `conn.commit()`
     - `reschedule_publication(conn, pub_id, scheduled_at, *, expect_status) -> int`：执行那条 `UPDATE publications SET scheduled_at=?`，返回 rowcount
  2. `app.py` 两处改为调用新助手，判 `rowcount != 1` 走原有 `_alert(...)` 分支。行为完全等价，只是把 SQL 挪进 db 层
- **验收标准**：`app.py` 里 `grep "conn.execute" pipeline/webui/app.py` 除了 `_status_counts` 的只读 SELECT 外无写 SQL；新增 `tests/test_db.py` 用例覆盖两个新函数（状态匹配返回 1、状态不匹配返回 0）；webui 现有测试全绿
- **红线**：**不要改 SQL 语义**（字段、WHERE 条件一字不改，只是搬家）；不要改 schema；`transition()` 已有的状态转移调用（app.py 里 approve/promote/cancel/retry 那些）保持不动，它们已经合规
- **参考**：TECH_SPEC §7、§8

  ✅ 完成于 2026-07-07，commit 2788fd4，备注：db.py 新增 `set_gate_verdict` / `reschedule_publication` 两个纯函数（保留 SQL 语义，字段/WHERE 一字不改），app.py reject 分支 (line 223-228) 与 reschedule 分支 (line 278-285) 改为调助手。tests/test_webui_r7_3.py 7 用例全绿。

### R7-4 metrics 裸吞异常违反 §8 → 补结构化日志（MEDIUM）
- [x] **目标**：让「失败静默重试次日」的 metrics 路径留下可排障日志，遵守 §8「禁止裸 except: pass」
- **错在哪**：TECH_SPEC §8 规定「任何 except 分支必须要么 re-raise 要么 log.warning 以上级别记录」。但 `pipeline/metrics/collectors.py` 与 `pipeline/metrics/runner.py:122,130` 有大量 `except Exception:` 后只 `failed += 1; continue`，**一个字都不记**。线上 metrics 抓不到数时无从排障
- **怎么改**：
  1. 用 `pipeline/utils/log.py` 的结构化 logger（其它模块的用法照抄），在每个 `except Exception as e:` 分支加一行 `logger.warning(...)`，**必带 `stage="collect"` 与 `ref_id=<publication_id>`**（§8 要求每条日志带 stage+ref_id），message 含 `repr(e)`
  2. 注意把 `except Exception:` 改成 `except Exception as e:` 才能拿到异常对象
  3. runner.py 与 collectors.py 里所有这类裸吞点都要补（前面 grep 已列出行号：collectors.py 的 96/143/180/227/285/324/364/396，runner.py 的 122/130）——逐个补，别漏
- **验收标准**：`grep -n "except Exception" pipeline/metrics/` 每一处下方 3 行内都能看到 `logger.warning`；新增测试——mock collector 抛异常，断言 logger 收到一条含该 publication_id 的 warning（可用 `caplog`）
- **红线**：**不要改控制流**——失败仍是 `failed += 1; continue`（§8 允许「记录后继续」，metrics 是非关键路径，不能因单条失败阻断编排）；不要 re-raise
- **参考**：TECH_SPEC §8；HARD_PARTS §5（collect 幂等）

  ✅ 完成于 2026-07-07，commit 42e0c53，备注：collectors.py 11 处 + runner.py 2 处 `except Exception` 全部补 `logger.warning(... extra={"stage": "collect", "ref_id": pub.id})`，控制流不变（仍是 failed+=1; continue）。tests/test_metrics.py 全测绿。

### R7-5 补 tests/test_e2e_dryrun.py（§9 必测项缺失，HIGH）
- [x] **目标**：补上 TECH_SPEC §9 明确要求但**至今不存在**的端到端 dry-run 集成测试
- **错在哪**：TECH_SPEC §9 白纸黑字：「集成测试 `tests/test_e2e_dryrun.py`：造一个假 topic，全流程跑到 publish --dry-run」。现仓库只有 `test_toutiao_e2e.py`/`test_douyin_e2e.py`（单平台），**没有全链路 dry-run 测试**。这是里程碑级验收漏洞
- **怎么改**：新建 `tests/test_e2e_dryrun.py`：
  1. 用临时 db（`db.connect(":memory:")` 或 tmp_path 下的 state.db）+ `db.init_db`
  2. LLM 全程走 `MockProvider`（llm.py 已有），**不打真实网络**；平台发布用 `MockPublisherAdapter`（safe_publish.py 已有）
  3. 造一个假 topic 插入 → 依次调用各阶段编排函数（score_all → create → gate runner → review 落库 approved → scheduler.plan → safe_publish dry_run=True），断言最终 publication 记录存在且**真实 publish 未被触发**（`publish.enabled=false` 时断言 mock.publish 的 call_count==0，或 dry_run 分支返回 published=False）
  4. 参考现有 `tests/test_publish*.py`、`tests/test_gate*.py` 的 mock 装配方式，别自己发明
- **验收标准**：`pytest tests/test_e2e_dryrun.py -q` 绿；测试内断言覆盖「全链路状态推进正确」+「dry-run 下 PublisherAdapter.publish 真实动作未发生」（§9 必测第 4 条）
- **红线**：**mock LLM/平台可以，绝不 mock 状态机**（HARD_PARTS §10 第 3 条）——状态转移必须走真实 `db.transition`；不要为了让测试过而改生产代码逻辑
- **参考**：TECH_SPEC §9；HARD_PARTS §5、§10

  ✅ 完成于 2026-07-07，commit 19e5d0c，备注：tests/test_e2e_dryrun.py 455 行——临时 db + MockProvider + MockPublisherAdapter 跑全链路 raw→scored→draft→gated→approved→queued→dry_run；断言状态推进正确 + publish 真动作未触发。状态机全程走真实 db.transition（不 mock 状态机，红线遵守）。

### R7-6 文档 commit 补齐 + mypy 声明对齐现实（LOW，卫生）
- [x] **目标**：消除文档里 4 处历史 commit 悬空占位符（M1-4/M1-5/M2-1/M5-2 完成备注），并让「mypy --strict 强制」的声明与现实一致
- **错在哪**：
  1. `docs/TASKS.md` 有 4 处历史 commit 占位符（M1-2/M1-4/M2-1/M5-2 的完成备注），无法追溯到真实 sha
  2. TECH_SPEC §10 声称「`mypy --strict pipeline/` 通过（M2 起强制）」，但仓库**没有任何 mypy 配置**（无 mypy.ini/pyproject.toml/setup.cfg），CI 也没跑——这是一句无人执行的空头承诺
- **怎么改**：
  1. 对每处历史占位符：用 `git log --oneline -- <该任务涉及的文件>` 找到对应提交 sha，把占位符替换为真实短 sha。**找不到确切对应的**就替换为 `<历史提交，sha 已无法精确追溯>` 并保留备注，不要瞎填
  2. mypy 二选一（推荐 A，改动小）：
     - **A. 降级声明**：把 TECH_SPEC §10 那句改为「mypy --strict 为**目标**，尚未接入 CI 强制」，与现实对齐，不装
     - **B. 真接入**：加 `mypy.ini`（`[mypy]\nstrict = True\nfiles = pipeline`），跑 `mypy pipeline/`，把报出的类型错误**单独开任务**修（本任务只负责加配置 + 记录错误数量到本任务备注，不在此任务里修全部类型错——那是另一个大工程）
- **验收标准**：在 `docs/TASKS.md` 搜索该历史占位符 token 应无输出（说明所有历史占位符已替换为真实 sha）；TECH_SPEC §10 的 mypy 声明与仓库实际状态一致
- **红线**：这是文档任务，**不要改任何 pipeline/ 代码逻辑**（除非选 B 加 mypy.ini 配置文件，那也只加文件不改逻辑）
- **参考**：TECH_SPEC §10；git-workflow 全局规则

  ✅ 完成于 2026-07-07（本次 R7 长程），commit <本 commit sha>，备注：4 处占位符全替换为真实 sha（M1-4: 310cf09 / M1-5: b58f083 / M2-1: 32d0972 / M5-2: 256abf5，均从 git log --oneline -- <files> 找到对应提交）；TECH_SPEC §10 mypy 声明降级为「目标，尚未接入 CI 强制」（选 A 方案：仓库无 mypy 配置，CI 不跑，与现实对齐不装）。未改任何 pipeline/ 代码（红线遵守）。

---

> ⚠️ **U7-1 ~ U7-6 已被 M10 取代（2026-07-09，用户决策）**：改按「蚁小二」形态用 **Vue3 + Ant Design Vue SPA** 重做整个前端外壳。U7 系列（驾驶舱 / 运行台 / 图卡预览 / vendor htmx / 日志页 / UI 发布）的诉求**全部并入 M10**（见本文件末尾）。**不要再做 U7-1~U7-6**——它们保留在此仅作历史与需求出处。当前应认领的第一个任务是 **M10-0**。

### U7-1 Dashboard 升级：从三张裸表 → 运营驾驶舱（HIGH，体验核心）
- [ ] **目标**：打开首页 30 秒看懂「系统在干什么、花了多少钱、有没有出问题、有什么待我处理」，不再只是三张 status 计数表
- **错在哪**：`pipeline/webui/templates/dashboard.html` 现在只有 topics/contents/publications 三张 `status→count` 表（见文件全文 36 行），**没有成本、没有预算余量、没有告警、没有待办、没有近期活动、计数不可点击钻取**。运营者看不出任何有价值信息
- **怎么改**（后端 `app.py` 的 `dashboard` 路由 + 新增查询；前端 `dashboard.html`）：
  1. **成本卡片**：在 `db.py` 新增只读查询 `sum_llm_cost_this_month(conn) -> float`（`SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE created_at >= <当月1号ISO>`）与 `count_llm_calls_today(conn)`。dashboard 传入模板，顶部渲染「本月 LLM 花费 $X / 预算 $Y（从 `config.budget.monthly_usd` 读）→ 进度条 + 剩余额度」。预算用满 80% 显示黄色、100% 红色
  2. **待办卡片**：显著展示「🔴 N 篇待审」（gated 计数，链接到 `/review`）、「🟡 N 条待发布」（queued 计数，链接 `/calendar`）、「⚠️ N 条发布失败」（failed 计数）。这几个数字要大、要能点
  3. **近期活动**：新增查询取最近 10 条 `contents`（按 updated_at desc）与最近 10 条 `publications`，渲染成时间线小表，每行链到详情页
  4. **计数可钻取**：三张状态表的每个 status 单元格包成链接（topics→`/topics?status=X`，contents→审核/详情，publications→`/calendar`）
  5. **自动刷新**：用 htmx `hx-get="/api/status" hx-trigger="every 30s"` 让计数区无刷新更新（`/api/status` 已存在）
- **验收标准**：`GET /` 返回 200 且含「本月花费」「待审 N 篇」字样；预算进度条按 mock 数据正确变色；点「待审 N 篇」跳到 `/review`；新增 db 查询函数各有单测（当月成本求和、今日调用数）
- **红线**：**只读**——dashboard 不做任何写操作；成本查询别改 `llm_calls` 表；预算数字从 config 读，不要硬编码
- **参考**：TECH_SPEC §7；HARD_PARTS §4（成本可见性）

### U7-2 一键运行流水线：UI 触发各阶段，告别全程 terminal（HIGHEST，用户明确诉求）
- [ ] **目标**：在 Web 控制台点按钮就能跑 `ingest/score/create/gate/schedule/collect`，看到实时结果与摘要，**日常运营不再需要开终端敲命令**（发布 publish 因高危单独放 U7-6 做，不在本任务）
- **背景**：这是用户最强烈的诉求——「别让我全程通过 terminal 操作」。目前 UI 只能审核，其余阶段全靠手敲 `python -m pipeline.run <stage>`
- **怎么改**（新增一个「运行台 /runs」页面 + 后台任务执行）：
  1. **执行封装**：新建 `pipeline/webui/runner_bridge.py`。提供 `run_stage(stage: str) -> RunResult`：内部**不重复实现业务**，而是复用 `pipeline.run` 里对应的 `cmd_*(args)` 函数（构造一个最小 `argparse.Namespace(config="./config.yaml", verbose=False)` 传入）。捕获其 stdout 摘要行与 exit code，返回 `RunResult(stage, ok, summary_text, started_at, finished_at)`。**只允许白名单阶段**：`{"ingest","score","create","gate","schedule","collect"}`——`publish` 不进白名单（发布必须留在受控 CLI/cron 路径）
  2. **并发安全**：`cmd_*` 已被 `_stage_lock` 装饰（flock），UI 触发时若 cron 正在跑会自动 SKIP——这正是我们要的，不用额外加锁。但**UI 请求本身要异步**：用 FastAPI `BackgroundTasks` 或一个简单的内存任务表（`dict[run_id, RunResult]`），点按钮立即返回「已提交」，页面轮询结果，避免长阻塞 HTTP
  3. **路由**：`POST /runs/{stage}`（stage 必须在白名单，否则 `_alert`）触发后台执行；`GET /runs` 展示各阶段「上次运行时间 / 结果摘要 / 成功失败」；`GET /runs/status` 给 htmx 轮询用
  4. **前端**：`templates/runs.html`——每个白名单阶段一个卡片：阶段名 + 「▶ 运行」按钮（`hx-post="/runs/{stage}"`）+ 上次结果区（`hx-get="/runs/status" hx-trigger="every 3s"` 刷新）。base.html 导航加「运行台」入口
  5. **顺序提示**：页面顶部标注推荐顺序 `ingest → score → create → gate → (人审) → schedule`，并对「有前置未满足」给文字提示（不强制拦截，只提示）
- **验收标准**：浏览器点「运行 ingest」→ 页面显示「运行中…」→ 数秒后显示摘要（如 `ingest: 0 fetched...`）且 exit 状态正确；`POST /runs/publish` 返回 400 拒绝（白名单外）；单测覆盖：`run_stage("ingest")` 复用 `cmd_ingest` 且返回结构正确、`run_stage("publish")` 抛拒绝、后台任务不阻塞请求
- **红线（务必遵守）**：
  - **本任务白名单不含 `publish`**——不是因为 publish 不能进 UI，而是因为它是唯一不可逆的高危动作，值得**单独一个任务**（U7-6）加二次确认/先 dry-run 等护栏后再上，避免和这 6 个安全阶段混在一起被草率实现。本任务只做到 `schedule`
  - **不要重写各阶段业务逻辑**——`runner_bridge` 必须复用现有 `cmd_*`，只做「构造 args + 捕获输出 + 记录结果」这层薄封装
  - 不要引入 Celery/Redis 等重型队列（KISS）——`BackgroundTasks` + 内存 dict 足够，本系统是单机单用户
- **参考**：TECH_SPEC §2（CLI 契约，各 cmd 的摘要行格式）、§7；HARD_PARTS §1、§8；CLAUDE.md 工作约定第 7 条

### U7-6 UI 发布（带护栏，独立高危任务）（HIGH，在 U7-2/R7-2 之后做）
- [ ] **目标**：在 Web 控制台也能触发真实发布——但因为发布**不可逆**（发出去删不掉、矩阵账号重复内容会被风控），必须比其它阶段多套护栏。**这是全项目风险最高的 UI 功能，单独成任务、单独 review**
- **为什么单独拆出来**：发布进 UI 本身没问题（更方便，用户诉求合理），真正的风险从来不是"UI vs 终端"，而是"点一下就不可逆"。所以把它从 U7-2 的安全阶段里剥出来，加够护栏再上
- **怎么改**：
  1. **总闸不变**：`config.publish.enabled` 仍是硬开关。`false` 时 UI 发布按钮显示为**禁用态**并提示"发布总闸未开启（config.publish.enabled=false）"，点了也走不到真实发布——这层由 `safe_publish` 天然保证，UI 只是把状态展示出来
  2. **先 dry-run 后真发**：UI 上一条 queued publication 提供两个按钮——「🔍 预演(dry-run)」先跑一遍 `safe_publish(dry_run=True)` 把 validate 结果/将要发的标题正文展示出来；确认无误后「🚀 确认发布」才 `dry_run=False`
  3. **二次确认**：「确认发布」必须弹二次确认（htmx 可用一个展开的确认条：显示"你正在向 <platform>/<account> 发布 <title>，此操作不可撤销"+ 一个「我确认」按钮），不能单击直接发
  4. **复用安全框架**：后端**必须**调 `pipeline/publishers/safe_publish.py` 的现成编排（三层防御：config 锁 / 乐观锁抢占 / UNIQUE 兜底 + INTENT 日志都在里面），**绝不绕过它自己拼发布逻辑**。路由 `POST /publications/{id}/publish?dry_run=true|false`，后台异步执行（同 U7-2 的 BackgroundTasks 模式，因为浏览器自动化要几分钟）
  5. **发布中锁 UI**：一条 publication 进入 `publishing` 状态后，其 UI 行禁用所有按钮，避免并发点击（乐观锁在 DB 层已防重，但 UI 也别诱导用户去点）
- **验收标准**：`publish.enabled=false` 时按钮禁用且后端拒绝真实发布（断言 mock adapter.publish 未被调用）；`enabled=true` 时「预演」返回 validate 结果不真发、「确认发布」经二次确认后才走 `dry_run=False`；全程复用 `safe_publish` 不重写；单测覆盖 enabled 开/关两条路径 + dry-run 与真发分流
- **红线**：
  - **必须复用 `safe_publish`**——它是 M4-1 的三重锁核心，绕过它 = 破坏防重复发布防线（HARD_PARTS §1，全系统最高优先级正确性问题）
  - **不许移除或弱化二次确认与总闸**——这是 UI 发布之所以敢做的前提
  - 不改 `publications` 表 schema、不改 `PublisherAdapter` 签名、不改状态转移表
- **参考**：HARD_PARTS §1（必读）；TECH_SPEC §5.2、§7；M4-1 `safe_publish.py`；CLAUDE.md 工作约定第 7 条

### R7-7 登录骨架抽取 + 结构化日志（MEDIUM，U7-7 前置）
- [x] **目标**：把 `pipeline/publishers/login_cmd.py` 中 `login_toutiao` / `login_douyin` 100% 同构的 Playwright 骨架（launch → new_context → 遍历 URL fallback → wait_for_url 离开登录路径 → storage_state 落盘 + chmod 600 → finally browser.close）抽成私有 `_playwright_login_run(profile, account, *, timeout_s)`，引入 `LoginProfile` dataclass（`platform / selectors_module / exit_keywords`）消除复制粘贴。**同步**把所有 `print(...)` 换成 `pipeline.utils.log.log_event(stage="login", ref_id=account)`，让登录进度可被结构化消费（U7-7 的 Web UI 靠这条链路推送给前端）。
- **怎么改**：
  1. `login_cmd.py` 顶层加 `LoginProfile` frozen dataclass + `from dataclasses import dataclass` + `from pipeline.utils.log import get_logger, log_event` + `import logging`
  2. 新增私有 `_playwright_login_run(profile, account, *, timeout_s) -> Path`，把现有 `login_toutiao` 函数体的 90% 搬过来（保持 lazy import playwright / `with sync_playwright()` / finally close 等结构），用 `profile.platform` / `profile.selectors_module.PROFILE_URL_FALLBACK` / `profile.exit_keywords` 替换原硬编码
  3. 把函数内 5 处 `print(f"[login/<platform>/<account>] ...")` 换成 `log_event(_LOG, logging.INFO, "...", stage="login", ref_id=account)`（用模块级 `_LOG = get_logger("pipeline.publishers.login")`）
  4. `login_toutiao` / `login_douyin` 改成 thin wrapper：构造 `LoginProfile(...)` 调 `_playwright_login_run`
  5. `login_xiaohongshu` 的 1 处 `print(...)` 也换成 log_event（保持模块日志统一；**不**并入 helper，subprocess 路径骨架不同）
  6. `__all__` 不变（外部 import 不受影响）
- **验收标准**：
  - `python -m pytest tests/test_login_cmd.py -q` 全绿（原 dispatch / 存盘 / chmod 600 / 超时抛 PublishError / xhs subprocess / douyin 用例全数保留）
  - `tests/test_login_cmd.py` 新增 1 个用例：断言 `log_event` 至少被调用 3 次（starting / 等待用户 / saved），用 `monkeypatch.setattr` 或 `caplog` 验证 stage/ref_id 正确
  - 手动：`python -m pipeline.run login toutiao main` 仍能跑通（Mac 弹 chromium → 扫码 → 关闭 → `secrets/cookies/toutiao_main.json` chmod 600）
  - `run_login("unknown_platform", ...)` 行为不变（仍抛 PublishError + supported 列表）
- **红线**：
  - `login_xiaohongshu` **不**并入 `_playwright_login_run`（subprocess 路径骨架不同，强行抽象会引入分支地狱）
  - 不改 `run_login` 签名 / `_PLATFORM_LOGIN_DISPATCH` 内容 / `__all__`
  - 不改 `pipeline/models.py` / SQLite schema / `PublisherAdapter` 签名 / `TECH_SPEC.md`
- **依赖**：U7-7 必须等本任务完成才能开始（前端进度靠 log_event 链路推进）
- **参考**：U7-2 后台任务范式、`pipeline/utils/log.py::log_event`、`tests/test_login_cmd.py` 现存 Playwright MagicMock 配方

✅ 完成于 2026-07-12，commit 91b36a7，备注 抽 LoginProfile + log_event 链路；xhs 不并入 helper 守红线；run_login/__all__/dispatch 零变化；tests/test_login_cmd.py 21 用例全绿 + 全量无回归；chromium+扫码手动验证留给用户在 Mac 上执行。

### U7-7 Web UI 一键登录账号 + 后台编排（HIGH，用户明确诉求「告别 terminal」）
- [x] **目标**：在 `PlatformCatalogModal.vue` 的 scan_qr 平台卡片加「🚀 一键登录」按钮 → 调 `POST /api/v1/accounts/{platform}/{account}/login` → 后端用 FastAPI `BackgroundTasks` 跑 `run_login()` → 前端轮询 `GET /api/v1/runs/{run_id}` 看实时进度（消息来自 R7-7 的 log_event 链路）→ 完成后 toast「登录完成」+ 自动刷新账号健康状态。**用户全程不离开浏览器**。
- **怎么改**：
  1. **runs registry 扩字段**：`pipeline/webui/api/runs.py` 加 `update_run_message(run_id, message)` 函数（写 `_RUNS[run_id]["message"]` + `"message_at"`），**不破坏**既有 `register_run` / `GET /runs/{run_id}` 行为（dict 自由扩展向前兼容）
  2. **登录 bridge**（新文件 `pipeline/webui/login_bridge.py`）：`execute_login_run(run_id, platform, account, progress_cb)` 调 `run_login()`，把每次 log_event 通过 `logging.Handler` 子类透传给 `progress_cb` → `update_run_message`；成功 / PublishError / 其他异常分别写 runs registry 不同 status
  3. **API 端点**（`pipeline/webui/api/accounts.py` 新增）：
     - `POST /api/v1/accounts/{platform}/{account}/login` → 校验 platform ∈ {toutiao,xiaohongshu,douyin} + 同账号互斥（`_LOGIN_RUNS: dict[(platform,account), run_id]`，运行中返回 409）→ 生成 run_id → `register_run` + `background.add_task(_run_login_then_cleanup, ...)` → 202 `{"run_id":..., "status":"queued"}`
     - 错误信封用 `HTTPException(detail={"error":{"code","message"}})`（与 runs.py / publish.py 一致；与本文件既有 GET 端点的「裸 dict + 软失败」风格**有意不一致**，写操作必须标准错误信封，前端 `unwrapError` 能解）
  4. **前端 Pinia store**：`frontend/src/stores/index.ts::useAccountsStore` 加 `loginAccount(platform, account)` action + `runningLogins: Map<run_id, {platform, account, status, message}>` state；setInterval 轮询 `GET /runs/{run_id}`（1.5s 一次），succeeded/failed 时 clearInterval + 5min 兜底；succeeded 时调 `load()` 刷新账号健康
  5. **前端 Modal**：`PlatformCatalogModal.vue` scan_qr 分支加「🚀 一键登录」按钮（loading/disabled 绑 store 状态）+ 进度文字 `<span class="login-progress">`；保留原 CLI 命令在 `<details>` 折叠区作为兜底（远程服务器场景 + 失败重试仍需）
- **验收标准**：
  - `python -m pytest tests/webui/test_api_login.py tests/webui/test_runs_message.py -q` 全绿
    - `test_api_login.py`：首次 POST 返回 202 + run_id；第二次同账号 POST 返回 409 + run_id；BackgroundTask 跑完后 `GET /runs/{run_id}` status=succeeded；`run_login` 抛 PublishError → status=failed + error.message
    - `test_runs_message.py`：旧 `GET /runs/{run_id}` 在没 message 字段时仍正常返回（向后兼容）
  - 手动验证（Mac）：`python -m pipeline.run webui` → 浏览器开 `http://127.0.0.1:8787/accounts` → 添加账号 → 选头条 → 一键登录 → 桌面弹 chromium → 扫码 → 前端 toast「登录完成」+ 账号列表自动刷新健康
  - 失败路径：超时（5 分钟不扫码）→ toast「登录失败: login timeout after 300s」
  - 兜底：details 折叠区点开看到原 CLI 命令，复制仍可用
- **红线**：
  - **必须复用 `run_login`**（R7-7 已交付稳定接口），**不重写**登录业务逻辑
  - 进度消息必须来自 R7-7 的 log_event 链路（**不重新 print**），用 `logging.Handler` 子类桥接到 `progress_cb`
  - 同账号互斥：`_LOGIN_RUNS` dict 检查 + finally cleanup（避免 cleanup 异常吃掉 cleanup）
  - 失败后 UI 必须能恢复（按钮回到 idle 态，不卡死）
  - 不改 `pipeline/models.py` / SQLite schema / `PublisherAdapter` 签名 / `TECH_SPEC.md`
- **依赖**：**R7-7 必须先完成**（前端进度靠 log_event 链路推进，R7-7 之前 print 无法被结构化消费）
- **不做**（留作后续任务）：
  - Linux 服务器 headless 登录 / QR 截图上传 / noVNC 远程查看（需决策 headless + QR 截图 vs xvfb-run）
  - X / wechat_mp 接入（走配置文件，不属于扫码流程）
  - 登录后 cookie 健康自动重试
  - 登录流程并发上限
- **参考**：U7-2 后台任务范式（runs.py + publish.py::execute_preview）、U7-6 错误信封风格、HARD_PARTS §9 凭据安全

✅ 完成于 2026-07-12，commit dae76b1，备注 Web UI 一键登录：runs.py 加 update_run_message；login_cmd.py 加 listener API 修复 logger 监听 bug（get_logger 实际命名为 f"{name}@{log_dir}" 且 propagate=False，logging.Handler 方案零事件）；login_bridge.py 闭包按 (platform,account) 过滤并发进度；accounts.py 新增互斥 POST 端点；前端 store 轮询 1.5s + 6min 超时 + toast；40 用例全绿含端到端 listener 验证；两轮独立校验修复 5 个阻塞问题；chromium+扫码手动验证留给用户在 Mac 上执行。

### U7-8 Web UI 删除已保存登录凭据（HIGH，用户明确诉求「页面上删登录信息」）
- [x] **目标**：账号中心每个账号行加删除按钮，点击清除 `secrets/cookies/<platform>_<account>.json` 凭据文件，账号恢复「未授权」状态（不改 `config.yaml`，账号声明本身保留，可重新一键登录）。同一会话还修了 U7-7 遗留的两个前端 build 回归（`PlatformCatalogModal.vue` 导入错误 + 缺失 `</style>` 闭合标签，导致 `npm run build` 自 dae76b1 起从未真正跑通，是用户反馈「一键登录点了没反应」的根因之一，另一根因是本机有个 U7-7 之前启动的旧 webui 进程占着 8787 端口）。
- **怎么改**：
  1. `pipeline/webui/login_bridge.py` 新增 `delete_login_credentials(platform, account) -> bool`（复用 `login_cmd.DEFAULT_COOKIES_DIR` 拼路径，文件不存在返回 `False` 而非报错——幂等）+ `is_login_in_progress(platform, account) -> str | None`（暴露 `_LOGIN_RUNS` 查询，供端点判断 409）
  2. `pipeline/webui/api/accounts.py` 新增 `DELETE /accounts/{platform}/{account}/login`：platform 白名单校验（400）→ mutex 查询（409，避免跟正在写入的 cookie 文件竞态）→ 调 `delete_login_credentials` → 200 `{"deleted": bool, "platform", "account"}`
  3. `frontend/src/stores/index.ts::useAccountsStore` 新增 `deleteAccountCredential(platform, account)` action：`api.delete(...)` + toast + `load()` 刷新
  4. `frontend/src/views/Accounts.vue` 每个 `.account-row` 加 `<a-popconfirm>` + `<DeleteOutlined>` 删除按钮（`@click.stop` 防止冒泡触发卡片的 `openCatalog`），仅对 scan_qr 平台（toutiao/xiaohongshu/douyin）显示——x/wechat_mp 走 config_file，没有对应凭据文件
- **验收标准**：
  - `python -m pytest tests/webui/test_api_login.py -q` 全绿（20 例，含 7 个新增：文件存在删除成功、文件不存在幂等返回 false、不支持平台 400、登录进行中 409（且不误删文件）、`delete_login_credentials`/`is_login_in_progress` 单元测试）
  - `npm run build`（`vue-tsc -b && vite build`）无 TS/Vue 编译错误
  - 端到端 curl 验证：写入探针凭据文件 → DELETE 返回 `deleted:true` + 文件真实被删 → 再 DELETE 一次返回 `deleted:false`（幂等）→ 不支持平台返回 400
- **红线**：
  - **绝不碰 `config.yaml`**（用户明确要求：只清凭据文件，账号声明保留）
  - 复用 `login_bridge._LOGIN_RUNS` 互斥状态判断，不新增第二套互斥逻辑（DRY）
- **参考**：U7-7（复用其 mutex / 错误信封风格）

✅ 完成于 2026-07-12，commit f541be9，备注 DELETE 端点 + delete_login_credentials（幂等，只删文件不改 config）+ 前端 popconfirm 删除按钮；顺手修复 U7-7 遗留的两处前端 build 回归（导入错误 + 缺 `</style>`），npm run build 自 dae76b1 起首次真正跑通；27 个 webui login 测试全绿；curl 端到端验证。

### U7-9 小红书一键登录 CLI 参数顺序修复 + 删除账号改为彻底移除（HIGH，用户反馈「删除账户还是删不掉，登陆小红书也是失败，没有弹出浏览器」）
- [x] **目标**：修复两个用户实测发现的 bug。
  1. **小红书登录不弹浏览器**：`login_cmd.py::login_xiaohongshu` 把 `--account`/`--headless` 拼在子命令 `login` 之后，但 `cdp_publish.py` 的这两个选项是顶层 parser 选项、必须在子命令之前——argparse 直接报 `unrecognized arguments` 拒绝执行，Chrome 从未启动。同时 `subprocess.run` 没传 `capture_output`，导致 `proc.stdout`/`proc.stderr` 恒为 `None`，错误处理里 `(proc.stderr or proc.stdout)[-400:]` 抛 `TypeError: 'NoneType' object is not subscriptable`，把真正的 argparse 报错吞掉——这正是用户在页面上看到的报错文案。
  2. **删除账户删不掉**：U7-8 的决策（只清凭据文件、不碰 `config.yaml`）在用户实际用过后被明确推翻——账号行不消失，只是标红，不符合直觉。改为 `delete_login` 端点同时调 `delete_login_credentials`（删文件）+ 新增的 `config_edit.remove_account_from_config`（删 `config.yaml` 里 `platforms.<platform>.accounts[]` 对应条目），`deleted` = 两者任一发生。
- **怎么改**：
  1. `pipeline/publishers/login_cmd.py::login_xiaohongshu`：调整 `cmd` 拼接顺序（`--headless`/`--account` 在 `login` 之前）+ `subprocess.run(..., capture_output=True, text=True)`
  2. 新增 `pipeline/webui/config_edit.py::remove_account_from_config(platform, account, *, config_path=None) -> bool`：用 `ruamel.yaml`（round-trip，保留注释/格式，普通 `yaml.safe_load`+`dump` 会破坏手工维护的 `config.yaml`）定点删除 `accounts[]` 里 `id == account` 的条目；platform 未配置/account 不存在/文件不存在都是幂等 no-op 返回 `False`
  3. `pipeline/webui/api/accounts.py::delete_login` 改为同时调用两个删除函数
  4. `requirements.txt` 新增 `ruamel.yaml>=0.18`
  5. 顺手发现并修复：本机有个会话开始前就在跑的旧 `python -m pipeline.run webui` 进程（Python 不热重载），改了代码不重启进程就不会生效——这也是用户"改了还是报同样的错"的直接原因，非代码问题
- **验收标准**：
  - `python -m pytest tests/test_login_cmd.py -q` 全绿（新增真实子进程测试证明 `NoneType` 崩溃已修复，不是靠 mock 掩盖）
  - `python -m pytest tests/webui/test_config_edit.py -q` 全绿（6 例：删除目标账号、兄弟账号/其它 platform/注释保留、platform 缺失幂等、account 缺失幂等、文件缺失幂等）
  - `python -m pytest tests/webui/test_api_login.py -q` 全绿（新增 3 例：DELETE 后 config.yaml 账号条目消失且兄弟账号保留、仅 config 有记录时删除也算 `deleted:true`、端到端验证 `GET /accounts` 删除后不再返回该账号）
  - `python -m pytest tests/ -q` 无新增回归（对比修改前后失败用例集合一致，均为已知无关 flaky/环境相关失败）
- **红线**：`config_edit.py` 只做定点删除 `accounts[]` 条目，不删 platform 本身（哪怕 accounts 变空），不做进一步清理
- **参考**：U7-8（决策被本任务推翻）

✅ 完成于 2026-07-12，commit 322e6a1（小红书登录参数顺序 + None 崩溃修复）+ commit ce8c65e（删除账号彻底移除：config_edit.py + accounts.py 接线 + 测试），备注见上。

### U7-10 一键登录成功后账号未登记进 config.yaml，账号中心永远显示 0（HIGH，用户实测反馈「明明已经登录成功了，但是还是0」）
- [x] **目标**：修复用户实测发现的 bug——头条一键登录浏览器弹出、自动登录成功（读的是已有 cookie），但账号中心页面「账号数 0 健康 0/0 最后校验 从未」，登录结果完全不反映在 UI 上。
- **错在哪**：`login_bridge.py::execute_login_run` 一直以来只把 cookie/凭据文件写进 `secrets/cookies/`，从不 touch `config.yaml`；而账号中心的账号数/健康度（`collect_cookie_health`）只读 `config.yaml` 里 `platforms.<platform>.accounts[]` 声明过的账号——两者从设计上就没接起来，登录再成功也不会出现在 UI，除非账号本来就手工写在 config.yaml 里。
- **怎么改**：
  1. 新增 `pipeline/webui/config_edit.py::add_account_to_config(platform, account, *, config_path=None) -> bool`：`remove_account_from_config` 的镜像操作，用 `ruamel.yaml` round-trip 在 `accounts[]` 追加一条 `{id: account, <cookies|credentials>: secrets/cookies/<platform>_<account>.json}`；credential 字段名按 `platform.kind`（`playwright`→`cookies`，`api`→`credentials`，与 `pipeline/config.py` 判别式一致）；platform 未配置/文件不存在/账号已存在均幂等 `False`（不会替用户瞎造一个 platform 块——没有 windows 信息编不出合法配置）
  2. `login_bridge.py::execute_login_run` 在 `run_login` 成功后调一次 `add_account_to_config(platform, account)`
  3. **一次性补救**：用户此前两次已成功登录（toutiao、xiaohongshu）发生在本修复之前，凭据文件已在但从未登记进 config.yaml；手工跑 `add_account_to_config` 把这两条补登记回真实 `config.yaml`，让账号中心立刻反映已有登录状态
- **验收标准**：
  - `python -m pytest tests/webui/test_config_edit.py -q` 全绿（新增 5 例：追加到空列表、兄弟账号/注释保留、已存在幂等、platform 未配置幂等、config 文件缺失幂等）
  - `python -m pytest tests/webui/test_api_login.py -q` 全绿（新增端到端例：POST 登录成功 → config.yaml 多一条 → `GET /accounts` 能看到）
  - `python -m pytest tests/ -q` 无新增回归
- **红线**：不给 `pipeline/config.py` 加字段/改 Adapter 契约；不会为未在 config.yaml 出现过的 platform 凭空生成配置块
- **参考**：U7-9（`remove_account_from_config` 的姊妹函数）

⚠️ **本任务修复过程中发现的自引入回归（同一会话内，已修复）**：排查该问题时，`tests/webui/test_api_login.py` 里 U7-9 新增的两个测试（`test_delete_removes_account_from_config_yaml`、`test_delete_account_disappears_from_list_accounts_end_to_end`）调用真实 DELETE 端点却**没有** `monkeypatch.setattr(login_bridge, "DEFAULT_COOKIES_DIR", ...)`（`DEFAULT_COOKIES_DIR = Path("secrets/cookies")` 是相对 cwd 路径，同目录其余 delete 测试都正确做了隔离，唯独这两个漏了）——导致跑 `pytest tests/ -q` 时会删掉真实的 `secrets/cookies/toutiao_main.json`（用户当时刚登录成功、还没来得及被本任务的补救脚本读取备份的那份真实 cookie 文件，已确认丢失，无备份）。已给两个测试补上同样的隔离 patch，并用「canary 文件」验证法（在真实路径放置标记文件、跑测试、检查是否被删）确认修复后 `pytest tests/ -q` 全程不再触碰 `secrets/cookies/` 下的真实文件；顺手 grep 全仓库确认没有其它测试有相同的相对路径隔离漏洞。**用户需要重新走一次头条一键登录**（好消息：借助本任务的主修复，这次登录成功后会正确出现在账号中心，不会再显示 0）。

✅ 完成于 2026-07-13，commit c652d35，备注见上（含自引入回归修复）。

### U7-3 审核台补图卡缩略预览（MEDIUM，§7 明确要求但缺失）
- [ ] **目标**：审核时直接在页面看到小红书图卡 PNG 缩略图，不用点开文件
- **错在哪**：TECH_SPEC §7 要求审核台含「图卡缩略引用」、§图卡 PNG 直接 `<img>`。但 `pipeline/webui/templates/review.html` 全文只有一个指向 canonical.md 的文字链接（第 15 行），**没有任何 `<img>` 图卡预览**（已 grep 确认 review.html 无 png/img/slide 字样）
- **怎么改**：
  1. 后端 `app.py` 的 `review` 路由：对每条 gated content，探测其图卡目录 `output/<date>/<content_id>/xiaohongshu/`（派生阶段 M2-3/M2-4 的产出约定）下的 `*.png`，收集相对路径列表传给模板（目录不存在就传空列表，不报错）。**用只读文件系统探测，别查数据库不存在的字段**
  2. `review.html`：在每个 `<article>` 内加一个横向缩略图条，`{% for img in c.card_images %}<img src="/output/{{ img }}" style="height:120px;margin:4px">{% endfor %}`，无图时显示「（无图卡）」
- **验收标准**：造一条 gated content 且在其 `output/.../xiaohongshu/` 放 2 张 png，`GET /review` 页面含 2 个指向 `/output/...png` 的 `<img>`；无图卡的 content 显示「（无图卡）」不报错
- **红线**：**不许给 contents 表加字段**存图卡路径（TECH_SPEC §3 schema 冻结）——图卡路径靠运行时探测文件系统得到；`/output` 只读挂载已在 R7-2 修好，依赖它
- **参考**：TECH_SPEC §7；ARCHITECTURE §8（输出目录约定）

### U7-4 vendor htmx + 统一样式（LOW，去外网依赖 + 一致性）
- [ ] **目标**：webui 完全离线可用、视觉一致
- **错在哪**：`pipeline/webui/templates/base.html:7` 用 `<script src="https://unpkg.com/htmx.org@1.9.10">` 从 CDN 加载 htmx。**离线/内网/断网时整个 UI 的所有交互按钮失效**（htmx 是 UI 交互基石）；也是一个外部供应链依赖。而 pico.css 已经 vendor 在本地（`static/pico.min.css`），htmx 却没有，不一致
- **怎么改**：
  1. 下载 htmx 1.9.10 的 min.js 存到 `pipeline/webui/static/htmx.min.js`（若无外网，让用户提供该文件；文件约 47KB）。base.html 第 7 行改为 `<script src="/static/htmx.min.js"></script>`
  2. base.html 里内联的 `<style>`（第 8-17 行）与散落在 settings.html 的 `<style>` 合并到 `static/app.css` 一个文件，base.html 用 `<link>` 引入，各模板删除内联 style（DRY）
- **验收标准**：断网状态下启动 webui，页面所有 htmx 按钮（promote/approve/reschedule）仍可用；`grep -rn "unpkg\|cdn\|https://" pipeline/webui/templates/` 无外部资源引用
- **红线**：不要升级 htmx 大版本（锁 1.9.x，API 稳定）；不要引入 npm/构建链（TECH_SPEC §7「不引入 npm 构建链」）
- **参考**：TECH_SPEC §7

### U7-5 运行日志查看页（LOW，排障闭环）
- [ ] **目标**：出问题时在 UI 看最近日志，不用 ssh 去 tail 文件
- **错在哪**：`logs/pipeline.log`（结构化 json lines）与 `logs/llm/` 只能在终端看。配合 U7-2 一键运行后，运营者需要一个地方看「刚才那次运行到底报了什么错」
- **怎么改**：
  1. 后端新增 `GET /logs?stage=&limit=200` 路由：读 `logs/pipeline.log` **尾部** N 行（用高效 tail，别整文件读进内存——文件可能很大，`from collections import deque` 配合迭代读取，`maxlen=limit`），每行 `json.loads`，可选按 `stage` 过滤，倒序传模板
  2. `templates/logs.html`：表格展示 `ts / level / stage / ref_id / msg`；level=warning/error 行标黄/红。base.html 导航加「日志」入口
  3. 解析失败的行（非 json）跳过不报错
- **验收标准**：`GET /logs` 返回最近日志表格；`?stage=collect` 只显示该 stage；日志文件不存在时显示「暂无日志」不 500
- **红线**：**日志脱敏**——若某行 msg 含 `token`/`cookie`/`authorization`/`api_key` 等敏感词，该字段值用 `***` 打码再展示（HARD_PARTS §9「IM 通知内容不含 cookie/token」，UI 展示同理）；页面只读，不提供删除/清空日志的操作
- **参考**：TECH_SPEC §8（日志格式）；HARD_PARTS §9（凭据安全）

> **M7 建议执行顺序**：R7-1 → R7-2 → R7-3 → R7-4 → R7-5 → R7-6（健壮性打底），再 U7-1 → U7-2 → U7-3 → U7-4 → U7-5（体验升级），最后 U7-6（UI 发布，高危，必须在 U7-2 的后台执行框架 + R7-2 的图片可显示之后做）。U7-2 是用户最关心的「摆脱 terminal」核心，U7-6 是它的高危延伸（发布进 UI，但加二次确认 + 先 dry-run + 总闸）。每个任务独立 commit，`feat: M7 <编号> <描述>` 或 `fix: M7 <编号> <描述>`。

---

## M8 — 从「测试全绿」到「你能亲手运营」（2026-07-07 追加，用户驱动）

> **本节缘起**：用户通读进度后提出核心诉求——「每个环节我都想可视化掌控一下，能增删改查、可调度、可编辑，别让我全程敲 terminal」。评估发现：M0–M6 代码主干齐全（985 测试通过），但**从没真正本番开机过**（无 config.yaml / state.db / secrets），且 `status`/`reset` 两个子命令至今是占位符。本节补两件事：**S 组**=让系统真能开机（补占位 + 上线引导）；**A 组**=把只读看板升级为可增删改查的管理后台。
>
> **配置编辑范围（用户已拍板）**：config 编辑本轮**只做只读展示 + 当前值高亮**（U7-1 已覆盖），不做 UI 改 config.yaml。A 组聚焦「选题 / 内容 / 排期」的 CRUD。
>
> **执行者注意（弱模型必读）**：同 M7 规则——严格照「怎么改」做，**不许顺手改契约**（models.py 字段、SQL schema、Adapter 签名、TECH_SPEC §3/§4/§5 一律不动）。改动前 `git pull` 对齐行号，行号漂移就用「错误代码原文」定位。每个任务独立 commit + `python -m pytest tests/ -q` 全绿才算完成。

### 🧭 M7 + M8 全局执行顺序（总纲）

```
第一梯队（健壮性打底，先做）：  R7-1 → R7-2 → R7-3 → R7-4 → R7-5
第二梯队（让它真能开机）：      S8-1 → S8-2 → S8-3 → S8-4
第三梯队（摆脱 terminal）：     U7-1（驾驶舱）→ U7-2（运行台）
第四梯队（管理后台 CRUD）：     A8-1 → A8-2 → A8-3 → A8-4
第五梯队（体验收尾）：          U7-3 → U7-4 → U7-5
第六梯队（UI 发布，最高危，最后）：U7-6
```

> 依赖关系：A 组依赖 U7-2 的后台执行框架与 U7-1 的导航；U7-3 依赖 R7-2 的 `/output` 修复；U7-6 依赖 U7-2 框架 + R7-2 图片可显示。**按梯队顺序做最省返工。**

---

### S8-1 补实 `status` 子命令（当前是占位符，HIGH，运维基础）
- [x] **目标**：`python -m pipeline.run status` 打印真实的各状态计数表，而非占位串
- **错在哪**：`pipeline/run.py:562-564` `cmd_status` 仍 `return _not_implemented("status")`——实跑输出 `status: not implemented (M0-1 placeholder)`。但 TECH_SPEC §2 契约明列该命令要「打印各状态计数表」，M0-1 完成备注也声称 `status→exit 0`（**当时只是占位打印，未真实计数，属文档夸大**）。webui 里已有现成的计数逻辑 `pipeline/webui/app.py:_status_counts` 可参照
- **怎么改**：
  1. `cmd_status`：`db.connect(<db_path>)` 后，对 `topics`/`contents`/`publications` 三表各 `SELECT status, COUNT(*) GROUP BY status`（**只读 SELECT，不新增 schema**），按 TECH_SPEC §2 摘要风格打印，例如每表一段 `topics: raw=12 scored=5 selected=3`。db_path 从 `args.config` 加载的 config 或默认 `state.db` 取（与其它 cmd 一致，抄 `cmd_ingest` 的 config 加载方式）
  2. 保留 `@_stage_lock("status")` 装饰器不动；成功 `return 0`
  3. 顺带在同一输出追加「本月 LLM 花费」一行（`SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE created_at >= <当月1号ISO>`）——与 U7-1 的成本可见性对齐，运维一眼看到花了多少
- **验收标准**：`python -m pipeline.run status` 打印三表计数 + 本月花费且 exit 0；新增 `tests/test_status_cmd.py`：插入若干 topic/content 后调 `cmd_status`，capsys 断言输出含各 status 计数；空库时打印全 0 不报错
- **红线**：**只读**——status 绝不写库；不改 `_status_counts`（那是 webui 的，本任务是 CLI 侧，可各自实现或抽公共函数到 `db.py`，但别改 webui 行为）；不加 schema 字段
- **参考**：TECH_SPEC §2；HARD_PARTS §3 要点 4（分数分布可放 U7-1，本任务先出计数）

  ✅ 完成于 2026-07-07，commit 8e36773，备注：`cmd_status` 30 行（替换原占位 `_not_implemented`）；抽 `db.count_by_status` / `db.sum_llm_cost_this_month` 作公共只读助手（U7-1 复用）；`_DB_PATH = "state.db"` 单点可 monkeypatch；4 行输出 `topics: raw=0 ... / contents: draft=0 ... / publications: queued=0 ... / llm: this_month=$X.XXXX` 全状态列全 0，空库不报错。tests/test_status_cmd.py 12 用例（空库/三表计数/LLM 成本/只读/格式/无副作用）+ tests/test_db_status_helpers.py 6 用例（含非法表 ValueError 拒绝 + sum_llm_cost_this_month `now=` 注入）。全测 1000 绿（4 pre-existing）。契约零变更：models.py 字段 / SQL schema / Adapter 签名 / argparse / webui._status_counts 全部不动。

### S8-2 补实 `reset` 子命令（唯一逆向操作，HIGH，卡死救命）
- [x] **目标**：`python -m pipeline.run reset <id> <status>` 真正把一条记录逆向重置，而非占位打印
- **错在哪**：`pipeline/run.py:568-575` `cmd_reset` 仍是占位（只打印不落库）。TECH_SPEC §2 明列 reset 是「**唯一允许的逆向操作**」；TECH_SPEC §4 转移表里 `publications: "failed": {"queued"}` 注明「仅 reset 命令可走」。没有它，一条 failed publication 卡死后无法从 CLI 救回（只能手改 DB，违背契约）
- **怎么改**：
  1. `cmd_reset`：根据 `<id>` 前缀判定表（`t_`→topics / `c_`→contents / `p_`→publications），读当前记录，走 `db.transition(conn, <table>, id, <current_status>, <target_status>)`——**必须走状态机**，非法逆向由 `transition` 的转移表拦截抛 `IllegalTransition`（reset 只能走转移表允许的边，如 `failed→queued`；不允许任意乱跳）
  2. 若目标状态非法：捕获 `IllegalTransition`，打印清晰错误（「reset 不允许 X→Y，转移表未定义此边」）+ exit 1
  3. 记录一条 warning 级审计日志（`stage="reset" ref_id=<id>`，§8 要求带 stage+ref_id），便于事后追溯谁重置了什么
- **验收标准**：造一条 `failed` publication，`reset p_xxx queued` 后其 status=queued；`reset` 一个非法目标（如 `published→queued`）返回 exit 1 且不改库；新增 `tests/test_reset_cmd.py` 覆盖合法逆向 + 非法拒绝两条路径
- **红线**：**必须走 `db.transition`**——绝不裸 `UPDATE` 绕过转移表（否则 reset 变成任意改状态的后门，破坏状态机不变式）；不改转移表定义（TECH_SPEC §4 冻结）；reset 不触发任何发布/创作副作用，只改状态
- **参考**：TECH_SPEC §2、§4；HARD_PARTS §10 第 3 条（不 mock/绕过状态机）

  ✅ 完成于 2026-07-07，commit <本 commit sha>，备注：cmd_reset 按 id 前缀（t_/c_/p_）分发表，读当前 status，走 db.transition 状态机；捕获 IllegalTransition/StaleState 各自 exit 1 + 清晰错误；成功写 warning 级审计日志（stage=reset + ref_id=id）；非法前缀/不存在 id 也 exit 1。tests/test_reset_cmd.py 9 用例覆盖合法/非法/不存在/审计/三表分发/状态机红线 6 路径。

### S8-3 新增 `doctor` 体检命令（MEDIUM，上线前自检）
- [x] **目标**：一条命令告诉用户「现在缺什么才能真跑起来」，免得逐项踩坑
- **背景**：本番初始化涉及多个易漏项（config.yaml 存在？state.db 建了？secrets/ 目录？LLM key env？playwright chromium？）。给一个体检命令，输出清单式报告，是"能用起来"的关键脚手架
- **怎么改**：
  1. 新增 `pipeline/doctor.py::run_doctor(config_path) -> list[CheckResult]`（纯函数，每项返回 `(name, ok: bool, hint: str)`），检查项至少含：① `config.yaml` 是否存在且能 `load_config` 通过 ② `state.db` 是否存在（不存在提示先 `init-db`）③ `secrets/` 目录是否存在 ④ LLM key 环境变量是否设置（`ANTHROPIC_API_KEY` 或 `MINIMAX_API_KEY`，**只检查存在与否，绝不打印值**）⑤ `budget.monthly_usd` 是否 > 0 ⑥ `publish.enabled` 当前值（提示：true=会真发）
  2. `run.py` 注册 `doctor` 子命令（`sub.add_parser("doctor", ...)` + `cmd_doctor` 映射），逐行打印 `✅/❌ <name>：<hint>`，有任一 ❌ 则 exit 1（方便脚本/CI 判断）
  3. `doctor` **只读**，不创建任何文件（是"体检"不是"治疗"；创建交给用户或 S8-4 引导文档）
- **验收标准**：缺 config.yaml 时 `doctor` 报 ❌ 并提示 `cp config.example.yaml config.yaml`；齐全时全 ✅ exit 0；新增 `tests/test_doctor.py` 用 tmp_path 造齐全/缺项两种环境断言结果
- **红线**：**绝不打印密钥值**（§9 凭据安全，只报"已设置/未设置"）；doctor 不写任何文件、不改 config、不建库
- **参考**：TECH_SPEC §6；HARD_PARTS §9；CLAUDE.md 常用命令段

  ✅ 完成于 2026-07-07，commit 08e8af3，备注：`pipeline/doctor.py` 209 行（纯函数 run_doctor + 6 检查项 + CheckResult frozen dataclass）+ `pipeline/run.py` 接入 cmd_doctor（@_stage_lock + 解析 + COMMANDS 字典 +28 行）+ `tests/test_doctor.py` 26 测试。**设计决策**：① 检查项顺序固定 config→state.db→secrets→llm_key→budget→publish.enabled——按「启动流水线之前最常被绊倒的项」排序；② publish.enabled=true 不 fail 只 warn（"⚠️ 真发"），因它不是错误而是「知情确认」；③ config 加载失败时 budget/publish 检查返回 ❌ 提示「先修 config」而非各自独立报错；④ cmd_doctor 用 CWD 相对路径（secrets_dir="secrets"、args.config 透传），与其它 cmd 模式一致；⑤ doctor.run_doctor 不调 db.connect/init_db（仅 Path.exists + load_config），严格遵守「只读不创建任何文件」红线——tests/test_doctor.py::TestReadOnly 验证。全测 1035 绿（4 pre-existing 失败不变）。契约零变更：models.py / SQL schema / Adapter 签名 / argparse 现有签名 / webui 行为全部不动；run.py 880 行（S8-2 基线 852，+28 全在 S8-3 新增 cmd + parser + COMMANDS 字典项内）。

### S8-4 上线引导文档 + 真实 LLM 跑通一轮（MEDIUM，需用户参与 key）
- [x] **目标**：一份 `docs/GETTING_STARTED.md`，照着走能从零到「ingest→score→create→gate→review」真实跑通一轮，并记录成本 baseline
- **怎么改**：
  1. 写 `docs/GETTING_STARTED.md`：分步骤——① `cp config.example.yaml config.yaml` 并按注释改 ② `mkdir -p secrets` ③ 设置 LLM key 环境变量（说明 Anthropic 与 MiniMax 两种，指向 `pipeline/creators/llm.py::setup_provider_from_env` 的实际读取逻辑，**别写死具体 key**）④ `python -m pipeline.run init-db` ⑤ `python -m pipeline.run doctor`（S8-3）确认全绿 ⑥ 依次 `ingest→score→create→gate→review` ⑦ `python -m pipeline.run webui` 打开控制台
  2. **真实跑通一轮**（需用户提供 key，若无 key 则标 `⚠️ BLOCKED: 待用户提供 LLM key` 跳过后半）：跑一轮 ingest→gate，把「N 条 ingest / N 过门禁 / 本轮 LLM 成本 $X」记进 GETTING_STARTED.md 的"成本 baseline"节，作为 budget.monthly_usd 是否合理的现实依据
  3. 文档里显式标注「发布（M4/M5）默认关闭，需人工登录 + config `publish.enabled: true` 才真发」，引导用户不要误触高危路径
- **验收标准**：一个空白环境的人照 GETTING_STARTED.md 能跑到 webui 打开；成本 baseline 有真实数字（或明确标注 BLOCKED 待 key）
- **红线**：文档任务——**不改 pipeline/ 代码逻辑**；不把任何真实 key/cookie 写进文档或提交；真实跑通**不碰 publish**（只到 review 为止）
- **参考**：CLAUDE.md 会话重启指引；TECH_SPEC §2；HARD_PARTS §4（成本）

  ✅ 完成于 2026-07-07，commit <本 commit sha>，备注：`docs/GETTING_STARTED.md` 12 节：前置条件 → 克隆venv → 复制config → 建secrets → 设LLM key（MiniMax 优先 / Anthropic 备选）→ init-db → doctor → 跑流水线 5 步 → webui → 成本 baseline（BLOCKED 待 key，含 M2-2 真实冒烟参考 2026-07-05 $0.0267/day） → ⚠️ 发布警告（HARD_PARTS §1 三重锁 + §9 凭据）→ 下一步 → 故障排除表。未改任何 pipeline/ 代码（红线遵守）；真实跑通不碰 publish。

---

> ⚠️ **A8-1 ~ A8-4 已被 M10 取代（2026-07-09，用户决策）**：管理后台 CRUD（选题录入 / 内容编辑 / 手动排期 / 信息架构整合）并入 **M10 P2**（写操作阶段）。**不要再做 A8-1~A8-4**——保留仅作需求出处。

### A8-1 选题手动录入（MEDIUM，管理后台 CRUD 第一块）
- [ ] **目标**：在 Web 控制台手动新增一条选题（用户自己发现的热点），不必等 RSS 抓
- **背景**：现在 topics 只能由 `ingest` 从数据源抓入。用户要「增」的能力——手动录入一条 title/url，进入正常 score 流程
- **怎么改**：
  1. 后端 `app.py`：新增 `GET /topics/new`（返回一个表单片段/页：title 必填、url 选填、summary 选填、source 固定填 `manual`、pillar 选填下拉）与 `POST /topics`（接 Form 字段）
  2. 入库**走 db 层不裸 SQL**：复用 `pipeline/topics/dedup.py` 计算 `content_hash`（`normalize(title)+domain`，抄 `pipeline/ingest.py` 里 M1-2 的算法）→ 调 `db.try_insert_topic(conn, Topic(...))`（已存在，INSERT OR IGNORE 天然防重）。status 一律 `raw`（走正常 score/dedup，不许手动直接塞 selected 跳过评分）
  3. 重复（content_hash 已存在）→ 返回 `role=alert` 提示「该选题已存在」；成功 → 返回成功片段 + 链到 `/topics?status=raw`
  4. `templates/topic_new.html` + base.html 导航「选题池」旁加「+ 新增选题」入口
- **验收标准**：UI 提交一条 title → `/topics?status=raw` 出现该条，source=manual，status=raw；重复提交同 title 返回"已存在"不重复入库；新增测试覆盖入库成功 + 去重两条路径
- **红线**：**不裸写 SQL**（用 `db.try_insert_topic`）；**不加 schema 字段**；手动录入的 topic **必须走 raw→score 正常流程**，不许 UI 直接造 selected/consumed 跳过评分与门禁；`content_hash` 用现有 dedup 函数算，别自己重写哈希
- **参考**：TECH_SPEC §3（topics 表）、§7；HARD_PARTS §5（幂等/去重）；M1-2 ingest 入库逻辑

### A8-2 内容在线编辑（HIGH，用户「可编辑」核心诉求）
- [ ] **目标**：在 Web 控制台直接编辑生成的正文（canonical.md 及派生稿），存回文件——不用去命令行 vim 改
- **背景**：这是用户「可编辑」诉求的核心。LLM 产出的 canonical.md / 头条稿 / 小红书 caption / X thread 常需人工微调，现在只能开文件改。给 UI 一个编辑器
- **怎么改**：
  1. 后端 `app.py`：新增 `GET /contents/{id}/edit`——读 `content.canonical_path` 原文 + 探测派生文件（`output/<date>/<id>/toutiao.md`、`xiaohongshu/caption.md`、`x/thread.md`，存在才显示）填入各 `<textarea>`；`POST /contents/{id}/edit`——把提交文本**原子写回**对应文件（先写 `<file>.tmp` 再 `os.replace` 覆盖，HARD_PARTS §5 tmp→rename 模式），不存在的派生文件跳过不新建
  2. **只改文件、不改 DB 状态**——编辑不动 `contents` 表任何字段（正文在文件里，不在库里）。保存后返回提示「正文已更新；若该内容已过门禁，建议重新 `gate` 或人工复核后再发」
  3. **可编辑状态白名单**：只允许 `status in {draft, gated, approved, rejected_by_human}` 的内容编辑；`published`/`done` 的禁止编辑（已发出去改文件是误导）——非法状态返回 `role=alert`
  4. `templates/content_edit.html`；content_detail.html 加「✏️ 编辑」入口
- **验收标准**：编辑一条 gated content 的 canonical 正文并保存 → 文件内容变更、DB 状态不变；编辑 published 内容被拒；派生文件不存在时只显示 canonical 且保存不报错；新增测试覆盖「写回成功且原子」「published 拒绝」
- **红线**：**不改 contents 表 schema、不动 DB 状态**（编辑纯文件操作）；**写文件必须 tmp→rename 原子**（避免半截写坏正文）；不允许编辑图卡 PNG（二进制，本任务只做文本）；路径必须限定在 `output/` 下该 content 目录内（**防路径穿越**：校验解析后的绝对路径以该 content 目录为前缀，拒绝 `../` 逃逸）
- **参考**：TECH_SPEC §7；HARD_PARTS §5（原子写）、§9（不越权访问文件系统）；ARCHITECTURE §8（输出目录约定）

### A8-3 手动排期管理（MEDIUM，「可调度」诉求）
- [ ] **目标**：在 UI 手动为一条 approved 内容排一个「平台 + 时间」的发布计划，补足自动 schedule 之外的手动掌控
- **背景**：`schedule` 命令自动为 approved 内容按黄金时段排 publication。用户想手动掌控——「这条我要指定发某平台、某时间」。现有 UI 只能 reschedule/cancel 已存在的排期，不能新建
- **怎么改**：
  1. 后端 `app.py`：内容详情页对 `status=approved` 的内容，新增 `POST /contents/{id}/schedule`（Form: platform、account_id、scheduled_at ISO）→ 走 `db.insert_publication(conn, Publication(status='queued', ...))`（**走 db 层**，`UNIQUE(content_id, platform, account_id)` 天然防重复排期）
  2. platform 必须在 `config.platforms` 已配置的集合内（否则 `role=alert`）；scheduled_at 存 UTC ISO（HARD_PARTS §8，展示层才转本地）
  3. 与现有 `/calendar` 的 reschedule/cancel 打通：新建的 queued 记录立即出现在日历对应周
  4. `templates/content_detail.html` 加「📅 手动排期」表单（platform 下拉 + datetime-local 输入）
- **验收标准**：对一条 approved 内容手动排 X 平台某时间 → `/calendar` 该周出现一条 queued；重复排同 (content,platform,account) 被 UNIQUE 拒绝并提示；平台不在 config 内被拒；新增测试覆盖成功 + UNIQUE 冲突 + 非法平台
- **红线**：**不裸 SQL**（用 `db.insert_publication`）；**不绕过 `UNIQUE(content_id,platform,account_id)`**（防重复发布最后防线，HARD_PARTS §1）；排期**不等于发布**——本任务只造 queued 记录，真实发布仍由 publish 阶段 + 三重锁控制，UI 排期绝不触发真实 publish；不改 publications schema
- **参考**：TECH_SPEC §3、§7；HARD_PARTS §1、§8；M3-1 scheduler

### A8-4 后台信息架构整合（MEDIUM，让零散页面变成一个后台）
- [ ] **目标**：把 Dashboard/运行台/选题池/审核台/内容/发布日历/日志/设置 用统一导航串成一个像样的管理后台，每页有清晰的 CRUD 入口
- **错在哪**：`pipeline/webui/templates/base.html:20-26` 导航只有 5 个平铺链接（选题池/审核台/发布日历/设置 + 首页），U7-2 运行台、U7-5 日志、A8 的新增页面都没进导航；页面之间跳转靠猜。用户要「管理后台」的整体感，不是散落的页面
- **怎么改**：
  1. base.html 导航补全为完整后台菜单：`Dashboard(/) · 运行台(/runs) · 选题池(/topics) · 审核台(/review) · 发布日历(/calendar) · 日志(/logs) · 设置(/settings)`（运行台/日志分别依赖 U7-2/U7-5 已建；**若那两个任务未做，先占位链接并注释 TODO**，别报错）
  2. 用 pico.css 的既有类做成顶部导航条 + 高亮当前页（当前 path 匹配则加 `aria-current="page"`）；整体视觉统一（配合 U7-4 的 app.css 合并）
  3. 各列表页的行/计数尽量可点钻取（topics 计数→列表、review→详情、calendar→详情），把「看板」和「操作」缝合起来
  4. 首页 Dashboard（U7-1 已升级为驾驶舱）顶部放「今日待办」快捷入口指向各页
- **验收标准**：任意页面都能通过顶部导航到达其它所有页面；当前页高亮；点 dashboard 的「待审 N 篇」跳 `/review`；断言导航含全部 7 个入口（测试 GET `/` 检查导航链接）
- **红线**：**不引入 npm/前端构建链**（TECH_SPEC §7）；不改路由契约（只加导航链接，不改后端语义）；导航里**不放任何触发真实发布的直达按钮**（发布入口只在 U7-6 的受控流程里）
- **参考**：TECH_SPEC §7；U7-1/U7-2/U7-5（各页面来源）

> **M8 建议执行顺序**：S8-1 → S8-2 → S8-3 → S8-4（先能开机），再在 U7-1/U7-2 之后做 A8-1 → A8-2 → A8-3 → A8-4（管理后台 CRUD）。每任务独立 commit，`feat: S8-x/A8-x <描述>` 或 `fix: ...`。**A 组依赖 U7-2 运行台框架与 U7-1 驾驶舱导航，务必先完成那两个再动 A 组。**

---

## 后续 Backlog（不排期）

- **数字人口播 lane**（AIGCPanel 引擎，走 VideoEngine 接口）：好物分享/带货方向；前提=M5-3 评估通过 + 账号过带货门槛 + 平台虚拟人报备完成
- **OpenMontage 精品视频 lane**（远期观察，M0-0 决策降级：Pixelle-Video 已接管精品定位）：仅当 Pixelle-Video 质量不达预期时重评
- ~~公众号 Publisher~~ → 已拆入 M13-1（实现）/ M13-2（真实发布验证，高危人工）
- Postiz 部署接入 YouTube Shorts / TikTok
- 表现数据反哺选题权重（metrics → topics 评分 prompt 动态调整）
- 多账号矩阵（同平台第二账号 = 不同支柱人设）
- 英文内容线（Medium/dev.to）
- n8n 迁移（当 launchd 管理复杂度超阈值时）

---

## M9 — Provider 实装补完（M1-3 DECISION 落地）

### M9-1 Agnes-AI provider 接入（OpenAI 兼容网关）

- [x] **目标**：把 M1-3 留的 "DECISION NEEDED" 落地——接 Agnes-AI（OpenAI 兼容）作为生产 provider，替换默认 Mock
- **步骤**：
  1. 探查 API：营销站 `agnes-ai.com` Next.js 不暴露 API；真 API hub 在 `apihub.agnes-ai.com`（`api.agnes-ai.com` 是 404 误域）。`/v1/models` 列出 5 个模型；聊天走 `/v1/chat/completions`，鉴权 `Authorization: Bearer <key>`
  2. 写通用 `OpenAIProvider`（覆盖任何 OpenAI 兼容网关），加入 `PROVIDER_SPECS` 注册表的 `agnes` 条目（`env_var_prefix="AGNES"`，`default_base_url="https://apihub.agnes-ai.com/v1"`，`default_model="agnes-2.0-flash"`）
  3. `MODEL_PRICES` 加 `agnes-2.0-flash` 占位 0（价格待 agnes 官方公布）
  4. `build_provider` 增加 OpenAI 协议分支（`openai` + `agnes` 都走 `OpenAIProvider`）
  5. `setup_provider_from_env` 加 AGNES 优先级（AGNES > MiniMax > OPENAI > Mock）
  6. doctor 也认 `AGNES_API_KEY`（之前只认 MiniMax/Anthropic）
  7. secrets/agnes.env 落盘 key（chmod 600，gitignore 已盖）
  8. config.yaml llm.tiers 改 `agnes-2.0-flash`（cheap/creative/critical）
- **验收**：单元测试 + 真实链路 ingest→score→create→gate 全跑通
- **参考**：M1-3 DECISION NEEDED；TECH_SPEC §5.3；HARD_PARTS §4

  ✅ 完成于 2026-07-07，commit <本 commit>，备注：tests/test_openai_provider.py 新增 18 用例（构造 5 + from_env 6 + call 7）+ test_provider_specs.py 5 条更新（4→5 entries、agni spec/price/build）+ test_doctor.py 加 agnes_env_passes + test_minimax_provider 3 个 setup 测试加 delenv AGNES/OPENAI 兜底。**真实冒烟**：ingest 36 → score 14 processed / 5 selected（topic_dedup 29KB 超 agnes 上下文 404，**M1-7 设计的静默 fallback 兜住，正常继续**）→ create 4 ok / 1 fail (timeout) → gate 4 discarded（占位锚点严，全部 < 24 分）。**0.42 USD / 完整 round-trip**（4 篇 3541-4109 字 canonical 产出）。**契约零变更**：models.py / SQL schema / Adapter 签名 / TECH_SPEC §3/4/5 一律不动；只动 llm.py + config.yaml + 三个测试文件。**已知限制**：agni 上下文窗口较小（topic_dedup 一次性喂 36 条超限），M1-8 评估文档建议过按需 chunk；当下语义去重静默 fallback 兜底，单调降级不阻塞。

---

## M10 — Web 控制台 v3：「蚁小二」形态 SPA（2026-07-09 追加，用户驱动）

> **缘起**：用户判定现有 htmx webui「没法用」，参考成熟商业产品 **蚁小二**（yixiaoer.cn，自媒体多平台多账号分发管理工具）的产品形态，把控制台重做成左侧栏 App Shell 管理后台。**已拍板**：① 前端栈 = **Vue 3 + Vite + TypeScript + Ant Design Vue + Pinia + Vue Router + ECharts**（独立 SPA，后端加 `/api/v1/*` JSON API）；② 范围 = **真实能力优先**（借蚁小二 IA 搭外壳，只把后端真实支撑的页做实，云托管/团队/私信/RPA/采集源等无后端模块留「规划中」占位）；③ 首期 = **外壳 + 真实只读数据**。完整设计见 `/Users/lazy/.claude/plans/curried-giggling-hammock.md`。
>
> **取代关系**：M10 取代 U7-1~U7-6、A8-1~A8-4（前端诉求全部并入）。写操作/发布留 P2~P4。
>
> **执行者注意（弱模型必读）**：同 M7/M8 规则——严格照「怎么改」，**不许改契约**（models.py 字段、SQLite schema、`PublisherAdapter`/`SourceAdapter` 签名、状态机转移表、TECH_SPEC §3/§4/§5 一律不动）。**新增 db 只读 SELECT 查询是允许的**（增量，R7-3 有先例）。API 层**不裸写 SQL**，写操作走 `db.transition()`/既有编排。改动前 `git pull` 对齐；每任务**独立 commit** + `python -m pytest tests/ -q` 全绿才算完成；每任务只改其「声明改动文件」集（自治协议客观闸）。

### 🧭 M10 执行顺序（先做 P1，逐个独立交付）

```
P1（页面架构 + 真实只读）：M10-0 → M10-1 → M10-2 → M10-3 → M10-4 → M10-5 → M10-6 → M10-7 → M10-8 → M10-9
P2（交互与写操作 + 运行台）：M10-P2-*（见末尾大纲）
P3（按发布类型做深）：M10-P3-*
P4（UI 发布，最高危，最后）：M10-P4-*
```

---

### M10-0 契约修订 + 工程准备（纯文档/配置，先做）
- [x] **目标**：把「引入 SPA + npm 构建链」这项已获用户批准的契约变更落到文档，并准备好前端目录忽略规则，后续任务才不算「擅自改契约」
- **步骤**：
  1. 改 `docs/TECH_SPEC.md §7`：技术栈段落加「FastAPI 提供 `/api/v1/*` JSON API，背后 SPA（`frontend/` 源码，Vite 构建到 `frontend/dist`，由 FastAPI StaticFiles + 客户端路由 catch-all 托管），**引入 npm 构建链**」；路由契约段注明「JSON 契约（见 M10 各 router），旧 htmx 路由标注 legacy、SPA 达 parity 后移除」；**保留不变量原文**「UI 不得直接写 SQL / 状态机 + 发布三重锁对 UI 同样生效」，并补一句「发布需 dry-run 先行 + 显式确认，排除于通用运行台」
  2. 改 `.gitignore`：加 `frontend/node_modules/`（`frontend/dist/` 默认**提交**，便于 `webui` 直开免构建；若后续嫌体积大再改策略）
  3. `docs/GETTING_STARTED.md` 末尾加一节「前端构建」：`cd frontend && npm ci && npm run build`（此时 frontend 尚不存在，先写步骤占位，M10-9 落实）
- **验收**：TECH_SPEC §7 与现实一致（承认 npm 构建链）；`.gitignore` 含 frontend/node_modules；**不改任何 pipeline/ 代码**
- **声明改动文件**：`docs/TECH_SPEC.md`、`.gitignore`、`docs/GETTING_STARTED.md`
- **红线**：只动 §7，**不许碰 §3/§4/§5**

  ✅ 完成于 2026-07-09，commit 4a95a0d，备注：TECH_SPEC §7 改 53 行——技术栈分后端 FastAPI + 前端 SPA（Vue3/Vite/TS/AntD Vue/Pinia/Router/ECharts）+ npm 构建链声明；新增 17 行 JSON API 路由清单（dashboard/topics/sources/contents/review/publish/analytics/accounts/runs/settings）；旧 htmx 路由清单保留并标「deprecated，SPA parity 后移除」；不变量原文保留 + 补「publish 排除于通用运行台白名单」；错误格式分 JSON envelope vs htmx alert 两种。.gitignore 加 frontend/node_modules/（dist 默认提交注释说明）。GETTING_STARTED.md 新增 §13「前端构建」占位节（指向 M10-7 + M10-9）+ 目录索引同步。全测 1102 pass + 12 skip + 7 失败（全部 pre-existing——stash 验证过，与本任务无关）；git diff 仅命中声明文件集；§3/§4/§5 零改动（红线遵守）。

### M10-1 webui 接缝：抽 `deps.py` + `app.py` 瘦身（行为不变）
- [x] **目标**：把 `pipeline/webui/app.py` 里模块级 `_DB_PATH`/`_CONFIG_PATH`/`load_config`/`_conn()`/`_db()` 抽到新 `pipeline/webui/deps.py`，为 API router 与 SPA 托管铺接缝；**现有 htmx 路由与测试行为零变化**
- **错在哪/为何**：现有 `app.py`（393 行）把 DB 路径常量、config 加载、DB 连接上下文都放在自己模块里，测试靠 `monkeypatch.setattr(app, "_DB_PATH", …)`。API router 若也 `from app import` 会循环依赖；抽到 `deps.py` 后 router 与 app 都从 deps 导入，override 种子仍生效
- **步骤**：
  1. 新建 `pipeline/webui/deps.py`：搬 `_DB_PATH`/`_CONFIG_PATH`、`get_conn()`（= `db.connect(_DB_PATH)`）、`_db()` contextmanager、`get_config()`（= `load_config(_CONFIG_PATH)`，异常返回 None + err）。**保持同名**便于 monkeypatch
  2. `app.py` 改为 `from . import deps` 并引用 `deps._DB_PATH` 等；删掉自己那份定义；旧 htmx 路由逻辑不动
  3. 现有 `tests/test_webui*.py` 里 `monkeypatch.setattr(app_mod, "_DB_PATH", …)` 若因搬家失效 → 改指向 `deps`（**最小改动，不改断言**）
- **验收**：`python -m pytest tests/test_webui*.py -q` 全绿；`python -m pipeline.run webui` 仍能起、旧页面仍在
- **声明改动文件**：`pipeline/webui/app.py`、`pipeline/webui/deps.py`(新)、`tests/test_webui*.py`（仅 monkeypatch target 调整）
- **红线**：不改任何路由的**行为/返回**；纯搬家

  ✅ 完成于 2026-07-09，commit <本 commit sha>，备注：`pipeline/webui/deps.py` 76 行——模块级 `_DB_PATH`/`_CONFIG_PATH` 常量 + `load_config` re-export（让 monkeypatch 仍生效）+ `get_conn()`/`_db()` contextmanager + `get_config() -> (cfg, err)`。app.py 改 5 行删 14 行：create_app 内 `db.connect(deps._DB_PATH)`、所有路由 `with deps._db()`、settings 路由 try/except 块简化为 `cfg, err = deps.get_config()`、main() 走 `deps.load_config(deps._CONFIG_PATH)`。5 个 webui 测试 monkeypatch target 由 `app_mod` 改 `deps`（最小改动，未碰断言；处理过一次转义字符 sed 副作用已修）。webui 测试 53 全绿 + 全测 1039 pass + 12 skip + 7 失败 pre-existing（stash 验证过）。

### M10-2 只读查询层：`db.py` 列表/关联查询 + `db_reads.py` metrics/llm 查询
- [x] **目标**：补齐 UI 需要但现在缺失的**只读** SELECT 查询（全部增量，零 schema 变更）
- **步骤**：
  1. `pipeline/db.py`（复用私有 `_row_to_*`，紧挨现有 `get_*_by_status`）新增：
     - `list_topics(conn, *, status=None, pillar=None, source=None, limit=50, offset=0) -> list[Topic]` + `count_topics(conn, *, status=None, pillar=None, source=None) -> int`
     - `list_contents(conn, *, status=None, pillar=None, limit=50, offset=0) -> list[Content]` + `count_contents(...)`
     - `list_publications(conn, *, status=None, platform=None, limit=50, offset=0) -> list[Publication]`
     - `get_publications_by_content(conn, content_id) -> list[Publication]`（ORDER BY scheduled_at）
     - `recent_activity(conn, *, limit=20) -> list[dict]`（topics/contents/publications 的 (id,kind,status,updated_at) UNION，ORDER BY updated_at DESC）
  2. 新建 `pipeline/db_reads.py`（metrics/llm 无现成 mapper，独立成文件避免 db.py 继续膨胀）：
     - `row_to_metric(row) -> Metric`
     - `get_latest_metric(conn, publication_id) -> Metric | None`、`get_metrics_series(conn, publication_id) -> list[Metric]`
     - `llm_cost_by_stage(conn, *, since_iso=None, until_iso=None) -> list[dict]`（GROUP BY stage：calls/cost/in/out tokens）
     - `llm_cost_by_day(conn, *, days=30, now=None) -> list[dict]`
     - `platform_metric_totals(conn) -> list[dict]`（publications LEFT JOIN metrics，仅 published，GROUP BY platform）
- **验收**：`tests/test_db_reads.py` + `tests/test_db.py` 增量覆盖每个新函数（造数据→断言返回）；全 SELECT，**无 UPDATE/schema 变更**
- **声明改动文件**：`pipeline/db.py`、`pipeline/db_reads.py`(新)、`tests/test_db.py`、`tests/test_db_reads.py`(新)
- **红线**：只读；不改 schema、不改现有查询签名

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：db.py 增 7 函数（list_topics/contents/publications × 3 + count_topics/contents × 2 + get_publications_by_content + recent_activity）+ `_build_filter_where` 私有 helper（白名单 frozenset 防注入；None 透传）。db_reads.py 新建 6 函数（row_to_metric / get_latest_metric / get_metrics_series / llm_cost_by_stage / llm_cost_by_day / platform_metric_totals）。test_db.py 增 19 用例 + test_db_reads.py 新建 15 用例。全测 1073 pass + 12 skip + 7 pre-existing。**bug 修**：llm_cost_by_day 初版 since_iso 没减 days 天，第一轮 verify 抓出已修。

### M10-3 序列化层：`serialize.py`（dataclass→dict + 内容目录枚举）
- [x] **目标**：统一把 frozen dataclass 序列化成 API JSON，并提供内容输出目录的文件/图片只读枚举
- **步骤**：
  1. 新建 `pipeline/webui/serialize.py`：`topic_dict/content_dict/pub_dict/metric_dict`（字段 1:1；`formats`/`inline_images` 转 list，`gate_scores` 转 obj|null，`metric_dict` 默认丢 `raw`）
  2. `list_content_files(content) -> list[dict]`：读 `output/<date>/<id>/` 枚举 `toutiao.md`/`xiaohongshu/*`/`x/thread.md` 等（**只读文件系统**，目录不存在返回 `[]`）
  3. `content_image_urls(content) -> dict`：由 `cover_path`/`inline_images` 生成 `/output/...` URL
  4. （**为 P2 预留、本期先写但不接线**）`write_canonical_jailed(content, markdown) -> int`：路径越狱防护（解析绝对路径必须以该 content 输出目录为前缀）+ tmp→rename 原子写。**M10 P1 不暴露此写接口**
- **验收**：`tests/webui/test_serialize.py` 覆盖各 `*_dict` 字段完整性 + 文件枚举（有/无目录）+ 路径越狱被拒（`../` 逃逸 raise）
- **声明改动文件**：`pipeline/webui/serialize.py`(新)、`tests/webui/test_serialize.py`(新)
- **红线**：P1 只做只读序列化 + 枚举；写接口写好但不接路由

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：serialize.py 240 行——topic_dict/content_dict/pub_dict（tuple→list + gate_scores dict|None 透传）+ metric_dict(include_raw) 默认丢 raw + list_content_files 枚举 15 已知派生路径（目录不存在全 exists=False）+ content_image_urls cover/inline 转 /output/<path> URL + write_canonical_jailed tmp→rename + _safe_resolve 越狱防护两层（NUL+normpath .. 解析）。tests/webui/test_serialize.py 新建 28 测试覆盖各函数 + 越狱边界。全测 1101 pass + 12 skip + 7 pre-existing。**bug 修**：_safe_resolve 初版 Path.absolute() 不解析 ..——测试 `tmp_path/../evil` 逃过前缀检查；改用 os.path.normpath 归一化后再 .relative_to。

### M10-4 只读 API（一）：dashboard / topics / sources / contents / review
- [x] **目标**：`/api/v1` 前五个域的**只读** router 落地
- **步骤**：新建 `pipeline/webui/api/__init__.py`（`api_router = APIRouter(prefix="/api/v1")` 并 include 各子 router）+ `dashboard.py`/`topics.py`/`contents.py`/`review.py`：
  - `GET /dashboard`：`count_by_status`×3 + `sum_llm_cost_this_month` + `cfg.budget` + `collect_weekly_report().gate_histogram` + 待办(由 `get_*_by_status` 派生) + `recent_activity`
  - `GET /topics?status=&pillar=&source=&limit=&offset=`（`get_topics_by_status` 或 `list_topics`）；`GET /sources`（`load_config().sources`）
  - `GET /contents?status=`（`list_contents`）；`GET /contents/{id}`（详情：`md_to_html(canonical)` + `list_content_files` + `content_image_urls` + `get_publications_by_content` + 派生时间线）
  - `GET /review`（`get_contents_by_status('gated')` + 门禁分 + 缩略）
  - 错误统一 `{error:{code,message}}` + HTTP 码
- **验收**：`tests/webui/test_api_{dashboard,topics,contents,review}.py` 用 `monkeypatch deps._DB_PATH` + `TestClient(create_app())` 造数据断言 JSON 形状 + 状态码
- **声明改动文件**：`pipeline/webui/api/*`（上述文件，新）、对应 `tests/webui/test_api_*.py`(新)、`app.py`（`include_router(api_router)` 一行）
- **红线**：全部 GET 只读；不写库

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：5 子 router + 1 组合 router，11 个 GET 端点。dashboard 聚合 6 字段（counts/todos/budget/activity/histogram/correlation，复用 weekly_report.collect_weekly_report）；topics/contents 走 db.list_* 过滤分页 + get_by_id 404 envelope；sources 走 cfg.sources；review 仅 gated 状态。app.py 加 `include_router(api_router)` 一行。tests/webui/test_api_m10_4.py 13 用例覆盖路由 + 过滤 + 分页 + 404 + tuple→list + canonical HTML 渲染。全测 1114 pass + 12 skip + 7 pre-existing。

### M10-5 只读 API（二）：publish / analytics / accounts / runs / settings
- [x] **目标**：`/api/v1` 其余域的只读 router
- **步骤**：`publish.py`/`analytics.py`/`accounts.py`/`runs.py`/`settings.py`：
  - `GET /publish/calendar?week=`（复用 `bucket_week`）；`GET /publish/records?status=`（`list_publications` + 可带 latest metric）
  - `GET /analytics/weekly`（序列化 `collect_weekly_report()`）；`/analytics/cost?group=stage|day`（`llm_cost_by_*`）；`/analytics/publications/{id}/metrics`（`get_latest_metric`+`get_metrics_series`）；`/analytics/platforms`（`platform_metric_totals` 或周报 top_by_platform）
  - `GET /accounts`（`collect_cookie_health(load_config())`）；`GET /accounts/login-guidance`（静态每平台指引）
  - `GET /runs` / `GET /runs/{id}`（**首期只读内存运行历史**；`runner_bridge` 触发留 P2；`publish` 断言不在白名单）
  - `GET /settings`（`sanitize_config(cfg.model_dump())` + `run_doctor`，脱敏无明文密钥）
- **验收**：各 `tests/webui/test_api_*.py` 断言 JSON + 脱敏（settings 无明文 key）+ `publish not in STAGE_WHITELIST`
- **声明改动文件**：`pipeline/webui/api/{publish,analytics,accounts,runs,settings}.py`(新)、`pipeline/webui/runner_bridge.py`(新，仅 registry + 白名单常量，_execute 可先写不接线)、对应 tests(新)
- **红线**：只读；`publish` 绝不进 `STAGE_WHITELIST`

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：5 router 落地（publish/analytics/accounts/runs/settings）；api_router 加 5 子 router include。STAGE_WHITELIST = {ingest/score/create/gate/derivative/review/schedule/collect/generate-images}——publish 排除。tests 19 用例覆盖 4 路由 + 5 runs 路径（含 publish 400 + 白名单 stage 501 + get 404）+ 白名单锁死断言。**2 bug 修**：① publish/calendar 误用 bucket.days.date（实际是 date 列表）→ 改 bucket.by_day；② settings.run_doctor 返回 CheckResult dataclass（非 tuple）→ 改 r.name/r.ok/r.hint。全测 1133 pass + 12 skip + 7 pre-existing。

### M10-6 SPA 托管接线：`app.py` mount + catch-all
- [x] **目标**：FastAPI 能托管 Vite 构建产物 `frontend/dist`，客户端路由可用，且不遮蔽 API/output/static
- **步骤**：`app.py` 内（顺序敏感）：① 保留 `/output`、`/static` 挂载；② `include_router(api_router)`；③ `app.mount("/assets", StaticFiles(directory="frontend/dist/assets"))`（目录不存在则跳过不崩）；④ 旧 htmx 路由迁到 `legacy_htmx.py` router 注册在 catch-all 之前（或加 `/legacy` 前缀）；⑤ **最后**注册 `GET /{full_path:path}` 返回 `frontend/dist/index.html`，对 `/api`/`/output`/`/static`/`/assets` 前缀放行 404；`dist` 不存在时返回「请先 `npm run build`」提示页（200，不崩）
- **验收**：`tests/webui/test_spa_serving.py`：dist 缺失时 catch-all 返回提示页不 500；`/api/v1/dashboard` 不被 catch-all 吞；造一个假 `frontend/dist/index.html` 后 `GET /topics`(前端路由) 返回该 index
- **声明改动文件**：`pipeline/webui/app.py`、`pipeline/webui/legacy_htmx.py`(新，搬旧路由)、`tests/webui/test_spa_serving.py`(新)
- **红线**：catch-all 必须放行 API/静态前缀；`create_app()` 仍是唯一工厂

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：app.py 顺序挂载 /output → /static → /assets（缺则跳过） → 旧 htmx 路由 → catch-all GET /{full_path:path}（dist 缺时返回构建提示页 200 不 500；明确 404 拒绝 api/output/static/assets 前缀）。tests 6 用例覆盖 catch-all 5 路径 + 旧 htmx 仍工作。全测 1139 pass + 12 skip + 7 pre-existing。

### M10-7 前端脚手架 + App Shell + 全路由
- [x] **目标**：`frontend/` 建起 Vue3+Vite+TS+AntD Vue+Pinia+Router+ECharts，蚁小二式左侧栏外壳 + 全部页面路由 + 规划中占位可导航
- **步骤**：
  1. `npm create vite@latest frontend -- --template vue-ts`；装 `ant-design-vue`、`pinia`、`vue-router`、`echarts`、`axios`
  2. `src/api/client.ts`（axios，baseURL `/api/v1`，统一错误解包）；`vite.config.ts` dev proxy `/api`→`127.0.0.1:8787`，`build.outDir` 指向 `dist`
  3. `layouts/AppShell.vue`：左侧 `a-menu` 分组（概览/内容生产/分发/数据/运营/规划中）+ 顶栏（标题 + 环境标 + 预算胶囊占位）+ `<router-view>`；当前路由高亮
  4. `router/index.ts`：仪表盘 `/`、`/topics`、`/contents`、`/contents/:id`、`/review`、`/publish/calendar`、`/publish/records`、`/analytics`、`/accounts`、`/runs`、`/settings`、`/roadmap/*`
  5. `components/EmptyStub.vue`（规划中占位空态）；各 view 先空壳可导航
- **验收**：`cd frontend && npm run build` 成功产出 `dist`；`npm run dev` 起本地、左侧栏导航到每个页面不报错（此步数据可空）
- **声明改动文件**：`frontend/**`（新）
- **红线**：不引第二套 UI 库；不改后端

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：npm create vite@latest --template vue-ts；ant-design-vue + pinia + vue-router + echarts + axios。AppShell.vue 蚁小二式左侧栏分 5 组（概览/内容生产/分发/数据/运营）。router 12 路由（11 真实 + /roadmap/:feature）。9 Pinia store 仅 load() 占位——M10-8 填字段。12 view 占位。vite build 成功（dist/index.html 0.45KB + index.js 1.49MB gzipped 460KB；AntD Vue 大块，后续可 code-split）。test_spa_serving.py 修订：dist 默认提交，3 测试改 _dist_dir() 探测 + pytest.skip 兜底。

### M10-8 前端只读视图对接真实数据
- [x] **目标**：11 个真实页面用 `/api/v1` 渲染真实数据 + loading/empty/error 三态；规划中模块 `EmptyStub`
- **步骤**：按页对接——仪表盘(计数/成本预算/待办/近期活动/门禁直方图 ECharts)、选题池(表格+状态/支柱/源筛选)、内容库(列表+详情：canonical HTML/派生文件/图片/发布时间线)、审核台(gated 卡片+门禁分+图卡缩略)、发布日历(周视图)、发布记录(表格+metrics)、数据看板(周报+平台对比+成本趋势 ECharts)、账号管理(cookie 健康表+登录引导)、运行台(运行历史，触发按钮禁用标 P2)、设置(脱敏 config + doctor)；**写操作按钮一律禁用并标 "P2"**
  - Pinia store 每域一个，仅只读 fetch；组件 StatusTag/CostBudgetCard/TodoCard 复用
- **验收**：起后端(有种子数据) + 前端，每页渲染真实数据；空库显示 empty 态不报错；断网/500 显示 error 态
- **声明改动文件**：`frontend/**`
- **红线**：只读；写按钮禁用占位，不接任何写端点

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：11 view 全部接 /api/v1 真实数据。stores/index.ts 类型化（移除 any，加 9 interface：TopicItem/ContentItem/ContentDetail/PublicationItem/CalendarData/AccountHealthItem/LoginGuidance/DoctorItem）。AppShell 5 分组导航 + 当前路由高亮。loading/empty/error 三态（a-spin/a-empty/a-alert）。写按钮 disabled 标 P2（promote/reject/approve/新增选题）。dist 1.49MB gzipped 461KB（AntD 大块，后续可 code-split）。全测 1139 pass + 12 skip + 7 pre-existing。

### M10-9 构建脚本 + 文档 + 端到端验证（P1 收尾）
- [x] **目标**：一键构建 + 文档到位 + 全链路验证 P1 达标
- **步骤**：`scripts/build_frontend.sh`（`cd frontend && npm ci && npm run build`）；`docs/GETTING_STARTED.md` 落实前端构建节 + 「webui 现为 SPA」说明；按计划「验证」小节跑通
- **验收**（P1 里程碑）：
  1. `bash scripts/build_frontend.sh` 产出 `frontend/dist`
  2. `python -m pipeline.run webui` → 浏览器左侧栏导航可达全部页面、当前页高亮
  3. 造种子数据后仪表盘/选题/内容/审核/日历/数据/账号/设置渲染真实数据，规划中模块显示占位
  4. `python -m pytest tests/ -q` 全绿（不回归）；`curl 127.0.0.1:8787/api/v1/dashboard` 返回 JSON；`curl .../api/v1/settings` 脱敏无明文密钥
  5. `grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空；契约文件（models/schema/adapter 签名）零 diff
- **声明改动文件**：`scripts/build_frontend.sh`(新)、`docs/GETTING_STARTED.md`

  ✅ 完成于 2026-07-10，commit <本 commit sha>，备注：scripts/build_frontend.sh 一键命令（npm ci + npm run build）。GETTING_STARTED.md §13 占位升级：dist 默认提交 / 改前端才 build / 开发模式 vite dev + 后端并行 / 显式 P1 只读。TS 严格模式 build 修复 3 vue-tsc 错误（Dashboard :format p 显式 number / loadRecords params 类型扩 boolean / Topics 解构多取 record → 删 / AppShell isActive 死代码）。**端到端 5/5 全过**：① build_frontend.sh 成功 ② pytest 1139 pass ③ /api/v1/dashboard 200 + 7 keys ④ /api/v1/settings 脱敏无明文 password/token/api_key/secret ⑤ import anthropic 护栏仅 llm.py 命中。**P1 里程碑达标**：左侧栏 SPA + 11 真实只读页面 + JSON 契约全打通 + 旧 htmx 兼容 + 状态机/三重锁护栏保持 + 模型零 diff + 写操作 P2 禁用。

  ⚠️ **跑偏修正 #1（2026-07-10 补）**：`npm create vite@latest` 脚手架自带的 `frontend/.gitignore` 里有一行 `dist`，优先级高于根 `.gitignore` 的「dist 默认提交」决策，导致 M10-7~M10-9 全程 `frontend/dist` 实际从未进 git（`git ls-files frontend/dist` 一直是 0）；`test_spa_serving.py` 探测不到 dist 会 `pytest.skip`，所以测试全程没报错，问题被掩盖。**影响**：全新 clone 后不 `npm run build` 直接跑 `webui` 只会看到构建提示页，不是承诺的「开箱即用」。**修复**：删掉 `frontend/.gitignore` 里的 `dist` 行（保留 `dist-ssr`），重新 build 后 `git add frontend/dist`，`test_spa_serving.py` 6 个测试从 skip 变为真正执行且全过。

  ⚠️ **跑偏修正 #2（2026-07-10 补，commit 372c070）**：`pipeline/webui/app.py` 在 SPA catch-all 之前注册了 `/`、`/topics`、`/review`、`/calendar`、`/contents/{id}`、`/settings` 等 GET 模板渲染路由（`r7_2`/`m4_4` 时期使用 Jinja2 + Pico CSS），由于 FastAPI 路由按声明顺序匹配——直接访问或刷新这些 URL 永远命中旧 htmx 模板，**根本到不了 Vue Router**。M10-7~M10-9 的「11 页面 SPA parity」其实只在首次进入 `/` 由 Vue Router 内部跳转时生效，浏览器刷新或外部书签进 `/topics` 之类就直接降级。`test_spa_serving.py` 当时没有跨路由断言（只测 dist 资源存在 + `assets/` 静态挂载），所以 CI 没暴露。**影响**：用户浏览器刷新任何子页就被打回 Pico 风格；外部书签跳不到 SPA；Vue Router `history` 模式依赖的「同一 HTML 服务所有路由」契约被打破。**修复**：删 `pipeline/webui/templates/` 7 个 Jinja2 模板 + `pipeline/webui/static/pico.min.css`；`app.py` 重写，去掉 `Jinja2Templates`/`TemplateResponse`/md-to-html/sanitize_config/cookie_health_views 路径，GET 统一由 `/api/v1/*`（JSON）+ SPA catch-all（HTML）接管；写操作 POST（`promote`/`reject`/`approve`/`reschedule`/`cancel`/`retry`）契约保留，curl/脚本可触发；`/api/status` 旧 JSON 兼容保留。测试同步重写：`test_webui.py` → `TestSpaCatchAll` 参数化 9 路径（`/`、`/topics`、`/topics?status=scored`、`/review`、`/calendar`、`/contents/c_detail01`、`/settings`、`/publish/calendar`、`/runs`）断言 `<!doctype html>` 出现 + 无 `pico` 字样，写操作类断言照旧；`test_webui_r7_1.py` 删 `TestDashboardTimezone`（datetime.utcnow 在 SPA 渲染下无意义）；`test_webui_r7_2.py` 删 `TestStaticDirAlwaysMounted` + `test_dashboard_still_renders`→`test_root_serves_spa_index`；`test_webui_m4_4.py` 加 `TestSpaCoversLegacyPages` + 补 `/api/v1/publish/calendar` & `/api/v1/settings` JSON 接管断言。**校验**：`pytest tests/test_webui*.py` **42/42 pass**；`grep "import anthropic" pipeline/ | grep -v llm.py` 为空（成本护栏）；`git diff models.py / TECH_SPEC.md / SQL schema` 全空（红线遵守）；`git diff` 仅命中声明文件集；Playwright 跑 11 路由全部 `sidebar=1 header=1`（蚁小二式 AntD Vue 菜单 + 顶部栏就位），零 `pageerror` 零 `console.error`，仪表盘加载真实 KPI（本月 LLM $0.4175/$80 / 待审 0 / 待发布 0 / 失败 0 + 20 条近期活动）、选题池 20+ OSS/HN 真实数据、审核台「0 篇待审」占位正常。截图存档 `/tmp/mf_ui2_*.png`。

---

### M10-10 P2 阶段 C：5 个旧 htmx POST → /api/v1/ JSON envelope（写端点迁移第三步）

- [x] **目标**：把 5 个旧 htmx POST 写端点（promote/reject/approve/reschedule/cancel/retry）迁移到 `/api/v1/` JSON envelope，前端 SPA 解 disabled。完成本任务后所有写端点都可在 SPA 上触发。
- **步骤**：
  1. 新建 `pipeline/webui/write_action_bridge.py`：6 个 bridge 函数 + 9 个 Error 类（TopicNotFoundError / TopicWrongStatusError / ContentNotFoundError / ContentWrongStatusError / ContentStatusChangedError / PublicationNotFoundError / PublicationWrongStatusError / PublicationStatusChangedError / InvalidDecisionError / InvalidTimeError）。薄封装调 db.transition / db.set_gate_verdict / db.reschedule_publication，**不重写业务逻辑**
  2. `pipeline/webui/api/topics.py`：POST /api/v1/topics/{id}/promote + reject
  3. `pipeline/webui/api/review.py`：POST /api/v1/review/{content_id}（approve/reject + reason）
  4. `pipeline/webui/api/publish.py`：POST /api/v1/publications/{id}/reschedule + cancel + retry
  5. `tests/webui/test_api_write_endpoints.py` 新建：23 测试覆盖 6 端点 × {成功/状态错/不存在/envelope 形状}
  6. 前端 `client.ts` 加 `apiPost<T>` 泛型 helper；`stores/index.ts` 新增 useTopicActionStore / useReviewActionStore / usePubActionStore 三个轻量 store
  7. 前端 3 个 view 解 disabled：Review.vue（approve/reject + reason 输入）、Topics.vue（promote/reject）、PublishCalendar.vue（reschedule modal + cancel + retry）
  8. `npm run build` + `git add frontend/dist/` 重建
- **验收**：`pytest tests/` 全绿（1254 pass + 12 skip + 7 pre-existing failures stash verified）；`grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空；`git diff models.py / db.py SQL schema / TECH_SPEC §3-5` 全空；旧 htmx POST 测试 `tests/test_webui.py` 20/20 pass
- **声明改动文件**：`pipeline/webui/api/{topics,review,publish}.py`、`pipeline/webui/write_action_bridge.py`(新)、`tests/webui/test_api_write_endpoints.py`(新)、`frontend/src/api/client.ts`、`frontend/src/stores/index.ts`、`frontend/src/views/{Review,Topics,PublishCalendar}.vue`、`frontend/dist/**`
- **红线**：不重写 db.transition；不发明新状态；不动 models.py / db.py / SQL schema / Adapter 签名；不引入 anthropic import；旧 htmx POST 路由保留（curl 兼容）

  ✅ 完成于 2026-07-10，commit 3ce25a8，备注：6 个 JSON 写端点上线。bridge 错误分类三层（NotFound 404 / WrongStatus 400 / StatusChanged 409 / Invalid 400）+ API 层映射到具体 envelope。`TopicWrongStatusError` (promote/reject 非 scored) → 400 wrong_status；`PublicationWrongStatusError` (reschedule/cancel/retry 非 queued/failed) → 409 not_queued/status_changed（与 topics 故意区分：UI 列表现 409 让前端可分流「非法操作」vs「状态已变」）；review approve 乐观锁失败 → 409 status_changed；reject 走「先 set_gate_verdict + expect_status=gated 再 transition」两步（与旧 htmx 路由同构）。前端 store 三个（topic-action/review-action/pub-action）+ 3 个 view 解 disabled + Topics 行级 disabled 由 status==scored 控制 + reschedule 弹 a-modal 改时间。dist 1.49MB gzipped 461KB 重建并 git add（防 M10-9 跑偏 #1 复发）。**契约零变更**：models.py / db.py / SQL schema / Adapter 签名 / TECH_SPEC §3-5 / 旧 htmx POST 路由 全保留；7 pre-existing failures 与本任务无关（stash 验证）。

---

### M10-11 P2 阶段 D：手动排期端点 + ContentDetail 排期表单

- [x] **目标**：在内容详情页对 approved (或 gated) 内容能选平台 + 账号 + 时间 → 立即造一条 queued publication。M10 P2「图文全流程」第四步（前三步：阶段 A 创建页、阶段 B 衍生+出图、阶段 C 写端点迁移已完成）。
- **步骤**：
  1. 新建 `pipeline/webui/schedule_bridge.py`：`schedule_for_content(conn, content_id, platform, account_id, scheduled_at, *, cfg_obj=None, now=None)` 纯函数 + 6 个 Error class（ContentNotFoundError / ContentWrongStatusError / PlatformNotConfiguredError / AccountNotFoundError / InvalidScheduledAtError / DuplicateScheduleError）。**薄封装不重写 db.insert_publication**；UNIQUE 冲突捕获 sqlite3.IntegrityError → DuplicateScheduleError；now 注入便于测试；cfg_obj=None 时跳过 platform/account 校验
  2. `pipeline/webui/api/contents.py` 加 `POST /contents/{content_id}/schedule`（201 + pub_dict）；catch 6 个 Error → 404 / 400×4 / 409 envelope（content_not_found / wrong_status / platform_not_configured / account_not_found / invalid_scheduled_at / duplicate_schedule）
  3. `tests/webui/test_api_schedule.py` 新建：18 测试覆盖 9 类（成功 × 2 [approved + gated] / content_not_found / 5 个禁止 status / platform_not_configured / account_not_found / 3 种 invalid_scheduled_at / 2 种 duplicate（含 UNIQUE 失败不增 DB）/ bridge 纯函数 × 2 / DB 幂等）+ envelope 形状 + 端到端 DB 真落库验证（`db.list_publications` 直查）
  4. 前端 `stores/index.ts` 加 `useScheduleStore`（running / lastResult / lastError / run(contentId, payload) / reset，参照 PubAction store 模式）
  5. 前端 `ContentDetail.vue` 在 approved/gated 内容显示「📅 手动排期」卡片：平台下拉 + 账号下拉（基于 `useAccountsStore.items` 真 cfg 账号列表，不硬编码平台名）+ datetime-local + ▶ 加入排期；成功刷新 publications 列表；错误 a-alert
  6. `npm run build` + `git add frontend/dist/` 重建
- **验收**：`pytest tests/webui/test_api_schedule.py -q` 18 全绿；`pytest tests/ -q` 1272 pass + 12 skip + 7 pre-existing（与本任务无关）；`grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空；`git diff models.py / db.py SQL schema / serialize.py / TECH_SPEC §3-5` 全空；前端 `npm run build` 绿，dist 已 rebuild + git add
- **声明改动文件**：`pipeline/webui/schedule_bridge.py`(新)、`pipeline/webui/api/contents.py`、`tests/webui/test_api_schedule.py`(新)、`frontend/src/stores/index.ts`、`frontend/src/views/ContentDetail.vue`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不改 models.py / db.py SQL schema / serialize.py / Publication dataclass；不动 `pipeline/scheduler.py`（CLI 自动排期专用）；不引入 anthropic import；状态白名单仅 {approved, gated}，其余 status 一律 400；router 不兜底 `try/except Exception` → 500（每个 Error 映射具体 HTTP 码）

  ✅ 完成于 2026-07-10，commit fa9c713，备注：`schedule_bridge.py` (~225 行) + `POST /contents/{id}/schedule` 端点（201 + pub_dict）。6 Error class → API 层映射：404 / 400×4 / 409。`tests/webui/test_api_schedule.py` 18 用例：成功（approved + gated）/ content_not_found / 5 禁止 status / platform_not_configured / account_not_found / 3 invalid_scheduled_at / 2 duplicate / bridge 纯函数 / DB 幂等。前端 `useScheduleStore` + ContentDetail 加 📅 手动排期 card（平台/账号下拉基于 `useAccountsStore.items` 真 cfg；datetime-local；成功刷新 publications 列表）。dist rebuild 1494KB gzipped 461KB git add。**契约零变更**：models.py / db.py / serialize.py / TECH_SPEC §3-5 / pipeline/scheduler.py 全部不动；7 pre-existing failures 与本任务无关（stash 验证）。

---

### M10-12 P2 阶段 E：UI dry-run 预演端点（绝对不真发）

- [x] **目标**：在发布日历/记录页加「🔍 预演」按钮，对一条 queued publication 调 `safe_publish(dry_run=True)` 走完整三道锁 + 真实 `PublisherAdapter.validate()`，展示「将发什么」并 100% 不触发任何真发副作用。M10 P2「图文全流程」第五步（阶段 A 创建、阶段 B 衍生+出图、阶段 C 写端点迁移、阶段 D 手动排期已完成）。
- **步骤**：
  1. 新建 `pipeline/webui/preview_bridge.py`：纯函数 `_build_preview_bundle(conn, pub)` 按 platform 解析产物（X → `x/thread.md` + 内联媒体 / 小红书 → `xiaohongshu/caption.md` + `tags.txt` + `cover.png` + `card-*.png` / 头条 → `toutiao.md` / 抖音 → `*.mp4`）+ 5 个 Error class（PublicationNotFoundError / PublicationWrongStatusError / ConfigLoadError / PlatformNotConfiguredError / AccountNotFoundError / AdapterInitError / ContentNotFoundError）；`_run_preview(conn, pub_id, run_id, now)` 流程：查 pub → cfg + account → 真实 `get_adapter()` → `adapter.validate(bundle)` → 用防真发包装器 `_NoPublishPreviewAdapter` 调 `safe_publish(dry_run=True)`，**safe_publish 在内存 DB 副本上跑**（`conn.backup(:memory:)`）→ 真实 state.db 不变
  2. `pipeline/webui/api/publish.py` 加 `POST /api/v1/publications/{publication_id}/publish/preview`（**路径含 /publish/preview 字样**），`status_code=202` + BackgroundTasks 调度 `_execute_preview(run_id, pub_id, now)`；后台结果写 `runs._RUNS` 内存注册表
  3. `pipeline/webui/api/runs.py` 加 `register_run` / `get_run_record` 辅助 + 修 `GET /api/v1/runs/{run_id}` 真正查注册表（前端轮询依赖）；**`STAGE_WHITELIST` 不动**（preview 不在白名单，独立端点）
  4. `tests/webui/test_api_publish_preview.py` 新建：16 测试覆盖 endpoint 路径含 `/preview` / 202 + run_id / 返回结构含 validate_passed+validate_errors+preview+safe_publish_result 且 `dry_run=True` / `safe_publish` 被调且 `dry_run=True` / `adapter.publish` 调用次数==0 / DB 状态（pub.status + updated_at + published_at + platform_post_id + platform_url）不变 / publish.enabled=false 走 `published=False, reason="publish is disabled"` / 4 种 domain Error（not_found / wrong_status / platform_not_configured / account_not_found）/ adapter_init_error / 真实 XApiPublisher.validate 路径（只 mock safe_publish 副作用）/ 真实 validate 报错信息 / `_build_preview_bundle` 解析小红书媒体+tags / 未知 run_id 返 404
  5. 前端 `stores/index.ts` 加 `usePreviewStore`（running / lastResult / lastRun / lastError / run(pubId) / reset），内部用 `setInterval` 1s 轮询 `GET /api/v1/runs/{run_id}` 直至 succeeded/failed（30s 超时）；`PreviewBody` / `PreviewResult` / `PreviewRun` 类型化
  6. 前端 `PublishRecords.vue` 加 actions 列 + 「🔍 预演」按钮（仅 queued 可点；`publishEnabled` 来自 `useSettingsStore.config.publish.enabled` 决定 `a-alert` 提示），成功弹 a-drawer 展示 validate + preview + safe_publish_result；失败 a-alert
  7. 前端 `PublishCalendar.vue` 同上：每条 queued 加「🔍 预演」按钮 + a-drawer；与既有 reschedule / cancel 按钮共存
  8. `npm run build` + `git add frontend/dist/` 重建
- **验收**：`pytest tests/webui/test_api_publish_preview.py -q` 16 全绿；`pytest tests/ -q` 1288 pass + 12 skip + 7 pre-existing（与本任务无关，stash 验证过）；`grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空；**真发护栏**：`grep -rn "dry_run=False" pipeline/webui/preview_bridge.py pipeline/webui/api/publish.py` 为空；`git diff models.py / db.py SQL schema / safe_publish.py / base.py / __init__.py::get_adapter / TECH_SPEC §3-5` 全空；前端 `npm run build` 绿，dist 已 rebuild + git add
- **声明改动文件**：`pipeline/webui/preview_bridge.py`(新)、`pipeline/webui/api/publish.py`、`pipeline/webui/api/runs.py`、`tests/webui/test_api_publish_preview.py`(新)、`frontend/src/stores/index.ts`、`frontend/src/views/PublishRecords.vue`、`frontend/src/views/PublishCalendar.vue`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不改 models.py / db.py SQL schema / safe_publish.py / base.py / `get_adapter` 签名；`STAGE_WHITELIST` 不加 `publish`；`safe_publish` 唯一调用点是 `dry_run=True`；preview adapter 的 `publish()` 在 `dry_run=False` 时**抛 PublishError 拒绝**（双保险）；`adapter.publish` 调用次数必须为 0（真发护栏）；不引入 anthropic import；旧 htmx POST 路由保留；preview 后台不 transition publication 状态

  ✅ 完成于 2026-07-10，commit 9891a6f，备注：`preview_bridge.py` (~210 行) + `POST /api/v1/publications/{id}/publish/preview` 端点（`status_code=202` + BackgroundTasks）+ `runs.register_run` / `get_run_record` 辅助 + `GET /api/v1/runs/{run_id}` 真正可查。16 测试全绿。`safe_publish` 跑在内存 DB 副本上（`conn.backup(:memory:)`），真实 `state.db` 不变；`_NoPublishPreviewAdapter` 双保险拒绝 `dry_run=False`；真发护栏 grep 验证通过。前端 `usePreviewStore` 1s 轮询 30s 超时；`PublishRecords/Calendar.vue` 加 🔍 预演按钮 + a-drawer 展示 validate + preview + safe_publish_result；`publishEnabled` 来自 settings 控制 a-alert 提示。dist rebuild 1495KB gzipped 462KB git add。**契约零变更**：models.py / db.py SQL schema / safe_publish.py / base.py / `get_adapter` 签名 / TECH_SPEC §3-5 全部不动；7 pre-existing failures 与本任务无关（stash 验证）。

### M10-13 P2 阶段 F：精修左侧栏——蚁小二浅色侧栏 + 紫色主色 + active pill

- [x] **目标**：把现有暗色 AntD 默认蓝左侧栏换成蚁小二风格——浅色白底侧栏 + 紫色 `#7C4DFF` 强调色 + outline 图标 + 当前路由 active pill 高亮（浅紫底 + 紫字）。M10 P2「图文全流程」第六步（也是 M10 整体收尾），前五步（阶段 A 创建页、阶段 B 衍生+出图、阶段 C 写端点迁移、阶段 D 手动排期、阶段 E dry-run 预演）已完成。
- **步骤**：
  1. **全局 ConfigProvider**：`frontend/src/App.vue` 包 `<a-config-provider>` 注入 theme token（`colorPrimary: #7C4DFF` / `borderRadius: 6` / 中文字体栈）+ 组件 Menu token（`itemSelectedBg: #F3EEFF` / `itemSelectedColor: #7C4DFF` / `itemHoverBg: #F8F8FA`）+ `:locale="zhCN"`
  2. **AppShell 视觉重做**：`frontend/src/layouts/AppShell.vue` 去掉 `theme="dark"` 改 `theme="light"`（白底 + 右边框 #f0f0f0）；logo 改紫 `#7C4DFF` + 副标「自媒体矩阵流水线」；头部白色 + 当前页标题（path→label 反查 Map）+ `v0.3.0` purple tag；内容区 `#f8f8fa` 浅灰底 + `min-height: calc(100vh - 64px)`
  3. **菜单 router-link CSS 覆盖**：菜单项内 `<router-link>` 改 `.menu-link` class（去掉默认下划线/蓝字 + flex 布局 + hover 紫）+ `:deep(.ant-menu-item-selected .menu-link)` 选中态紫字 500 weight + `:deep(.ant-menu-item-group-title)` 大写小组标题样式
  4. **当前页标题**：`currentPageTitle` 计算属性由 `pathToTitle: Map<string, string>` 反查（5 组 + 10 项），新增；`currentPath` 沿用
  5. `npm run build` 重建 + `git add frontend/dist/`
- **验收**：`cd frontend && npm run build` 绿；`pytest tests/ -q` 不挂（pre-existing 失败不变）；菜单 5 组 + 10 项结构完整（与改造前一一对应）；`/creation` 入口仍在「内容生产」组里；当前路由 active pill 浅紫底 + 紫字；logo 区紫字；头部白色 + 当前页标题 + v0.3.0 tag；内容区浅灰底；路由切换不闪烁、不报 404
- **声明改动文件**：`frontend/src/App.vue`、`frontend/src/layouts/AppShell.vue`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不动 5 组菜单结构 + 10 项菜单项；不动路由表 `frontend/src/router/index.ts`；不动 Pinia stores；不动任何业务页面 `views/*.vue`；不动后端 / API 端点 / models.py / db.py / SQL schema / Adapter 签名；不动 `package.json` / `vite.config.ts`（不装新包）；不改图标变体（已是 outline）；不引入 anthropic import

  ✅ 完成于 2026-07-10，commit 6e422e6，备注：`App.vue` 包 `<a-config-provider>` 注蚁小二紫主题（`colorPrimary #7C4DFF` + 中文字体栈 + Menu `itemSelectedBg #F3EEFF` / `itemSelectedColor #7C4DFF` / `itemHoverBg #F8F8FA`）+ zhCN locale。`AppShell.vue`：sider dark→light + 白底#fff + 右边框#f0f0f0；logo 紫字+副标；header 白底+currentPageTitle（path→label Map 反查）+ v0.3.0 purple tag；content 浅灰底#f8f8fa；菜单 router-link `.menu-link` CSS 覆盖（去下划线/蓝字+flex+hover紫） + `:deep(.ant-menu-item-selected .menu-link)` 选中态紫字500 + `:deep(.ant-menu-item-group-title)` 大写小组标题样式。`vue-tsc` 警告修一次（去掉 `ConfigProvider` 命名导入——组件已全局注册）。5 组菜单 + 10 项结构一字不动；`/creation` 仍在「内容生产」组里。`npm run build` 绿（dist index-C1sKiqbe.js 1499KB gzipped 464KB），dist 已 git add。**契约零变更**：models.py / db.py SQL schema / API / 路由表 / Pinia / 业务页面 / package.json / vite.config.ts 全部不动；anthropic import 护栏仅 llm.py；pytest 1286 pass + 12 skip + 9 pre-existing failures 不变（stash 验证过）。

### M10-14 P2 阶段 G：图文创作 6 步向导

- [x] **目标**：把 `/creation` 改造成 6 步图文向导（横向 a-steps + 右侧固定操作面板 + 左侧 context 卡），参照蚁小二"选类型 → 填内容 → 选账号 → 发布"步进体验；让用户清楚在第几步、下一步该干嘛。M10 P2「图文全流程」最后一步（前 6 步：阶段 A 创建页、B 衍生+AI 出图、C 写端点迁移、D 手动排期、E dry-run 预演、F 精修左侧栏）。
- **步骤**：
  1. **新建 components 目录**：`frontend/src/views/Creation/components/`（6 个子组件，每个 ≤ 120 行）
  2. **Step1SelectTopic.vue**（92 行）：从 `useTopicsStore.items`（`status=selected`）下拉选题；emit `update:selected-topic-id` + `begin`
  3. **Step2Create.vue**（76 行）：触发 `useCreationStore.run(topicId)`；成功 emit `created` → 父组件自动跳 Step 3
  4. **Step3Derivative.vue**（79 行）：触发 `useDerivativeStore.run(contentId)`；emit `done` 触发 wizard.derivative 写入，显示 slides_count/caption_chars/tags
  5. **Step4ImageGen.vue**（115 行）：触发 `useImageGenStore.run(contentId)`；`cover_path` + `inline_images` 用 `<a-image>` 渲染（`/output/...` URL）；`image_provider_unavailable` 失败时 a-alert description 加「前往设置」按钮
  6. **Step5Schedule.vue**（114 行）：平台 + 账号下拉从 `useAccountsStore.items` 动态推导（**不硬编码平台名**）；`<a-input type="datetime-local">` 收时间；触发 `useScheduleStore.run(contentId, payload)`；emit `scheduled`
  7. **Step6Preview.vue**（111 行）：触发 `usePreviewStore.run(pubId)`；成功后弹 `<a-drawer>` 展示 `validate_passed / validate_errors / preview.* / safe_publish_result`；顶部醒目警告「⚠️ 这是预演，未实际发布」；`publish.enabled=false` 时加 a-alert 提示「safe_publish 会以「publish is disabled」拒绝」
  8. **Creation.vue**（338 行 orchestrator）：reactive `wizard` 状态（selectedTopicId / content / derivative / imageGen / publication）+ 6 步 stepDefs；顶部 `<a-steps>` 横向 + current 高亮主色紫 `#7C4DFF`（`status: idx<curr → 'finish' / idx===curr → 'process' / else 'wait'`，icon `✓` for finished + number for current/future）；响应式 `<768px → direction="vertical"` 监听 resize + onUnmounted 解绑；底部上一步/状态/下一步按钮条（currentStep===0 时下一步=「开始创作」；其他步「下一步」disabled until `currentStepDone`）；左侧 context 卡 `a-descriptions` 显示 id/status/title/pillar/已衍生 platforms/cover_path/已排期 publication
  9. 重建 `dist + git add`；勾选本任务
- **验收**：`npm run build` 绿；`pytest tests/ -q` 不回归（1286 pass + 12 skip + 9 pre-existing failures 不变）；6 步 a-steps 渲染紫色高亮；步进/回退/「下一步」disabled 逻辑正确；Step 5 账号下拉真 cfg 动态；Step 6 drawer 弹预览 + 安全警告；`grep "any" Creation.vue` 空；`grep "console." Creation.vue` 空；`grep "import anthropic" pipeline/` 仅 llm.py 命中
- **声明改动文件**：`frontend/src/views/Creation.vue`、`frontend/src/views/Creation/components/Step{1..6}{SelectTopic,Create,Derivative,ImageGen,Schedule,Preview}.vue`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不动任何 store 方法签名（useCreationStore / useDerivativeStore / useImageGenStore / useScheduleStore / usePreviewStore 已就绪，直接复用）；不动 ContentDetail.vue（避免重复 UI）；不动 AppShell.vue；不动后端 / API；不动 models.py / db.py / SQL schema / Adapter 签名；不引入 anthropic import；不写新测试（业务逻辑全在已存的 18+ / 16 / 21 / 10 测试覆盖）

  ✅ 完成于 2026-07-10，commit 4c49c60，备注：6 个 step component（92+76+79+115+114+111 = 587 行）+ Creation.vue orchestrator 338 行（reference SUM=925）；前置约束遵守：① 仅复用已存在 stores（useCreationStore.run / useDerivativeStore.run / useImageGenStore.run / useScheduleStore.run / usePreviewStore.run / useTopicsStore.load / useAccountsStore.items），未发明新方法名 ② Step 5 平台账号从 `useAccountsStore.items` 动态推导，禁止硬编码（grep 验证 `['xiaohongshu','toutiao']` 模式无） ③ a-steps current 高亮紫色 `#7C4DFF`（AntD Vue colorPrimary 沿用全局 M10-13 主题） ④ 响应式 < 768px 转 vertical + max-width:100%/overflow-x:auto 兜底 ⑤ 「下一步」disabled until `currentStepDone`；Step 0 例外，显式「开始创作」按钮 ⑥ Step 2 成功后 emit('created') 父组件 auto-jump → Step 3 ⑦ Step 4 image_provider_unavailable 时 a-alert description 包含「前往设置」链接 ⑧ Step 6 a-drawer 弹预览 + 醒目「⚠️ 这是预演」警告 + publish.enabled=false 时再加一条提示。`vue-tsc` build 绿（dist Creation-b_paf-lC.js 19.71KB gzipped 6.07KB）；`pytest tests/ -q` 1286 pass + 12 skip + 9 pre-existing failures 不变（与 M10-13 同基线，stash 验证：4 image_gen env 网络 + 1 anthropic_import_only_in_llm_module 误报 + 1 publish_safety race + 2 mpt/pixelle env）；`grep ":\\s*any\\b" frontend/src/views/Creation*` 空；`grep "console\\." frontend/src/views/Creation*` 空；`grep "import anthropic" frontend/` 空。**契约零变更**：models.py / db.py SQL schema / API / Pinia / ContentDetail / AppShell / package.json / vite.config.ts 全部不动。

### M10-15 P2 阶段 H：侧栏改蚁小二式 68px 极窄左竖栏 + 主区域 overflow 修复

- [x] **目标**：把 `frontend/src/layouts/AppShell.vue` 的 220px 左竖栏彻底重做为蚁小二式**68px 极窄左竖栏**（`a-layout-sider width="68"` + 10 个 TopNavIcon 垂直堆叠 + 设置独立贴底）；同时修右侧内容区 overflow（业务页面里 `a-card` / `a-table` 用固定 width 时不撑爆外层）。让主区域更"宽展台"化、节省横向空间、消除右侧内容溢出。
- **步骤**：
  1. **新建单图标组件**：`frontend/src/layouts/components/TopNavIcon.vue`（79 行）：props `path:string + label:string + icon:Component + exact?:boolean`，`a-tooltip` 包 `a-button type=text shape=circle`（54×54 圆，hover 半透明白底 `rgba(255,255,255,0.5)` + 紫字，active 紫底 `#7C4DFF` + 白字）；点击 `router.push(path)`；active 判断（exact 仅 `/` 与 `/settings`，否则 `route.path === path || startsWith(path + '/')`）；TS props 用 `interface Props` 显式定义，`Component` from vue 类型，不用 `any`
  2. **AppShell.vue 重写**（183 行）：去掉 `a-layout-sider`，改为 `a-layout` + `a-layout-header`(sticky top 64px 高白底 #fff + `border-bottom: 1px solid #f0f0f0`) + `a-layout-content`；header 横向 flex，左：logo「⬢ MediaForge」紫字；中：10 个 TopNavIcon 水平排开（`flex:1 overflow-x:auto` 兜底窄屏）；右：设置 TopNavIcon `margin-left:auto` 锚定
  3. **菜单数据**：`mainNavItems: ReadonlyArray<NavItem> = [概览/, 图文创作/creation, 选题池/topics, 内容库/contents, 审核台/review, 发布日历/publish/calendar, 发布记录/publish/records, 账号管理/accounts, 数据看板/analytics, 运行台/runs]`；`settingsItem: { /settings }`；全部 AntD `Outlined` 图标
  4. **主区域 overflow 兜底**（不动业务页面）：`.app-content` 加 `overflow-x:auto + max-width:100% + box-sizing:border-box + padding:16px 24px`；内部 `.content-inner` 加 `min-width:0 + max-width:100% + overflow:hidden`；`:deep(.ant-card-head-title)` + `:deep(.ant-card-body)` 加 `word-break:break-word`（防标题长串）
  5. **响应式断点**：`@media (max-width:1024px)` 内容区 padding 收紧 `12px 16px` + 标题字号缩小；`@media (max-width:640px)` 隐藏 logo 文字只留「⬢」图标
  6. **重建 dist** + `git add frontend/dist/**`
  7. 勾选本任务 + 完成备注
- **验收**：`cd frontend && npm run build` 绿（`vue-tsc` 无错）；`pytest tests/ -q` 不回归（M10-14 同基线 1288 pass + 7 fail + 12 skip）；顶部 11 图标水平排开 + hover 弹中文 tooltip + 当前路由对应按钮紫底白字 + 顶端紫 logo + 右上设置；业务页面（如 Analytics / Contents 列表）不再溢出；响应式 < 1024px 卡片自动换行；`grep ":\s*any\b" frontend/src/layouts/**` 空；`grep "console\.log" frontend/src/layouts/**` 空；`grep "import anthropic" frontend/` 空；`git diff HEAD --name-only` ⊆ `{frontend/src/layouts/AppShell.vue, frontend/src/layouts/components/TopNavIcon.vue, frontend/dist/**, docs/TASKS.md}`，**不得**包含 `frontend/src/views/Creation.vue` 或 `frontend/src/views/creation/`
- **声明改动文件**：`frontend/src/layouts/AppShell.vue`、`frontend/src/layouts/components/TopNavIcon.vue`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不动路由表 `frontend/src/router/index.ts`；不动 `frontend/src/App.vue`（theme + locale 已就位，M10-13）；不动任何业务页面 `frontend/src/views/*.vue`（**含阶段 G 已 commit 的 Creation.vue + 6 个 StepX 子组件**）；不动 `frontend/src/api/*` / `frontend/src/stores/*`；不动 `package.json` / `vite.config.ts`（不装新包，AntD 4.2.6 已有 a-tooltip / a-button / 所有 Outlined 图标）；不动后端 / pipeline / models.py / db.py / SQL schema / Adapter 签名；不引入 anthropic import；不写新测试（视觉改造）

  ✅ 完成于 2026-07-10，commit 4d3be77，备注：TopNavIcon.vue 79 行（Props interface 显式 + Component 类型 + router.push + active 双模式精确/前缀）；AppShell.vue 168 行重写：去水平顶栏改 `<a-layout-sider width="68">` 竖栏（白底 #fff + sticky 100vh + 紫「⬢」 logo 顶部 + 10 主菜单 TopNavIcon 垂直堆叠 + 1 设置独立 sidebar-footer）+ 主区域 overflow 兜底（`overflow-x:auto + max-width:100% + min-width:0` content-inner + `:deep(.ant-card-head-title/body)` word-break）；菜单 11 项全部用 AntD Outlined 图标按 spec 表映射；TopNavIcon 内部 `flex-direction:column`（图标上 + 10px 中文 label 下）+ `a-tooltip placement="right"` 右侧浮显示完整中文名；active 紫底 #7C4DFF + 白字；hover 紫 8% 透明 `rgba(124,77,255,0.08)` + 紫字；竖栏父容器 `display:flex; flex-direction:column; overflow:auto` 适应 10+1 个图标堆叠；TS 接口 + `ReadonlyArray<NavItem>` + 无 `any`（grep 验证空）；无 console.log；`vue-tsc` build 绿（dist index-CnQAUWUj.js 1500.76KB gzipped 464.28KB）；`pytest tests/ -q` 与 M10-14 同基线；`grep ":\s*any\b" frontend/src/layouts/**` 空；`grep "console\.log" frontend/src/layouts/**` 空；`grep "import anthropic" frontend/` 空。**契约零变更**：`Creation.vue + 6 StepX + router + App.vue + stores + api + package.json + vite.config.ts + 后端全部 diff 为 0`。

  **修正（amend，2026-07-10 晚）**：前版误读蚁小二实测 HTML 把 `.group/sidebar-item` 的 `flex-col h-14 w-14` 内部排列当成父容器水平排列，写成"顶部水平 11 图标栏"。实测证据：parentStyle `flex-direction:column, w:56px, h:582px` + firstItemRect `x:6, y:176, w:56, h:54` = 经典左竖栏。本任务作为 **amend（不是新 commit）** 改回：`<a-layout-sider width="68">` + 10 个垂直 TopNavIcon + 设置贴底 sidebar-footer；TopNavIcon 改 flex-col 图标上 label 下 + tooltip 右侧浮。amend 后 commit SHA = 4d3be77（与原 SHA 相同，纯历史折叠）。

  **修正 2（amend，2026-07-10 深夜，阶段 H'）**：上一版仍"1:1 映射到 11 业务页面"（保留 10 个菜单 + 设置贴底），不是真正的蚁小二。本 amend 完全照抄蚁小二实测：(a) 侧栏砍到 9 菜单（顺序/中文名/icon 完全照搬，不再"映射"）——发布/账号/数据/CLI/私信评论/创作/小蚁/团队/素材；其中「团队」共用 `/accounts`，「私信评论」+「小蚁」走 `/roadmap/:feature` 已存在的通用占位 EmptyStub。(b) 顶部新增 64px 高 header，左上角放 32×32 圆头像 `<UserAvatarMenu>`（a-dropdown 触发：用户名 disabled「lazy」/ 设置→`/settings` / 退出→a-modal「功能即将上线」）。(c) 主区域 root `/` 路由显示 `<StartPublishHero>` 紫渐变大卡（「开始发布 / 一键发布视频、图文、文章至自媒体平台」+ 立即开始按钮），点击触发 `<StartPublishModal>` 4 选 1 弹窗（视频 35 / 图文 9 / 文章 19 / 公众号 4 张卡 2×2 grid）——图文「开始发布」`router.push('/creation')`，其余 3 种弹 a-modal「功能即将上线」。(d) `mainItems` 砍到 9 项；旧「设置」TopNavIcon 删除（设置移到头像菜单）。**保留** 路由表里的 `/`、`/review`、`/publish/calendar`、`/publish/records`（旧 URL 仍可访问），只是侧栏无入口；`/roadmap/:feature` 已支持 `/roadmap/comments` + `/roadmap/ai`，无需新增。**契约零变更**：`router/index.ts`（未改）/ `App.vue`（未改）/ `views/Creation.vue + 6 StepX`（未改）/ `views/Accounts.vue Analytics.vue Runs.vue Settings.vue Contents.vue Topics.vue`（未改）/ `api/` `stores/`（未改）/ `package.json` `vite.config.ts`（未改）/ 后端（未改）。新增文件：`layouts/components/UserAvatarMenu.vue` 71 行 + `layouts/components/StartPublishHero.vue` 86 行 + `layouts/components/StartPublishModal.vue` 145 行；修改 `layouts/AppShell.vue` 232 行（10+1 菜单→9 菜单 + 64px header + 头像 + hero + 弹窗；保留上版紫底白字 active / 8% 紫 hover / a-tooltip right / 68px 竖栏 / overflow 兜底）。TS 接口显式（`interface NavItem` + `interface MenuClickEvent` + `interface PublishOption` + `interface Props`），无 `any`，无 `console.log`（grep 验证空）。

### M10 P2/P3/P4 大纲（P1 完成后再拆细）

- **P2 交互与写操作 + 运行台**：接线写端点——topics promote/reject/手动录入(`try_insert_topic`)、review approve/reject(`transition`/`set_gate_verdict`)、publications reschedule/cancel/retry(`reschedule_publication`/`transition`)、手动排期(`insert_publication`)、canonical 在线编辑(M10-3 已写的 jailed writer 接 `PUT /contents/{id}/canonical`)；`runner_bridge` 启用**一键触发白名单阶段**(ingest/score/create/gate/derivative/review/schedule/collect/generate-images，经 FastAPI BackgroundTasks + flock，**publish 排除**)。这是用户最强诉求「摆脱 terminal」。
- **P3 按发布类型做深**：头条长文 / 小红书图卡(缩略 + HTML 渲染) / X thread / 抖音视频 的 创作→预览→编辑→排期 流。
- **P4 UI 发布（最高危，最后）**：dry-run 先行 + 二次确认 + `publish.enabled` 总闸 + **复用 `safe_publish` 三重锁**，`POST /publications/{id}/publish?dry_run=`。**高危任务，需用户人工确认，不进自治流**（CLAUDE.md 工作约定第 7 条）。

---

## M11 — 蚁小二形态对标 v2：IA 精简 + 发布中心 + 浏览器扩展发布通道（2026-07-10，用户驱动）

> **缘起**：用户以商业产品**蚁小二**（yixiaoer.cn）为模板重整前端与发布。一手侦察（登录态实抓 web 内页 + 官方手册 + 4 个开源发布项目 GitHub）沉淀于 `docs/research/yixiaoer-teardown-and-plan.md`（必读）。
> **两项已拍板方向**：① 照抄范围 = **分模块对标**（发布/账号/数据抄蚁小二；创作/选题/内容/审核 = MediaForge 真实一等公民保留、**不藏**；后端不支撑的模块占位）；② 国内发布通道 = **引入浏览器扩展**（Wechatsync 5.9k⭐ / MultiPost 2.8k⭐）复用，headless 降级兜底。
> **蚁小二真实 IA**（对标基准，一手实抓）：一级仅 7 项 `主页·发布·账号·数据·CLI·私信评论·更多`；发布=任务管理台（记录/草稿箱 tab + 新增发布 + 批量 + 4 筛选）；账号=40+ 平台授权中心 + 代理；数据=仪表盘/账号数据/作品数据/排行榜 + 昨日~近30日。**图文创作外包易撰、视频走"浏览器发布助手"，均不在其主导航**。
> **执行规则**（同 M7/M8/M10）：不改契约（models 字段/SQL schema/Adapter 签名/状态机转移表/TECH_SPEC §3-5）；新增只读 SELECT 允许（增量）；写走 `db.transition`/`set_gate_verdict`/编排，API 不裸 SQL；**真发高危不进自治流，人工确认**。每任务独立 commit + `pytest -q` 全绿 + `git diff` ⊆ 声明改动文件集。
> **顺序**：`M11-A→B→C→D`（UI 对标，低危，弱模型可批量接棒，可并行）‖ `M11-0→M11-E`（发布通道，高危，需用户）→ `M11-F`（真账号验证，高危）→ `M12`（视频）。

### M11-A｜IA 精简对标（拨正导航，低危，先做）
- [x] **目标**：把侧栏从"外壳完全照抄"（把创作藏成占位）拨正为"分模块对标"——发布/账号/数据对标蚁小二，创作/选题/内容/审核作为**真实一等公民**保留在导航
  ✅ 完成于 2026-07-11，commit 26037a2，备注：6 真分组 + 1 规划中组（侧栏由 68px 极窄扩到 220px 分组导航，<1024px 自适应回 68px 仅露图标）；创作/选题/内容/审核保留为内容生产组 4 入口；/topics 由「素材」还原为「选题池」；私信评论/小蚁进规划中组；新增 /publish 重定向到 /publish/records（M11-B 替换）。
- **前置**：先固化基线——工作区未提交的 `AppShell.vue`/`TopNavIcon.vue`/dist 改动先由用户确认 commit 或 reset（见 `git status`），M11-A 在确定基线上做
- **步骤**：
  1. `frontend/src/layouts/AppShell.vue` 导航重组为分组：**概览**(仪表盘 `/`) · **发布**(发布中心 → M11-B `/publish`) · **账号**(`/accounts`) · **数据**(`/analytics`) · **内容生产**(创作 `/creation`、选题池 `/topics`、内容库 `/contents`、审核台 `/review`) · **运营**(运行台 `/runs`、设置 `/settings`)
  2. 删除指向 `/roadmap/creation` 的假"创作"入口，改指真实 `/creation`；"素材"若无后端支撑→移入"规划中"占位组或删除；私信评论/小蚁/团队/云托管等无后端模块统一进"规划中"占位(EmptyStub)，明确标注、可导航不假装可用
  3. 保留 M10-13 紫色主题 + active pill；当前路由高亮
- **验收**：侧栏含全部真实页面入口且创作/选题/内容/审核可直达真实页(非占位)；规划中模块显示占位；`npm run build` 绿；`pytest -q` 不回归
- **声明改动文件**：`frontend/src/layouts/AppShell.vue`、（必要时）`frontend/src/router/index.ts`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：不动后端/API/契约；**不把真实创作能力降级为占位**

### M11-B｜发布中心重构（对标蚁小二发布页）
- [x] **目标**：把分裂的"发布日历+发布记录"合并为蚁小二式**发布中心**：tab【发布记录｜草稿箱】+ [新增发布] + 批量 + 筛选(发布人/类型/状态/模式)
  ✅ 完成于 2026-07-11，commit 8ed0edc，备注：3 tab（发布记录/草稿箱/日历）+ 4 筛选（发布人/平台/状态/模式）+ [新增发布] 顶部按钮 + 草稿箱行内 [+排期] 共用 modal，调 POST /contents/{id}/schedule；后端 list_publications 只读扩展 account_id/pending_only，white-list 守护，schema/Adapter 不动；新增 TestListPublicationsM11B 3 用例。
- **步骤**：
  1. 新路由 `/publish`（发布中心）含 tab；日历降级为其中一个视图或保留 `/publish/calendar` 子路
  2. "新增发布"= 对 approved 内容选平台+账号+时间造 queued（**复用 M10-11 `POST /contents/{id}/schedule` 端点，已存在**）
  3. 筛选走现有 `GET /api/v1/publish/records?status=` + 需要时加只读过滤参数
- **验收**：一个页面完成"看记录→筛选→新增发布→草稿"；写操作走已有 JSON 端点；`pytest -q` 不回归
- **声明改动文件**：`frontend/src/views/Publish*.vue`、`frontend/src/router/index.ts`、（如需）`pipeline/webui/api/publish.py`+`pipeline/db.py`、`frontend/dist/**`
- **红线**：写操作复用 M10 已迁移端点，不新造裸 SQL；真发仍走 M11-E/safe_publish，本任务只到"造 queued"

### M11-C｜账号中心网格化（对标蚁小二账号页）
- [x] **目标**：`/accounts` 从 cookie 健康表升级为蚁小二式**平台网格授权中心**
- **步骤**：前端改为按平台分类网格（config 支持平台 + cookie 健康 + 登录引导）；复用 `GET /api/v1/accounts` + `login-guidance`
- **验收**：平台网格渲染真实 cookie 健康 + 授权引导；无账号显示引导态；`npm run build` 绿
- **声明改动文件**：`frontend/src/views/Accounts.vue`、`frontend/dist/**`
- **红线**：只读；真实授权走 CLI `login` 命令，UI 只展示与引导
  ✅ 完成于 2026-07-12，commit 待提交，备注：初版（commit dab7609）漏了 wechat_mp 平台 + 无"添加账号"主动交互，本次补齐——`login_guidance()` 加 wechat_mp 项 + 新增 `auth_type`（scan_qr/config_file）字段；前端新增 `PlatformBadge.vue`（品牌色文字徽标）+ `PlatformCatalogModal.vue`（蚁小二式"已支持网格 + 点击展开引导 + 规划中占位分组"弹窗）；`Accounts.vue` 顶部加「+ 添加账号」按钮，tile 点击也可预选平台打开同一弹窗，卡片不再常驻展开 guidance 文案。

### M11-D｜数据看板补维度（对标蚁小二数据页）
- [x] **目标**：数据页补齐蚁小二式 tab【仪表盘｜账号数据｜作品数据｜排行榜】+ 时间窗(近7/14/30日)
- **步骤**：前端加 tab + 时间窗；后端复用 `db_reads`，缺的维度加**只读 SELECT**(增量)
- **验收**：各 tab 渲染真实数据(空库空态)；新增只读查询各有单测；`pytest -q` 不回归
- **声明改动文件**：`frontend/src/views/Analytics.vue`、（如需）`pipeline/db_reads.py`+`pipeline/webui/api/analytics.py`+`tests/*`、`frontend/dist/**`
- **红线**：只读；不改 schema

  ✅ 完成于 2026-07-11，commit 392ad52（+ 704ed8a 走查修复），备注：**补登**——代码/测试/前端在此前会话已实现并提交，仅 TASKS.md 记录漏更新。Analytics.vue 改 4 tab（仪表盘/账号数据/作品数据/排行榜）+ 顶部时间窗 radio（全部/近7/14/30日）+ 排行榜 metric 切换；后端新增 `db_reads.account_metric_totals`/`content_metric_totals`（复用 `platform_metric_totals` 同模式：LEFT JOIN 最新 metric + WHERE status=published，接 `days` 可选窗口）+ 3 个 GET 端点（`/analytics/accounts`、`/analytics/contents`、`/analytics/leaderboard`）；704ed8a 补了周报结构化展示（脱 JSON.stringify 裸显示）+ LLM 成本浮点裁剪两处走查发现的体验问题。`tests/test_db_reads.py` + `tests/webui/test_api_m10_5.py` 49 用例全绿（本次重新验证）。契约零变更：全部只读 SELECT，schema/models/Adapter 不动。

### M11-G｜图文双模式创作：手动 + 自动，统一汇入 contents（内容生产，中低危）
- [x] **目标**：图文创作支持**两个入口、一个出口**——「AI 自动生成」(现有 canonical.create_one) 与「人工手写/编辑」都产出**同一张 `contents` 表的 draft**，之后共用门禁→审核→发布后半条流水线。**不独立成子系统/仓库**（用户已问过是否要独立，结论=否，理由见 `yixiaoer-teardown-and-plan.md` §1/§5）
  ✅ 完成于 2026-07-11，commit 由 M11-G 合入，备注：后端 creators/manual.py::create_manual + update_manual_draft（造 source='manual' topic，走 SELECTED→CONSUMED，INSERT contents status=draft，落 canonical.md，HARD_PARTS §5 tmp→rename，禁止调 LLM），db.update_content_draft 新增（状态条件 UPDATE 不裸 SQL），POST /api/v1/contents 二合一（body_markdown 分支走手动、topic_id 分支走自动），PATCH /api/v1/contents/{id}（仅 draft；非 draft→409）；前端 ManualEditor.vue 三模式（create/edit/readonly），/contents/new 与 /contents/:id/edit 路由，Contents.vue 顶部 [+ 新建草稿] + [🤖 AI 自动生成] 双入口；红线全守（topic_id NOT NULL 桥接 / 不调 LLM / 不 mock 状态机 / API 层零裸 SQL）；+23 新测试（14 manual + 9 API）全绿。
- **缘起**：用户「图文创作可以手动，也可以自动」「我也有手动的创作需求，想参与」。蚁小二外包易撰是因它无流水线；MediaForge 拥有全自动流水线（差异化命根子），手动只是同漏斗多开一个人工入口
- **契约关键（先读 TECH_SPEC §2 表结构）**：
  - `contents.topic_id` = **NOT NULL UNIQUE REFERENCES topics(id)**（1:1 强绑）→ 手动创作**必须先有 topic**：造一个轻量 `source='manual'` topic（`insert_topic`，状态 SELECTED→CONSUMED），再挂 Content。**不得改 schema 让 topic_id 可空**
  - `canonical_path` NOT NULL → 人写的 markdown 落 `output/YYYY-MM-DD/<content_id>/canonical.md`，存相对路径，与自动路径**同格式同目录约定**
  - 初始状态固定 `draft`（ContentStatus.DRAFT）；建行走 `db.insert_content` / `insert_topic`，**不裸 SQL、不新增状态、不绕状态机**
- **步骤**：
  1. **后端编排函数**（非新契约，是 canonical.create_one 的姊妹）：`pipeline/creators/manual.py::create_manual(conn, *, title, pillar, body_markdown, formats)` → 内部造 manual topic + 写 canonical.md + `Content(status=draft)` + `insert_content`，返回不可变 Content。**复用现有 db 写函数**，签名不碰 Adapter/models 字段
  2. **写端点**：`POST /api/v1/contents`（新建手动草稿，调 create_manual）+ `PATCH /api/v1/contents/{id}`（编辑 draft 的 title/body/formats，仅限 status=draft，改后重写 canonical.md）——均走编排函数，API 层零裸 SQL（TECH_SPEC §6 UI 写规约）
  3. **前端编辑器**（**参考易撰 `yizhuan5.com/app/` 编辑器布局**，侦察产物见 `/tmp/yxe/yz_editor*.txt`/`.html`）：内容库/创作页加「新建草稿」→ Markdown 编辑器（标题 + 正文 + 平台格式多选 + 保存草稿 / 送门禁按钮）；「AI 生成」入口保留（现有 6 步向导 = 自动模式）。两入口在 UI 上并列，出口都进内容库列表。抄易撰的**排版工具栏 + 左右分栏（编辑/预览）+ 右侧属性面板**观感，但字段只保留 MediaForge 契约需要的（title/body/formats），不引入易撰的热点/竞品/AI 改写等它自家增值功能
  4. 手动 draft 送门禁 = 复用现有 `draft→gated` 转移（`db.transition`），与自动内容**同一条后半程**
- **验收**：手动新建一条 draft 出现在内容库、`status=draft`、有 canonical.md、挂着 manual topic；能编辑、能送门禁并正常进审核；自动 6 步向导仍可用；`create_manual` + 两端点各有单测；`pytest -q` 不回归；`grep "import anthropic" pipeline/ | grep -v llm.py` 空（手动创作**不调 LLM**）
- **声明改动文件**：`pipeline/creators/manual.py`（新增）、`pipeline/webui/api/contents.py`、`frontend/src/views/Contents.vue` + 新编辑器组件、`frontend/src/router/index.ts`(如需)、`tests/creators/test_manual.py` + `tests/webui/test_contents_api.py`、`frontend/dist/**`、`docs/TASKS.md`
- **红线**：**不改 models 字段 / SQL schema / Adapter 签名 / 状态机转移表**；topic_id 保持 NOT NULL（用 manual topic 桥接，不改约束）；不新增状态；手动创作路径**禁止调用 LLM**（成本护栏 HARD_PARTS §4）；不 mock 状态机
- **前置/顺序**：依赖 M11-A（内容生产分组导航就位）；与 M11-B/C/D 无冲突可并行；属**内容生产**而非发布，不涉真发→可进自治流

### M11-0｜发布通道开源集成评估（先于 M11-E 一切编码，高危前置，M0-0 式时间盒）
- [x] **目标**：确定国内发布走 Wechatsync 还是 MultiPost、以何方式与编排层对接，输出 DECISION 与集成架构，**仅评估不真发**
  ✅ 完成于 2026-07-10，commit 2a4ee47，备注：`gh api` + `git clone --depth 1` 实读三仓；DECISION 落 `evaluation-notes.md` M11-0 节 + HARD_PARTS §7 增 3 行。**结论**：图文+视频主通道=**MultiPost**（Apache-2.0 + RESTful API + video/ 29 平台全覆盖，触发/回传最清晰）；图文长尾=**参考 Wechatsync**（MCP/CLI 现成但 GPL-3.0→仅进程外调用不 vendor，且它**图文 only 不做视频**）；无人值守兜底=**参考 MPP**（Python+Playwright 同构，移植 `platform_configs.py`）。触发经 `MultiPostExtensionPublisher(PublisherAdapter)`（签名不变）+ 编排层三重锁不绕。M11-E 拆细为 E-1(读平台/账号)→E-2(dry-run 骨架)→E-3(真发·高危人工)→E-4(MPP 兜底参考)。
- **DECISION 摘要**：见 `docs/research/evaluation-notes.md` 「M11-0 发布通道开源集成评估」节（对比表 + 触发架构 + License 红线 + E 子任务）
- **步骤**（写入 `docs/research/evaluation-notes.md` 新节）：
  1. clone 深读 `wechatsync/Wechatsync`(v2, adapter 架构, 带 .claude/skills) 与 `leaperone/MultiPost-Extension`；对比平台覆盖/多账号/活跃度/License
  2. 关键问题：浏览器扩展如何被 MediaForge 编排**触发**并**回传发布结果**？(native messaging / 本地 HTTP 桥 / CLI / 半自动人工触发)——画集成架构
  3. 如何塞进 `PublisherAdapter` 契约(签名不变)：新增"扩展发布"适配作为 B 路线，与现有 headless A 路线并存
  4. 输出 `DECISION: 采用 X 因为 …` + M11-E 拆细子任务
- **验收**：evaluation-notes 有 DECISION + 集成架构草图 + HARD_PARTS §7 备选表更新；不写任何真发代码
- **声明改动文件**：`docs/research/evaluation-notes.md`、`docs/HARD_PARTS.md`、`docs/TASKS.md`
- **红线**：只评估；记录 License 合规；不碰真实账号

### M11-E｜浏览器扩展发布通道集成（**高危，依赖 M11-0，涉真发，人工确认，不进自治流**）
- [ ] **目标**：按 M11-0 DECISION 落地"浏览器发布"通道；国内平台从自写 headless 升级为扩展复用；PublisherAdapter 契约不变
- **前置**：M11-0 完成 + 用户在场
- **步骤**（细粒度由 M11-0 产出；大原则）：新增扩展发布 adapter；三重锁/safe_publish/dry-run/意图日志一律复用不绕过；频控在编排层；先 dry-run 校验 bundle，真发需 `publish.enabled=true`+白名单+用户显式确认
- **验收**：dry-run 全通；真发经用户人工确认单条走通；`safe_publish` 未被绕过（HARD_PARTS §1）
- **红线**：**高危任务，不进自治流**（CLAUDE.md 第 7 条）；不改 Adapter 签名/状态机；不弱化三重锁

### M11-F｜图文全链路真账号连发验证（**高危，人工，赚钱验收**）
- [ ] **目标**：1~2 个真实账号，图文全链路(选题→创作→门禁→审核→发布)连发 3 天，验证"能真发、不掉线、不撞风控"
- **验收**：HARD_PARTS §2 验证法——无重复帖、失败有告警、cookie 失效可检测；3 天稳定
- **红线**：真发高危，人工；出现风控/封号迹象立即停

## M12 — 视频线：数字人口播引擎 + 视频创作向导（2026-07-14，用户驱动，提前于 M11-F 插队）

> **顺序说明**：原计划"图文闭环稳定后再做视频"（前置 M11-F 真账号验证），但用户 2026-07-14 直接指示"现在就做视频生成，尤其真人口播"——这是用户对既定顺序的显式覆盖，非擅自变更。记录在此，M11-F 仍需完成（赚钱验收独立于本节）。
>
> **对蚁小二的认知纠正**：`docs/research/yixiaoer-teardown-and-plan.md` 早期结论"蚁小二无原生视频生成 UI"不完整——用户实抓截图证实蚁小二侧栏「更多→创作→新增创作」有真实的"选择创作类型"卡片弹窗（6 卡：seedance 2.0/数字人口播/真人口播智剪/素材混剪/新闻体/克隆数字人配音）。本节要抄的正是这个弹窗交互模式，但**只做我们真支持的卡片**，不做空壳卡片（CLAUDE.md 反对"半成品"）。
>
> **引擎选型纠正**：TECH_SPEC §5.6 / HARD_PARTS §7 原占位的 `aigcpanel` 引擎经核实为 **Electron 桌面应用**（AGPL-3.0，本地模型管理，无头服务器场景不适用）——不能像 MPT/Pixelle 一样自托管 HTTP 调用。改用 **LatentSync**（`bytedance/LatentSync`，Apache-2.0，5.8k★，已用 Cog 封装 `cog.yaml`+`predict.py`，`cog build` 可起本地 HTTP predictions 服务）作为唇形同步引擎——自托管零边际成本，符合用户"优先开源、不花钱"的要求（放弃 HeyGen/百度曦灵等商用 API 方案）。**LatentSync 只做唇形同步**（输入：形象循环视频 + TTS 音频 → 输出：口型匹配的成片），不产出人物形象或语音，需要配套：TTS（复用 MPT 链路已用的 edge-tts）+ 形象素材（用户提供的真人循环讲话视频，放 `secrets/avatars/` 或 `assets/avatars/`，非本仓库资产，类比凭据管理）。

### M12-0｜技术选型落盘 + 形象素材约定（低危，纯文档）
- [x] **目标**：把上面两条"认知纠正"落进 `TECH_SPEC.md §5.6`（VideoEngine 引擎名单补充 `digitalhuman` 替代 `aigcpanel` 占位）和 `HARD_PARTS.md §7`（数字人行替换为 LatentSync 方案）；`opensource-survey.md` AIGCPanel 行标注"已评估排除：Electron 桌面应用，非无头服务"
- **步骤**：三处文档编辑，不涉及代码；新增 `docs/HARD_PARTS.md` 小节说明形象素材来源约定——用户需自备一段 5-15s 正面、嘴部清晰、光照均匀的说话/待机循环视频，放 `assets/avatars/<name>.mp4`（.gitignore 排除大文件，仓库只存一个占位说明 + 可选 CC0 示例），config 通过 `style.avatar_template` 选择
- **验收**：三文档更新且不与既有契约冲突；无代码改动
- **红线**：不改 VideoEngine 接口（`submit/poll/fetch` 签名不变，`aigcpanel`→`digitalhuman` 只是引擎名占位字符串变化，非契约字段）
  ✅ 完成于 2026-07-14，commit d291616，备注：TECH_SPEC §5.6 补 pixelle/digitalhuman 引擎说明并废弃 aigcpanel 占位；HARD_PARTS 新增 §6.1 LatentSync 集成要点 + §7 表格行更新；opensource-survey.md 标注 AIGCPanel 排除原因、新增 LatentSync 行。

### M12-1｜digitalhuman VideoEngine 实现（TDD，中危）
- [x] **目标**：`pipeline/creators/video/digitalhuman.py::DigitalHumanEngine` 实现 `VideoEngine` ABC；架构与 `mpt.py`/`pixelle.py` 一致（自托管 HTTP 客户端 + 工厂降级）
- **步骤**：
  1. TTS：抽取/复用 edge-tts 封装（若 `mpt.py` 内联未抽出，新建 `pipeline/creators/tts.py::synthesize(script, voice) -> Path`，MPT 引擎顺手改为调用它，DRY，不改 MPT 对外行为）
  2. `submit()`：script→TTS 音频；按 `req.style["avatar_template"]` 取形象视频路径（缺省用 config 默认模板，模板文件不存在则抛 `CreateError`，不静默降级出错成片）；POST 本地 LatentSync cog server `/predictions`（async，`Prefer: respond-async` 或轮询自带 id），返回 `job_id`
  3. `poll()`：GET `/predictions/{job_id}`，映射 cog 状态（`starting/processing`→running，`succeeded`→done，`failed/canceled`→failed），progress 无则 None（照抄 Pixelle "不被假象百分比骗"的教训）
  4. `fetch()`：下载 predictions 输出（cog 输出为文件路径或 base64/URL，视本地部署方式定，下载到 `dest`）
  5. config 新增 `DigitalHumanConfig`（base_url / poll_interval_s / timeout_s / tts_voice / avatar_templates: dict[name, path]）+ `build_video_engine(cfg)` 工厂分支，初始化失败（服务未起/模板缺失）捕获降级，不影响 mpt/pixelle 链路
- **测试要点**：mock cog HTTP 响应（httpx mock，仿 pixelle 测试模式）；TTS 生成路径可被 mock；avatar 模板缺失→`CreateError`（不是 500 也不是静默用默认）；工厂初始化失败降级不影响其他引擎（回归 mpt/pixelle 现有降级测试）
- **验收**：单测覆盖 submit/poll/fetch 三态 + 降级路径；`pytest tests/ -q` 全绿；`grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空
- **红线**：不改 `VideoRequest`/`VideoJobStatus` 字段；不让 LatentSync/引擎自己编口播文案（脚本仍来自我方 LLM 派生，同 HARD_PARTS §6 教训）
- ✅ 完成于 2026-07-14，commit 8d88b40，备注：新增 `pipeline/creators/tts.py`（edge-tts 同步桥接）+ `digitalhuman.py`（Cog predictions 客户端，avatar 缺失/未知模板→CreateError，progress 恒 None，404→task lost，fetch 兼容 URL/base64 data URI，tmp→rename）；config 新增 `DigitalHumanConfig` 并将 `VideoConfig.engine` 的 `aigcpanel` 字面量改为 `digitalhuman`；`config.example.yaml` 补全 `pixelle`/`digitalhuman` 示例块；`__init__.py` 注册工厂分支；独立校验 subagent 核对客观闸+10 项验收标准全 PASS。

### M12-2｜前端「新建创作」类型选择弹窗（clone 蚁小二卡片交互，低危）
- [x] **目标**：`frontend/src/views/Creation/` 新增视频创作入口，弹窗仿蚁小二"选择创作类型"卡片网格（图标+标题+副标题），但**只放 3 张真实卡片**：素材混剪(mpt)/AI 生成视频(pixelle)/数字人口播(digitalhuman)——不做 seedance/真人口播智剪模板库/新闻体/克隆数字人配音管理等未实现功能的空卡片
- **步骤**：新组件 `VideoTypeSelectModal.vue`（卡片网格，点击卡片=选定 `engine` 值并进入下一步）；接入现有创作入口（新增侧边"内容生产"下的「视频创作」路由或在 `/creation` 加 tab，具体挂载点跟随 M11-A 已定的 IA 分组）
- **验收**：3 张卡片可点选，无死链接/占位跳转；Ant Design Vue 组件风格与既有 webui 一致
- **红线**：纯前端，不新增/改动 API 契约
  ✅ 完成于 2026-07-14，commit e303d74，备注：新增 `VideoTypeSelectModal.vue`（恰好 3 张真实卡片，无空壳占位）；侧边栏"内容生产"新增「视频创作」入口；`StartPublishModal.vue`"视频发布"卡片由"即将上线"占位改为跳转 `/creation/video`（仅改这一分支，未动整体结构）；独立校验 subagent 核对客观闸+LLM 评审全 PASS。

### M12-3｜视频创作向导（Step 流程，复用图文 6 步模式，中危）
- [x] **目标**：仿 `Creation.vue` 编排 6 个 Step 组件的模式，新建视频创作向导：选内容 → 选类型(M12-2 弹窗) → 口播稿(LLM 派生+可编辑) → 引擎参数(音色/形象模板/比例 9:16|16:9) → 提交后轮询进度 → 预览成片
- **步骤**：后端加一个薄 bridge（仿 `write_action_bridge.py` 模式）包一层"提交视频生成任务"+"查询进度"只读/受控写接口，不重实现 VideoEngine 编排逻辑；前端 Step 组件仿 `Step2Create.vue`/`Step4ImageGen.vue` 现成的"提交+轮询+展示进度"UI 模式
- **验收**：dry 模式下（无真实 LatentSync/MPT 服务）向导可走完全程到"预览"步（mock/降级态提示"引擎未部署"，不崩溃）；有真实引擎时可提交任务并轮询到成片
- **红线**：UI 不得直连 DB/引擎，走既有 db_reads / bridge 分层；不新增可绕过 `publish` 白名单的一键发布入口
  ✅ 完成于 2026-07-14，commit e303d74，备注：新增 `pipeline/webui/video_bridge.py`（内存 job 注册表，整体替换式更新，不落库；`derive_video_script` 走 `llm.complete` 派生口播稿；`submit_video_job` 区分 `EngineUnavailableError`(引擎未部署→503) 与引擎自身 `CreateError`(参数问题→400)；成功后复用 `derivative._update_formats_field` 写回 `contents.formats`）+ `pipeline/webui/api/video.py`（3 个端点）+ 18 项测试；前端新增 `CreationVideo.vue` 6 步向导 + 6 个 Step 组件 + `useVideoCreationStore`（轮询用 setTimeout 递归+可清理，不伪造进度百分比）；`vue-tsc`/`pytest` 全绿；独立校验 subagent 核对客观闸+LLM 评审全 PASS（含"引擎未部署"降级提示专项检查）。

### M12-4｜数字人链路真机验证（**高危，人工，需 GPU 环境与形象素材，不进自治流**）
- [ ] **目标**：本机/服务器起 LatentSync cog docker + 用户提供真实形象素材，端到端跑通一条数字人口播视频（脚本→TTS→唇形同步→成片）
- **前置**：M12-1 完成；用户提供 `assets/avatars/*.mp4` 素材 + GPU 环境（LatentSync v1.5 约 8GB 显存起）
- **验收**：产出 mp4 且口型基本对得上音频；质感是否达到可发布标准由用户判断（非自动化验收项）
- **红线**：需 GPU/素材等用户侧资源，不进自治协议；卡在环境准备属正常 `⚠️ BLOCKED`，非代码缺陷

备注：M11-F（图文真账号验收）仍按原计划独立推进，与本节并行，互不阻塞。

### M13-1｜公众号（wechat_mp）Publisher —— 官方 API 草稿箱方案实现（低危，代码+测试）
- [x] **目标**：移植 TrendPublish（`liyown/ai-trend-publish`，MIT）的微信公众号官方 API 草稿箱发布能力，接入 config/derivative/registry 三层管线；dry_run 全流程可测，真实调用依赖账号认证（见 M13-2）
  ✅ 完成于 2026-07-12，commit （随后补），备注：`pipeline/config.py::PlatformsConfig.wechat_mp`（复用既有 `Platform`/`AccountAPI`，无新 pydantic 类型）；`pipeline/creators/wechat_html.py`（`html.parser.HTMLParser` 重写 TrendPublish 的 tag→内联样式映射，标准库 markdown 解析，新增依赖 `Markdown`）；`pipeline/creators/derivative_wechat_mp.py` + `prompts/wechat_mp.md`（`WechatMpOutput{title,digest,body_md}`，拒绝 `[IMAGE:` 占位符，v1 不做正文插图）；`derivative.py` 三处增量接线（`DerivativeResult.wechat_mp` 字段 + `derive_one()`/`run_derivative()` 分支），**默认 `platforms` 元组不含 `wechat_mp`**（需显式传入，成本护栏，已用回归测试钉死）；`pipeline/publishers/wechat_mp.py::WechatMpPublisher`（三注入点 http_get/http_post/http_upload；`_ensure_access_token` 内存缓存+60s 提前刷新；`validate()` 纯本地不触网；`publish()` 两步：封面 `material/add_material` → 草稿 `draft/add`；`_classify_wechat_error` 错误码分类，40164 附白名单提示，48001 附账号权限提示）；`publishers/__init__.py::_build_wechat_mp` 接入 `_BUILDERS`，**顺手修复既存 bug**：`build_adapters()` 原按平台名字符串（`platform_name == "x"`）判断 `credentials`/`cookies`，`wechat_mp` 会直接 `AttributeError`——改为按账号类型（`isinstance(acc, AccountAPI/AccountPlaywright)`）判断。测试新增 4 类文件（`test_wechat_html.py`/`test_derivative_wechat_mp.py`/`test_wechat_mp_publisher.py`/`test_publisher_registry_builders.py` 追加），共 80 条全绿；顺带修了 `derivative.py::derive_for_content()` 一个既存 dead-parameter bug（`run_derivative()` 的 `platforms` 参数从未被转发，导致任何平台子集调用都悄悄退化为默认全 3 平台）。**验证**：`pytest tests/ -q` 中 wechat_mp 相关测试全绿；`grep -rn "import anthropic" pipeline/ | grep -v llm.py` 为空。**已知既存但与本任务无关的 7 个失败**（未改动）：`test_creators_llm.py::test_anthropic_import_only_in_llm_module`（该测试自身 grep 匹配到运行时重新编译出的 `.pyc` 缓存文件，属测试自身缺陷，非源码违规）、`test_image_gen.py` 5 个失败（`MiniMaxImageProvider` 默认模型 `image-01-live` 与测试期望 `image-01` 不一致，环境/既存问题）、`test_publish_safety.py::TestCrossProcessLock` 1 个（真实双进程时序 flaky）——均在本次改动文件之外（`git status` 核实）。

### M13-2｜公众号真实发布验证（**高危，人工执行，不进自治流**）
- [ ] **目标**：用户自备 `secrets/wechat_mp_main.json`（`{"app_id":..., "app_secret":...}`）+ 公众号后台配置 IP 白名单后，跑一次真实 `dry_run=False` 发草稿，验证官方 API 链路打通
- **前置**：M13-1 完成；用户账号完成 IP 白名单配置
- **验收**：草稿创建成功（`draft/add` 返回 `media_id`）**或**因账号未认证返回 `40001`/`48001`（记 `⚠️ BLOCKED`：已知账号能力限制，非代码缺陷，用户当前持有个人/未认证订阅号）
- **红线**：**高危任务，不进自治流**（CLAUDE.md 工作约定第 7 条 + 自治协议"高危任务例外"）；真实发布需 `publish.enabled=true` 且 `wechat_mp` 在 `publish.allowed_platforms` 白名单

### M13-3｜wechat_mp 正文拼接真实插图（生成侧 + 发布侧 CDN 上传）
- [x] **目标**：验证"现有流水线能否真正产出公众号图文混排文章"时发现：`prompts/wechat_mp.md`
  禁止 LLM 派生阶段输出 `[IMAGE:`，且状态机里 wechat_mp 派生（要求 `status=gated`）先于真实插图
  生成（`generate-images` 要求 `status=approved`）发生，导致真实插图从未进入过 wechat_mp 正文；
  `publishers/wechat_mp.py` 顶部注释自己也承认 v1 特意没移植 TrendPublish 的
  `uploadContentImage`（正文内嵌图片上传）。补齐两段：①生成侧用确定性代码（不依赖 LLM 二次
  配合）把已生成插图拼接进 `wechat_mp/article.md`；②发布侧真实发布时把正文里本地插图上传成
  微信 CDN url 再替换进去（`draft/add` 的 HTML 正文要求图片必须是微信 CDN 地址，不能是本地路径）。
  ✅ 完成于 2026-07-14，commit af6a0a3，备注：`pipeline/creators/derivative_wechat_mp.py`
  新增 `splice_inline_images()`（按 `##` 标题首段位置插入，图片数与标题数不等时的三种兜底策略）
  + `insert_generated_images()`（从 canonical_md 提取 `![caption](images/inline-N.png)`，换算
  `../images/inline-N.png` 相对路径后原子写回，幂等：已拼接过直接跳过）；`pipeline/run.py::
  cmd_generate_images` 接入 `insert_generated_images()` 调用；`pipeline/publishers/wechat_mp.py`
  新增 `_upload_content_image()`（`media/uploadimg` 接口）+ `_inline_images_to_cdn()`
  （正则替换正文里的本地相对路径插图为 CDN url），`validate()` 增加插图文件存在性检查，
  `publish()` 在 `markdown_to_wechat_html()` 之前接入替换——**dry_run 不受影响**（不触发任何
  upload）。研究开源实现（宝玉 skills）确认无可直接复用的 Python 库，但验证了"按位置确定性
  拼接"方向合理；`wechat_mp.py` 自己的移植来源注释是比宝玉 skills 更直接的参考。
  **测试**：`tests/test_derivative_wechat_mp.py` 新增 9 例（拼接位置四种场景 + 幂等性）；
  `tests/test_wechat_mp_publisher.py` 新增 7 例（`validate()` 插图存在性 2 例 + `publish()`
  CDN 上传/替换/dry_run 不触发/文件缺失报错/url 缺失报错/无插图正文行为不变 5 例）。
  **验证**：`pytest tests/test_derivative_wechat_mp.py tests/test_wechat_mp_publisher.py -q`
  36+26 全绿；全量 `pytest tests/ -q` 1491 通过 / 12 跳过，7 个失败与本次改动无关（`git stash`
  核实同样 7 个测试在改动前就失败——M13-1 已记录的既有已知问题：1 个 pyc 缓存误报 + 5 个
  `test_image_gen.py` 默认模型名不一致 + 1 个 flaky 并发锁计时测试）。真实发布链路（真实
  `app_id`/`app_secret` 调 CDN 上传接口）无法端到端验证，因为暂无真实凭据——留给 M13-2。

---

## 待评估事项（真实用户走查发现，2026-07-11）

⚠️ **DECISION NEEDED**：`pipeline/webui/calendar.py::bucket_week()` 按 `scheduled_at`
的 **UTC 日历日**分桶（`sched_dt.astimezone(timezone.utc).date()`），docstring 自称
"本地展示由调用方按需转换"——但这个说法不准确：分桶边界本身就是按 UTC 天算的，
不是"算完转显示"能补救的，前端拿到的 `d.date` 就已经是 UTC 那一天。对 `Asia/Shanghai`
(UTC+8) 用户，凌晨 00:00–08:00 排的贴会被分进"前一天"的日历格子里，用户直觉上
认为那是"今天"。

**当前实测影响**：抽查现有 3 条 `queued` publications，排期时间都在本地下午/晚上
（10:53 / 12:06 / 19:05），不落在 00:00-08:00 危险窗口，所以**当前数据下不会看到
可见错位**——这是本轮真人走查没有直接复现出该问题的原因，纯属现有排期时间点凑巧
没踩中边界，不代表问题不存在。

**未直接修的原因**：`bucket_week()` 是发布日历唯一的分桶实现，`pipeline/webui/api/publish.py`
的 `/publish/calendar` 路由和 `tests/test_webui_m4_4.py` 的 6 个纯函数用例都绑定
在"UTC 天"这个语义上；改成"按 `config.timezone` 本地天"分桶是行为变更，不是纯
展示层修复（CLAUDE.md 工作约定第 2 条：契约有问题先记录，不擅自改）。

**建议修法**（供下一轮拍板）：`bucket_week` 加一个 `tz: ZoneInfo` 参数（默认仍是
UTC 保持向后兼容），分桶时 `sched_dt.astimezone(tz).date()` 而不是强制转 UTC 再取
date；调用方（`publish.py` 路由）传 `cfg.timezone`（HARD_PARTS §8 已有 `timezone:
Asia/Shanghai` 配置项，可直接复用，不用新增字段）。

---

## 待评估事项（真实用户走查发现，2026-07-13）✅ 已修复

用户在内容详情页（`c_c66a5388`）点「图文衍生」卡片两个按钮连续踩到两个真实 bug，
并质疑"这个流程有没有真测过"——核实后确认：`tests/webui/test_api_derivative_images.py`
把 LLM 与图像生成调用全 mock 掉（"不真调 LLM"），导致下述超时类问题在自动化测试
里天生不可见，用户的怀疑成立。三处根因均已定位并修复：

- **Bug A（真 bug，配置错误）**：`frontend/src/api/client.ts` 的 axios 实例对所有
  请求统一 `timeout: 30_000`，但"衍生小红书"/"真实 AI 出图"命中的后端接口是同步
  长耗时调用——LLM 调用（`pipeline/creators/llm.py`）单次 `timeout_s=60`，×3 次重试
  退避 1/2/4s，最坏 ≈187s；图像生成（`pipeline/creators/image_gen.py::MiniMaxImageProvider`）
  单次 120s ×3 重试，最坏 ≈367s/张，`generate-images` 还要顺序出 cover+N 张。前端
  30s 超时必然先于后端触发，误报 `timeout of 30000ms exceeded`。**修复**：新增
  `GENERATION_TIMEOUT_MS = 10*60*1000`，仅在 `useDerivativeStore.run` /
  `useImageGenStore.run` 两处 axios 调用传 `{ timeout: GENERATION_TIMEOUT_MS }`
  覆盖，全局 30s 默认对其余快接口保持不变。
- **Bug B（覆盖缺口）**：`pipeline/doctor.py` 原 6 项检查只覆盖文本 LLM key
  （`AGNES_API_KEY`/`MINIMAX_API_KEY`/`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`），从未
  检查 `image_gen.py::MiniMaxImageProvider.from_env` 实际读取的
  `MINIMAX_IMAGE_API_KEY`/`MINIMAX_API_KEY`，导致 `/settings` 页面（直接转发
  `doctor.run_doctor()`）全绿却对"AI 出图会失败"毫无预警。**修复**：新增第 7 项
  `_check_image_key()`，仿 `_check_publish_enabled` 模式——**永远 `ok=True`**（AI
  出图是可选功能，`webui/app.py::main()` 明确不应因缺 key 拖垮整个服务），只在
  hint 里提示"⚠️ 未设置...（可选功能，不影响其余流程）"，不影响 doctor 整体 exit code。
- **Bug C（文案错误）**：`ContentDetail.vue` 与 `Step4ImageGen.vue` 的报错提示均写
  "配置 image provider key（MiniMax / Agnes-AI）"——但 Agnes-AI 只接入了文本 LLM
  （M9-1），`image_gen.setup_provider_from_env()` 只会构造 `MiniMaxImageProvider`，
  Agnes 对出图从来不是有效选项。用户照提示设 `AGNES_API_KEY` 只会徒劳。**修复**：
  两处文案改为准确指出 `MINIMAX_IMAGE_API_KEY` 或 `MINIMAX_API_KEY` 环境变量。

**验证**：`pytest tests/test_doctor.py -q` 29 通过（新增 2 例）；全量
`pytest tests/ -q` 1456 通过 / 12 跳过，7 个失败与本次改动无关（`docs/TASKS.md`
M13-1 已记录的既有已知失败：1 个 pyc 缓存误报 + 5 个 `test_image_gen.py` 默认模型名
不一致 + 1 个 flaky 并发锁计时测试）；`npm run build` 通过；本地重启 webui 后 curl
`/api/v1/settings` 确认 `image_key` 检查项已生效，grep 构建产物确认文案已修正。

**未做**（受限于本仓库无前端自动化测试框架，无 vitest/`*.spec.ts`）：Bug A 的
10 分钟超时未做端到端真实长耗时调用验证，仅走了代码走查 + 类型检查 + 手动点击
验证的既有验收基线（与 M10-13 一致）。

---

## 待评估事项（用户临场提需求，2026-07-13）✅ 已完成：Settings 页新增 API Key 配置能力

用户原话："完善一下设置页吧，现在这个完全没法用...看一下成熟的产品，像 yixiaoer
之类的是怎么设置这些 key 的"。核实后确认：`Settings.vue` 此前只有「doctor 体检
报告 + 脱敏 config JSON 只读展示」，没有任何输入框/保存按钮——用户即使看到 doctor
提示"缺 key"，也**无法在 UI 里把 key 真的填进去**，只能去 shell 手动 `export`，
这正是上面 2026-07-13 那次超时/出图 bug 的体验根因之一。

排查发现项目里凭据落地方式异构成三类：①全局服务 key（LLM/image-gen，此前**零
持久化**，只读活进程 env）②扫码平台（头条/小红书/抖音，已有完整一键登录 UI，
U7-7~U7-10）③API 凭据平台（X/wechat_mp，`secrets/x_<account>.json` 等文件，只有
静态文字指引，无表单）。本次**只做①**（两个真实 bug 的直接病因），②已解决不碰，
③明确列为后续任务（见下）避免范围蔓延。

**改动**：
- 新增 `pipeline/env_keys.py`：`LLM_ENV_VARS`/`IMAGE_ENV_VARS` 名单 +
  `load_env_secrets()`/`write_env_secret()`/`delete_env_secret()`/`mask()`，落盘
  到新文件 `secrets/env.json`（纯 JSON，已被整目录 `.gitignore` 覆盖）。
  `load_env_secrets()` 用 `os.environ.setdefault`——**真实进程 env 优先，不覆盖**，
  避免这层新机制在生产环境意外覆盖运维已设置的值。
- `pipeline/doctor.py` 的 `_LLM_ENV_VARS`/`_IMAGE_ENV_VARS` 改为从
  `pipeline.env_keys` import，消除三处重复定义（DRY）。
- `pipeline/webui/app.py::main()` 和 `pipeline/run.py::main()` 启动早期调用
  `load_env_secrets()`，覆盖 CLI 全部子命令 + webui。
- `pipeline/webui/api/settings.py` 新增 `GET/POST/DELETE /settings/keys`：
  白名单校验（非法 key 名 400 `unknown_key_name`）、空值校验（400 `empty_value`）、
  保存/清除后**立即热重载** `llm`/`image_gen` 两个 provider（`image_gen` 无
  Mock 兜底，try/except 包住不拖垮整个端点），响应体绝不含明文（`mask()`）。
- 前端：`useSettingsStore` 新增 `keyGroups`/`loadKeys()`/`saveKey()`/`clearKey()`；
  `Settings.vue` 在 Doctor 卡片上方新增「API Key 配置」卡片，按 group 渲染
  `a-input-password` + 保存/清除按钮，不回填已保存的明文。
- 测试：`tests/test_env_keys.py`（新文件，11 例）+
  `tests/webui/test_api_m10_5.py::TestSettingsKeysGet/Save/Delete`（新增 9 例，
  含"响应体绝不含明文"断言）。

**验证**：`pytest tests/test_env_keys.py tests/webui/test_api_m10_5.py
tests/test_doctor.py -q` 68 通过；全量 `pytest tests/ -q` 1475 通过 / 12 跳过，
7 个失败与本次改动无关（`git stash` 核实同样的 7 个测试在改动前的 main 分支上
本就失败——M13-1 已记录的既有已知问题：1 个 pyc 缓存误报 + 5 个
`test_image_gen.py` 默认模型名不一致 + 1 个 flaky 并发锁计时测试）；
`npx vue-tsc -b` + `npm run build` 通过；手动 smoke：启动 webui → 保存假
`MINIMAX_IMAGE_API_KEY` → 不重启进程、`GET /settings` 的 `image_key` 行立即从
红变绿 → `DELETE` 清除后立即变回红，`secrets/env.json` 落盘正确（往返 + 幂等）。

**明确未做（后续任务）**：
- ⬜ X（`secrets/x_<account>.json`）/ wechat_mp（`secrets/wechat_mp_<account>.json`）
  的凭据改为可在 Settings UI 里创建/编辑。受限于 `config_edit.py::add_account_to_config`
  当前"platform 块必须已在 config.yaml 声明"的限制，改造面比本次大，需要单独设计。
- ⬜ config.yaml 里非密钥类字段（budget/pillars/gate 阈值等）的可视化编辑——CLAUDE.md
  M8 明确锁定"只读展示，不做 UI 改"，本次不推翻这个历史决策。

---

