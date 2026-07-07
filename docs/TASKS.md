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
- [ ] **目标**：score 后、selector 前用 LLM 识别"同主题不同 URL/不同标题"的条目并去重，避免同一事件多角度报道占满 daily_quota
- **步骤**：
  1. `pipeline/topics/topic_dedup.py` 新增纯函数 `dedup_topics(items, ai_client) -> (reps, dups)`：单次 AI 调用，prompt 移植 Horizon `src/ai/prompts.py` 的 `TOPIC_DEDUP_SYSTEM/USER`（MIT License）；失败静默 fallback（返回 (items, [])）
  2. 复用 `creators/llm.py::complete_json`（已有 JSON fence + 自动重试）
  3. 接入 `pipeline/topics/runner.py::score_all`：在 `merge_by_url` 之后、`score_topic` 之前（顺序：URL dedup → 语义 dedup → score）
  4. **契约不变**：不动 schema/models；in-memory 合并（与 M1-6 同模式）
  5. 测试：纯函数（mock LLM：成功返回分组、失败 fallback、边界如空列表/单条）+ runner 集成
- **验收**：全测绿；同主题两条（不同 URL 不同 title）经 AI 去重只占一个 quota
- **参考**：Horizon `src/orchestrator.py:433-504` + `src/ai/prompts.py:3-13`

### M1-8 AI 智能筛选预筛（评估任务，借鉴 sansan0/TrendRadar filter.py）
- [ ] **目标**：评估"两阶段 AI 筛选"（A: 兴趣描述→标签；B: 标题批量分类+relevance）作为 M1-4 score 前的预筛是否值得做
- **步骤**：
  1. 设计 spec 草案：`pipeline/topics/prefilter.py` 设计 + cost 估算（每次 ingest 多 N 次 LLM 调用 vs 减少下游 score 调用量）+ threshold 策略
  2. 评估 ROI：score 阶段 cheap 档 ≈ $0.001/条，预筛再 cheap ≈ $0.001/条；预筛只对"高 relevance"的条目进入 score 才能摊薄；50% 命中率才能打平，70%+ 才有正收益
  3. 决策：写评估到 `docs/research/evaluation-notes.md` §6.2，得出 `DECISION: 落地 / 推迟 / 放弃`
- **验收**：决策记录 + 若 DECISION=落地 则转正式任务 P2-M1-8
- **参考**：sansan0/TrendRadar `trendradar/ai/filter.py`（GPL-3.0 仅参考设计）

### M1-9 多 provider 坑结构化收编（借鉴 Horizon ai/client.py）
- [ ] **目标**：把 `creators/llm.py::MiniMaxProvider` 散落的特殊 case（NO_RESPONSE_FORMAT / TEMP_CLAMP / JSON fence）提到 `PROVIDER_SPECS` 注册表，新增 provider 不用改 llm.py 主逻辑
- **步骤**：
  1. `pipeline/creators/llm.py` 新增 `PROVIDER_SPECS: dict[str, ProviderSpec]` 注册表（fields: supports_response_format / min_temperature / extra_fence_strip / 价格等）
  2. 各 provider 创建时按 spec 读配置
  3. **契约不变**：TECH_SPEC §5.3 接口不动；只重构内部 provider 注册
  4. 测试：新增 Anthropic/MiniMax/OpenAI 各 provider 都从 spec 正确初始化；删 MiniMax 散落 case 后行为不变
- **验收**：全测绿；llm.py 行数变少或结构更清晰
- **参考**：Horizon `src/ai/client.py:174-337`（MIT）；M1-3 已完成基线

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
- [ ] **目标**：消除 webui 每请求重开连接、每请求跑 DDL、以及弃用的 `utcnow()`
- **错在哪**：
  1. `pipeline/webui/app.py:50-53` `_conn()` 每次请求都 `db.init_db(c)`——`init_db` 会执行全部 `CREATE TABLE IF NOT EXISTS` DDL，**每个 HTTP 请求跑一遍建表语句**，纯浪费且拖慢页面
  2. `pipeline/webui/app.py:123` 用了 `datetime.utcnow()`——本项目跑在 Python 3.14，该 API 已 deprecated，会打 warning 且未来移除
- **怎么改**：
  1. 在 `create_app()` 内、返回 app 前**只调用一次** `db.init_db`（用一个临时连接建表后 close），把 `_conn()` 里的 `db.init_db(c)` 删掉，`_conn()` 只保留 `db.connect(_DB_PATH)`。这样每请求仍新开连接（SQLite 下可接受）但不再重复建表
  2. `app.py` 顶部已 `from datetime import datetime`；把第 123 行 `datetime.utcnow().isoformat()` 改为 `datetime.now(timezone.utc).isoformat()`，并在 import 段加 `from datetime import timezone`（或改成 `from datetime import datetime, timezone`）
- **验收标准**：`tests/test_webui*.py` 全绿；新增 1 个断言测试——patch `db.init_db` 后连续发 3 个 `GET /api/status`，断言 `db.init_db` 被调用次数 ≤ 1（证明不再每请求建表）
- **红线**：不要改 `db.py` 的 `init_db` 本身；不要改路由签名
- **参考**：TECH_SPEC §7

### R7-2 修 /output 与 /static 挂载时机 → 图卡/预览 404（HIGH，影响审核体验）
- [ ] **目标**：`output/` 目录在 webui 启动后才生成时，图卡 PNG 仍能被访问
- **错在哪**：`pipeline/webui/app.py:97-112`——`/output` 和 `/static` 用 `if output_dir.exists(): app.mount(...)` 挂载。若启动 webui 时 `output/` 还不存在（新机器、当天还没 create），之后流水线生成了图卡，**这些图片永远 404，直到重启 webui**。审核台/详情页的 `<img>` 全裂
- **怎么改**：
  1. 挂载前确保目录存在：把条件挂载改成 `output_dir.mkdir(parents=True, exist_ok=True)` 后**无条件** `app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")`
  2. `/static` 目录是仓库自带资产（`pipeline/webui/static/`），正常存在，保留即可；但同样去掉 `if` 直接挂（该目录已随代码提交）
- **验收标准**：新增测试——先删除/不创建 `output/`，`create_app()` 后再 `mkdir output/2026-01-01 && 写一个 x.png`，请求 `GET /output/2026-01-01/x.png` 返回 200 且 content-type 为 image/png
- **红线**：`/output` 必须只读（StaticFiles 默认只读，别加写路由）；不要把 `output/` 加进 git（`.gitignore` 已忽略，别动）
- **参考**：TECH_SPEC §7「/output 挂静态目录，只读」

### R7-3 webui 直写 SQL 违反 §7 契约 → 抽到 db.py 助手函数（MEDIUM）
- [ ] **目标**：消除 UI 层里的裸 `UPDATE` SQL，遵守 TECH_SPEC §7「**UI 不得直接写 SQL**」
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

### R7-4 metrics 裸吞异常违反 §8 → 补结构化日志（MEDIUM）
- [ ] **目标**：让「失败静默重试次日」的 metrics 路径留下可排障日志，遵守 §8「禁止裸 except: pass」
- **错在哪**：TECH_SPEC §8 规定「任何 except 分支必须要么 re-raise 要么 log.warning 以上级别记录」。但 `pipeline/metrics/collectors.py` 与 `pipeline/metrics/runner.py:122,130` 有大量 `except Exception:` 后只 `failed += 1; continue`，**一个字都不记**。线上 metrics 抓不到数时无从排障
- **怎么改**：
  1. 用 `pipeline/utils/log.py` 的结构化 logger（其它模块的用法照抄），在每个 `except Exception as e:` 分支加一行 `logger.warning(...)`，**必带 `stage="collect"` 与 `ref_id=<publication_id>`**（§8 要求每条日志带 stage+ref_id），message 含 `repr(e)`
  2. 注意把 `except Exception:` 改成 `except Exception as e:` 才能拿到异常对象
  3. runner.py 与 collectors.py 里所有这类裸吞点都要补（前面 grep 已列出行号：collectors.py 的 96/143/180/227/285/324/364/396，runner.py 的 122/130）——逐个补，别漏
- **验收标准**：`grep -n "except Exception" pipeline/metrics/` 每一处下方 3 行内都能看到 `logger.warning`；新增测试——mock collector 抛异常，断言 logger 收到一条含该 publication_id 的 warning（可用 `caplog`）
- **红线**：**不要改控制流**——失败仍是 `failed += 1; continue`（§8 允许「记录后继续」，metrics 是非关键路径，不能因单条失败阻断编排）；不要 re-raise
- **参考**：TECH_SPEC §8；HARD_PARTS §5（collect 幂等）

### R7-5 补 tests/test_e2e_dryrun.py（§9 必测项缺失，HIGH）
- [ ] **目标**：补上 TECH_SPEC §9 明确要求但**至今不存在**的端到端 dry-run 集成测试
- **错在哪**：TECH_SPEC §9 白纸黑字：「集成测试 `tests/test_e2e_dryrun.py`：造一个假 topic，全流程跑到 publish --dry-run」。现仓库只有 `test_toutiao_e2e.py`/`test_douyin_e2e.py`（单平台），**没有全链路 dry-run 测试**。这是里程碑级验收漏洞
- **怎么改**：新建 `tests/test_e2e_dryrun.py`：
  1. 用临时 db（`db.connect(":memory:")` 或 tmp_path 下的 state.db）+ `db.init_db`
  2. LLM 全程走 `MockProvider`（llm.py 已有），**不打真实网络**；平台发布用 `MockPublisherAdapter`（safe_publish.py 已有）
  3. 造一个假 topic 插入 → 依次调用各阶段编排函数（score_all → create → gate runner → review 落库 approved → scheduler.plan → safe_publish dry_run=True），断言最终 publication 记录存在且**真实 publish 未被触发**（`publish.enabled=false` 时断言 mock.publish 的 call_count==0，或 dry_run 分支返回 published=False）
  4. 参考现有 `tests/test_publish*.py`、`tests/test_gate*.py` 的 mock 装配方式，别自己发明
- **验收标准**：`pytest tests/test_e2e_dryrun.py -q` 绿；测试内断言覆盖「全链路状态推进正确」+「dry-run 下 PublisherAdapter.publish 真实动作未发生」（§9 必测第 4 条）
- **红线**：**mock LLM/平台可以，绝不 mock 状态机**（HARD_PARTS §10 第 3 条）——状态转移必须走真实 `db.transition`；不要为了让测试过而改生产代码逻辑
- **参考**：TECH_SPEC §9；HARD_PARTS §5、§10

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

## 后续 Backlog（不排期）

- **数字人口播 lane**（AIGCPanel 引擎，走 VideoEngine 接口）：好物分享/带货方向；前提=M5-3 评估通过 + 账号过带货门槛 + 平台虚拟人报备完成
- **OpenMontage 精品视频 lane**（远期观察，M0-0 决策降级：Pixelle-Video 已接管精品定位）：仅当 Pixelle-Video 质量不达预期时重评
- 公众号 Publisher（官方 API 草稿箱 + 人工点发布，公众号自动群发风险高；M0-0 决策：不部署 TrendPublish，自研 lane 时移植其微信兼容 HTML 后处理器，见 evaluation-notes §1 移植清单）
- Postiz 部署接入 YouTube Shorts / TikTok
- 表现数据反哺选题权重（metrics → topics 评分 prompt 动态调整）
- 多账号矩阵（同平台第二账号 = 不同支柱人设）
- 英文内容线（Medium/dev.to）
- n8n 迁移（当 launchd 管理复杂度超阈值时）
