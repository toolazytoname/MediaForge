# M0-0 同类项目深度评估 — 决策记录

> 评估日期：2026-07-05 | 方式：4 个并行研究 agent，全部 `git clone --depth 1` 深读源码 + GitHub API 核实活跃度
> 本文件是 M0-0 的验收产物。每项含 `DECISION: <采用/参考/放弃> 因为 <理由>`。
> 完整评估报告原文见本文各节摘要；克隆快照在 `/tmp/eval-*`（临时，重要代码片段移植时需重新 clone 并 pin commit）。

---

## 总览

| 候选 | 领域 | DECISION | 一句话理由 |
|------|------|----------|-----------|
| TrendPublish | 公众号全链路流水线 | **参考**（不部署，移植三块设计） | 与本系统同构 80%，整体部署 = 双状态机；但门禁/排版/防幻觉三块设计社区最佳 |
| XiaohongshuSkills | 小红书发布 | **采用**（M4-3 集成，subprocess 封装） | 能力全集（图文/真话题/视频/定时）+ 活维护 + CDP 反检测强一代 |
| xhs-toolkit | 小红书发布 | **放弃** | 作者官宣停更，选择器已随 2026 年初改版失效，修复 PR 无人合并 |
| AiToEarn | 国内多平台发布 | **放弃**（整体方案）/ **参考**（API 设计 + Electron 逆向资料） | 自部署下国内平台无法无人值守：小红书/视频号靠闭源插件、抖音需手机确认、头条不支持 |
| Pixelle-Video（用户补充） | 视频产出 | **采用**（VideoEngine 第二引擎，AI 生成类内容优先） | 异步 API 与 VideoEngine 契约零适配，AI 生成素材视觉上限远超 MPT 库存混剪 |
| baoyu-skills（用户补充） | 视觉产出/排版/发布 | **参考**（skills 桥 + 唯一抽出 `baoyu-image-gen` 作 §5.4 配图 backend 扩展） | 23.1k⭐ 活跃度高，但 80% skill 依赖 Claude agent 编排，违反 §5.4 无人值守；只有 image-gen 是纯 CLI 可 subprocess |

连带结论：
- **OpenMontage 从「第二引擎候选」降级为远期观察**——Pixelle-Video 在生产可用性（Docker + HTTP API + 进度回报）上全面胜出 agent 驱动模式。
- **国内发布回归自写 Playwright 主线**（TECH_SPEC §5.2 契约不变），AiToEarn 的 Electron 遗留代码（MIT，约 6800 行）作为平台接口摸底资料。

---

## 1. TrendPublish（liyown/ai-trend-publish，3.0k⭐，MIT，Deno/TS）

**DECISION: 参考（借鉴自研为主 + 局部代码级移植），不整体部署** 因为：架构与本系统同构 80%（同为「选题→创作→审稿→dry-run→发布」状态机），整体部署得到「双状态机、双配置、双人审入口」；其选题层与创作层强耦合，「只用公众号 lane」必须 fork（Deno/TS 与我们 Python 异构）；单人维护、脉冲式活跃，不宜做运行时依赖。

**评估要点**：
- 活跃度：最近 push 2026-06-14，仅 1 个 open issue，53 个单测，v2.0.7。单人项目（liyown）。
- 流水线：`workflow.ts` 1178 行状态机，每步产物落 artifact store 可复盘，有「编辑记忆」（人工反馈回流选题 prompt）。
- 质量审稿：7 维 JSON 评分 + normalize 交叉推导（低分无 issue 时合成 issue）+ 修订白名单（禁碰事实类/blocker 问题）+ 修订后强制复审 + fact 高危一票否决。修订采纳规则（复核修正，见文末复核记录）：action 等级（block<revise<dry-run-only<publish）**提升即采纳（哪怕分数降）**，等级持平才要求分数不降，新增 blocker/发布权降级则回滚——移植时以 `workflow.ts:1112-1126` 真实逻辑为准，勿按「评分单调不降」简化实现；另注意白名单有 `autoFixable` 旁路（先于类别判断）。**但审稿人=作者（同一 LLM 配置），无锚点校准、无强制批判配额**——「AI 给 AI 打高分」防线未闭合，恰好印证 HARD_PARTS §3 的设计必要性。
- 可集成性：有 HTTP API（POST /api/runs 支持 dryRun）+ CLI；dry-run 默认开，真发布也只到微信草稿箱。但无「从外部注入已选题内容」的入口。

**移植清单**（影响 M2-1/M2-2/后续公众号 lane）：
1. **门禁修订协议**（→ M2-2）：修订只许碰安全问题白名单（title/tone/structure/html），禁碰事实类；修订后强制复审，采纳规则按 `workflow.ts:1112-1126` 真实逻辑（action 等级提升即采纳、持平才比分数、新增 blocker/发布权降级回滚）。在此之上补我们的锚点校准 + 异构评分模型。
2. **微信兼容 HTML 后处理器**（→ 公众号 lane，Backlog）：`html-post-processor.ts` 231 行纯函数清洗（div→section、剥 class/script、外链转脚注）+ validateWeixinHtml，社区最完整的公众号排版知识沉淀。
3. **防幻觉 prompt 条款**（→ M2-1）：「商业状态/定价/API 开放性/参数规格，只有来源明确写出才能确定表述」+ 逐句实体支持度过滤（`dropUnsupportedEntityParagraphs` 模式），抄进 canonical 创作 prompt。
4. 其 `src/experiments/article-quality/` 思路可参考——但复核发现它 A/B 对比的是「baseline vs 加 evidence-pack 证据检索」对成稿质量的影响（审稿评分只是度量而非被测对象），用于 M2-2 门禁校准时需自行改造。

---

## 2. 小红书发布：XiaohongshuSkills vs xhs-toolkit vs 自写

### xhs-toolkit（aki66938，1.3k⭐）

**DECISION: 放弃** 因为：README 首行官宣停更（「后续也不再计划维护」）；正文编辑器选择器停留在 Quill，小红书 2026 年初已改版 TipTap/ProseMirror，社区修复 PR 悬空未合并；26 个 open issue 大量无人回应；Selenium+ChromeDriver 还有版本匹配包袱；反检测几乎为零。集成死项目 = 变相自写。

### XiaohongshuSkills（white0dew，3.1k⭐）

**DECISION: 采用（M4-3 集成为发布引擎）** 因为：三候选中唯一「能力全集 + 活维护」——图文多图、真话题标签（下拉候选选中）、视频、定时发布（`--post-time`）全覆盖；已适配 2026 年 2-3 月 DOM 改版，选择器集中在单文件 `SELECTORS` dict（正文编辑器三级 fallback：tiptap.ProseMirror → ProseMirror contenteditable → ql-editor，其余选择器多为两级）；维护响应尚可（复核修正：最快次日关 issue，但也有 issue 拖 1-3 个月批量关，非普遍「以天计」；最后 commit 2026-05-21，距今约 6 周）；裸 CDP 驱动真 Chrome（无 webdriver 指纹），话题标签逐字输入 45-95ms 随机延迟（复核注：正文是整段 DOM 一次性写入、非逐字），反检测仍比 Selenium 强一代；CLI + 明确退出码（1/2/3）+ `PUBLISH_STATUS:` 状态行，subprocess 封装进 `PublisherAdapter` 顺畅；Chrome profile 隔离天然支持多账号；附赠创作者数据看板 CSV 抓取，可复用到 M6-1 metrics 回流。

**集成护栏（写入 M4-3）**：
1. **mac 冒烟测试先行**——该项目 Windows 优先（chrome_launcher 已有 darwin 路径，profile 路径需验证），跑不通降级 Plan B；
2. **vendor 固定 commit**（2026-05-21 `fix(_click_tab)` 之后的 main），不追 HEAD；
3. **dry_run 语义分层**：契约的 `dry_run=True` 在 adapter 层校验 bundle 即返回、完全不碰浏览器；其 `--preview`（填充表单不点发布）仅作 M4 上线前人工验证档位；
4. **频控归编排层**：它无内建限流，社区有封号案例（issue #24「昨天用完，今天喜提封号」，2026-05）——日限额/间隔全按 HARD_PARTS §2 §6 在我们侧执行。

### social-auto-upload（13k⭐）基线

新版 `xiaohongshu_uploader` 用 patchright（Playwright 反检测 fork）+ storage_state + success-URL 判定，骨架干净，但以视频为中心、图文支持不明确。**降级为 Plan B 参考**（HARD_PARTS §7 表更新）。

---

## 3. AiToEarn（yikart，23.1k⭐，MIT，NestJS/Next.js）

**DECISION: 放弃整体方案；参考其 API 设计与 Electron 遗留代码** 因为：**自部署下国内平台无法无人值守**——
- 小红书/视频号：`authType: Plugin`，发布由**闭源 Chrome 插件**在用户已登录的浏览器里完成，服务端 `publish()` 只接收已发布的 workLink 回填（插件源码不在任何公开仓库）；
- 抖音：官方 share API + `UserHandoff`——**用户必须用抖音 App 扫码确认**才完成发布，且授权依赖其小程序资质；
- 头条：**不支持**（issue #550 未实现）；公众号 `coming_soon`；
- Relay 模式（低门槛授权的唯一路径）把授权 token、发布请求、媒体文件**整体代理到 aitoearn.ai 官方云**——自部署实例沦为其 SaaS 的前端壳，数据自主性消失；不配 Relay 则需自己申请十几个平台的开发者资质；
- docker-compose 默认 `pull_policy: always` 拉其 Docker Hub latest 镜像（非源码构建），有供应链/版本漂移风险。

**可复用**：
1. **发布 API 数据模型**（→ M4-1 参考）：`createPublishFlow`（多平台 items + 平台级 overrides + publishAt + flowId 幂等 + taskId 回联外部系统 + status/linkStatus/errorMsg 回传）与我们的状态机高度同构；
2. **Electron 遗留代码**（MIT，`project/aitoearn-electron/electron/plat/`，xhs 1704 行 / douyin 2610 行 / 视频号 1531 行 / 快手 769 行）：cookie 判活字段、creator 后台上传/发布私有接口、快手签名算法——自写 Playwright 发布器时的**接口摸底资料**；
3. 海外扩展期（≥3 平台）可重评「部分海外平台走它」，其 OAuth2 + 官方 API 部分成色最高——但 Postiz 仍是 HARD_PARTS §7 首选。

---

## 4. Pixelle-Video（ATH-MaaS/Pixelle-Video，24.1k⭐，Apache-2.0，用户补充候选）

**DECISION: 采用（VideoEngine 并列第二引擎，AI 生成类内容优先引擎；MPT 保持默认兜底）** 因为：
- **接口天然契合**：FastAPI 异步任务 API（`POST /api/video/generate/async` → task_id → `GET /api/tasks/{id}` 轮询 progress → 文件下载）与 TECH_SPEC §5.6 的 submit/poll/fetch 三段式几乎一一对应；
- **文案主权可保**：`mode=fixed` 直接按段落拆分我们的口播稿为分镜，完全跳过它的 LLM 写稿——匹配「创作管道产出脚本，引擎只负责渲染」的分工；
- **视觉上限高于 MPT**：素材全部 AI 生成（Flux/SDXL/Qwen-Image 生图，WAN/Kling/Seedance 生视频）+ HTML 模板渲染帧（Playwright 截图，30+ 模板），风格可控、观感精致，且规避平台「库存素材搬运」判定；TTS 支持 Edge-TTS（免费）与 Index-TTS 声音克隆；
- **Mac + 云 API 即可全链路**：无 GPU 硬需求，生图可走 DashScope/RunningHub 按量付费；Docker Compose 官方支持；
- 背景：阿里 AIDC-AI 出品（已迁移 ATH-MaaS org），2025-11 建仓 7 个月 24k stars，2026-06 仍活跃。

**不替代 MPT 为唯一默认**的原因：每条视频有真实生图成本（约 ¥0.5–5/条，视频模板更高）vs MPT 库存素材近乎免费；项目仅 7 个月大 + 刚经历 org 迁移；**任务状态存内存**（服务重启丢任务）。分工 = 内容类型互补：知识科普/读书/情感类走 Pixelle-Video，时效资讯量产走 MPT。

**适配要点（→ M5 新增任务，含复核补充）**：
- `VideoRequest` 映射：script→text（mode=fixed）、aspect→frame_template 尺寸目录（`templates/1080x1920|1920x1080|1080x1080/`，`frame_template` 必填）、style→frame_template + prompt_prefix；`duration_s` 无法硬指定（API 请求无 duration 字段，实际时长=各帧 TTS 音频累加），适配层按语速预估校验；
- 轮询 404（服务重启丢任务；**且完成任务默认 24h 后被清理**，需及时 fetch）按 failed 处理 + 重提交；轮询**以 status 为准**——`progress` 字段对 video 任务未接线回调，大概率恒为 null，勿依赖百分比；
- API 未暴露 split_mode，但默认即 paragraph（按 `\n\n` 双换行拆段）——**口播稿分段协议 = 双换行分镜边界**，在我们侧预处理为段落即可精确控制分镜；
- mode=fixed 跳过其 LLM 写稿，但**非零 LLM**：未传 title 时仍 LLM 生成标题（应显式传 title），image/video 类模板仍用其 LLM 生成配图 prompt；
- 生图供应商 API Key 单列 config，走 `secrets/`；每条视频记录生成成本。

**连带决策**：OpenMontage 降级为远期观察（M5-3 评估范围相应缩减为 AIGCPanel 数字人 + 可选看一眼 OpenMontage），Pixelle-Video 接管「精品/差异化视觉」定位。

---

## 5. baoyu-skills（JimLiu/baoyu-skills，23.1k⭐，MIT，TS/bun，用户补充）

**DECISION: 选项 (a) + 有限 (b) — 保留 §5.5 skills 桥；唯一抽出来集成的是 `baoyu-image-gen` 作为 §5.4 配图 backend 扩展** 因为：核心流水线「不依赖 Claude Code CLI」原则（§5.4）排除多数 skill；但 `baoyu-image-gen` 是纯 CLI、subprocess 友好、API key 计费（不占 Claude 额度）、覆盖 12 个 provider（含国内 DashScope/Z.AI/Jimeng/Seedream）——直接扩展 §5.4 的图像生成 provider 集合；其余 skill 保留为 §5.5 可选桥。（复核补充：`baoyu-compress-image`、`baoyu-youtube-transcript` 等也是可独立跑的 CLI，若流水线后续需要压图/取字幕可同法 subprocess 化，无需走桥。）

**评估要点**：
- 活跃度：23.1k⭐/2.6k forks，最近 push 2026-07-04（昨天），每天 ≥1 commit，MIT，21 个 skill（HEAD），中英 README，活跃度**远超** HARD_PARTS §7 其他 Plan B 候选。
- 架构范式：多数 skill = `SKILL.md`（agent 编排指南）+ `scripts/*.ts`（bun CLI 子命令）；21 个中有 5 个是纯 prompt 型（无 scripts/：article-illustrator/cover-image/infographic/wechat-summary/xhs-images）。可独立 subprocess 化的纯 CLI 有 image-gen / markdown-to-html / compress-image / youtube-transcript 等，其它多依赖 Claude 会话内做风格判断与 AskUserQuestion 确认流。
- 视觉产出（xhs-images/cover-image/infographic/article-illustrator/comic/slide-deck）：价值在「风格系统 + prompt 模板 + 风格 layout 决策树」（如 9 styles × 6 layouts、5 维封面、21 layouts × 22 styles 信息图），**但调用方式是 Claude agent 内 LLM 决策**，subprocess 拿不到最终交付。**正确集成方式 = 风格模板作为参考知识手工翻译进 MediaForge 的 `templates/`，不直接调 skill**。
- 图像生成 backend `baoyu-image-gen`：v2.1.0（本机与 HEAD 字节级一致），约 45KB 主 CLI 支持 `--prompt --image --provider --model --json` 出口、`--batchfile --jobs N` 批模式，12 provider（OpenAI/Azure/Google/OpenRouter/DashScope/Z.AI/Jimeng/Seedream/MiniMax/Replicate/codex-cli/Agnes）。**subprocess.run 直接调，无需 Claude 中转**。复核确认关键细节：`--json` 出口返回 **`savedImage` 文件路径（非 base64）**；配置加载顺序 CLI args > EXTEND.md > env > `.baoyu-skills/.env`——subprocess 集成**不需要 EXTEND.md**，注入对应 provider 的环境变量 API key 即可；本机无 bun 时 `npx -y bun .../scripts/main.ts --help` 已实测可跑。
- 发布类 `baoyu-post-to-wechat`（CDP 路线）：与 §5.4「官方 API 草稿箱」路线冲突，与「无人值守」原则冲突，**不引入**；HEAD 新增 `wechat-http.ts`/`wechat-remote-publish.ts`/`wechat-api.ts`（向官方 API 演进方向），观察其未来是否演化为官方 API 路径。
- 排版 `baoyu-markdown-to-html`（v1.57.0 vs HEAD v1.117.3，**落后 60 个 minor**）：4 主题 + 13 色板 + mermaid/plantuml/footnotes/alerts/外链转文末引用——**功能弱于 TrendPublish html-post-processor**（主题少、中文细节差），不替换。
- 翻译 `baoyu-translate`（落后 58 个 minor）：Claude 编排型，subprocess 只能拿 chunk，翻译靠 Claude agent——**不集成**，翻译走 §5.3 creators/llm.py。

### 本机版本 vs GitHub HEAD（复核修正 2026-07-05）

> ⚠️ 原评估的「本机落后 HEAD」对照表基于旧快照，**已失效**：本机 `~/.agents/skills/` 于 2026-07-05 09:59 全量更新，复核实测所有 baoyu skill 均与 HEAD 同步（image-gen v2.1.0 字节级一致；markdown-to-html/translate 已到 v1.117.3；xhs-images v2.0.1；post-to-wechat v1.118.2 且 wechat-http.ts 等新文件本机已有）。原表删除，任何基于版本落差的论证作废。M2-4.5 集成时仍按惯例复核当时 HEAD 的 CLI 签名。

### 移植/集成清单

1. **`baoyu-image-gen` → §5.4 image_gen.provider 追加 `baoyu` 选项**（→ M2-4 增子任务 M2-4.5，可选不阻塞主验收）：`pipeline/creators/render.py` 增加 subprocess 调 `npx -y bun ~/.agents/skills/baoyu-image-gen/scripts/main.ts --prompt <text> --image <out.png> --provider X --model Y --json` 分支（复核实测命令可行；`--json` 返回 `{"savedImage": "<路径>", ...}`，batch 模式返回 `{mode:"batch", results[]}` 且失败时 exitCode=1；API key 走环境变量注入）。配置 `image_gen.provider: baoyu` 时启用；provider 选 none/baoyu 默认行为与 §5.4 完全一致——**纯扩展，不破坏契约**。
2. **风格系统（xhs-images / cover-image / infographic）作为参考知识**：style × layout 决策树、prompt 模板设计思路（2-5 维度枚举 + 配色矩阵 + 参考图系统）可手工翻译进 MediaForge 的 `templates/` 资产——但**不直接调 skill**（需 Claude agent），不符合 §5.4 无人值守原则。
3. **观察 `baoyu-post-to-weibo`**（HEAD 有、本机无）：未来若国内发布要做微博，对比评估。

---

## 对 TASKS.md 的影响（已同步修改）

- **M2-1**：步骤补充「移植 TrendPublish 防幻觉 prompt 条款」
- **M2-2**：步骤补充「参考 TrendPublish 修订协议（白名单修订+复审单调不降）」
- **M4-3**：小红书改为「集成 XiaohongshuSkills（subprocess 封装，四条护栏）」；头条维持自写 Playwright（参考 AiToEarn electron 遗留代码 + social-auto-upload）
- **M5-1**：维持 MPT 默认引擎不变
- **M5-3** → 改为「Pixelle-Video 第二引擎接入」（原 OpenMontage/AIGCPanel 评估内容缩减进 Backlog/子项）
- **M2-4** → 增子任务 M2-4.5（可选）：`baoyu-image-gen` 集成进 `pipeline/creators/render.py` 作为 `image_gen.provider == "baoyu"` 分支（详见 §5 评估，subprocess + JSON 出口，11 provider，不阻塞 M2-4 主验收）
- **TECH_SPEC §5.4** → `image_gen.provider` 选项由 `none|gemini|openai` 扩为 `none|gemini|openai|baoyu`（纯扩展不破坏契约）
- **TECH_SPEC §5.5** → 补一句：skills 桥主要承载 Claude 编排型 skill（xhs-images/cover-image/infographic 等），`baoyu-image-gen` 不走桥，由 §5.4 直接 subprocess 调
- **HARD_PARTS §7 备选表**：国内发布主选 = 自写 Playwright + XiaohongshuSkills（小红书）；AiToEarn 移出候选

---

## 复核记录（2026-07-05，Fable 5 二次审查）

> 方式：5 个并行核查 agent，对本文全部事实性声明逐条对照 `/tmp/eval-*` 克隆快照源码 + GitHub API 重验。

**总体结论：5 项 DECISION 全部维持不变。** 原评估事实基础扎实（行数、函数名、API 端点、issue 编号级别的声明绝大多数逐字命中），无一处错误动摇决策方向。已修正的实质性偏差（正文已就地更新）：

1. **TrendPublish 修订采纳规则**：原文「评分单调不降才采纳」是简化——真实逻辑是 action 等级提升即采纳（哪怕分数降）、持平才比分数；白名单有 `autoFixable` 旁路。M2-2 移植时以 `workflow.ts:1112-1126` 为准。`src/experiments/article-quality/` 实为「证据检索机制 A/B」而非「审稿机制 A/B」。
2. **XiaohongshuSkills 三处修饰性夸大**：issue 响应非普遍「以天计」（也有拖 1-3 个月批量关的）；45-95ms 逐字输入仅限话题标签（正文为整段 DOM 写入）；三级 fallback 仅正文编辑器。均不动摇「采用」——对比项 xhs-toolkit 停更 + Quill 选择器失效 + 修复 PR 悬空全部属实。另：mac 有历史 issue #8「mac 版发不出去」（已关），M4-3 的「mac 冒烟先行」护栏必要性再获确认。
3. **AiToEarn**：全部核心声明属实（含 electron 四文件行数逐一吻合）。措辞微调：抖音「扫码」严格属授权环节，发布确认是用户在 App 内打开 handoff schema；Relay serverUrl 理论可自建但无开源 relay 服务端。
4. **Pixelle-Video 适配层新增三个已证实约束**（已并入 §4 适配要点）：完成任务 24h 后清理需及时 fetch；`progress` 字段对 video 任务未接线、轮询以 status 为准；mode=fixed 非零 LLM（应显式传 title）。分段协议确认 = `\n\n` 双换行。
5. **baoyu-skills**：provider 实为 12 家（漏了 Agnes）；「本机落后 HEAD」版本对照表因本机 2026-07-05 晨已全量同步而作废；「仅 3 个纯 CLI」低估（compress-image/youtube-transcript 等亦可 subprocess 化）；`--json` 出口确认返回 `savedImage` 文件路径、API key 走 env 无需 EXTEND.md、`npx -y bun` 实测可跑。M2-4.5 集成命令可信度升级为「已实测」。

---

## 6 选题层强化项评估

> 本节聚焦「选题 raw → score」这条链路上是否值得再加一道闸。M1-6/M1-7 是已落地的去重项；M1-8 是本次评估，借鉴 sansan0/TrendRadar `trendradar/ai/filter.py` 的「cheap LLM 预筛」设计思想（GPL-3.0 仅参考设计，不复用源码）。

### 6.1 M1-6 跨源 URL 去重（已落地，commit 188c311）

`pipeline/topics/url_dedup.py::merge_by_url(items)` 纯函数，URL normalize 后将多源转载的代表条合并到 score 队列。

**效果**：`ScoreRunResult.duplicates_merged` 新字段 + cmd_score 打印 + tests 22（url_dedup 18 + runner 集成 4），全量 885 pass。**契约零变更**（TECH_SPEC §3 models / SQL schema / Adapter 签名未动）。

**已知限制**（留 TODO，不在本任务修）：in-memory 合并不写回 DB，下次 cron 重跑仍会再合并一次（少量 LLM 浪费）；彻底解决需 schema 加 `merged_into_topic_id` 字段（动契约，留 TODO）。详见 M1-6 完成记录与 `runner.py:8-9` 注释。

### 6.2 M1-7 AI 语义主题去重（已落地，commit 2b4df08）

借鉴 Horizon `src/orchestrator.py:433-504` + `src/ai/prompts.py:3-13`（MIT License 已合法移植）的 `TOPIC_DEDUP_SYSTEM/USER` prompt，单次 LLM 调用按"同事件聚类"返回分组代表条。

**关键决策**：
- 顺序 = URL dedup → 语义 dedup → score（与 M1-6 同模式，in-memory 不动 DB）
- 失败静默 fallback（AI 抛异常返回 `(items, [])`，不阻塞主流程）
- keyword-only `ai_client` 注入（测试用 mock，集成用 `complete_json`）

**效果**：`pipeline/topics/topic_dedup.py` 243 行 + tests 25（纯函数 20 + 集成 5），全量 940 绿/12 skip，verify PASS 10/10。

### 6.3 M1-8 两阶段 AI 预筛评估（本节）

#### 评估问题
score 前是否值得插一层 cheap 档 LLM 预筛（relevance 0-10 + tags）？借鉴 TrendRadar `trendradar/ai/filter.py` 的「先打分后入主流程」设计（GPL-3.0 不复制源码，仅复用 prompt 结构思想）。

#### 数据基线（实测自本仓库）

读自 `pipeline/creators/llm.py`、`pipeline/topics/scorer.py`、`pipeline/config.py`、`config.example.yaml`：

| 项 | 值 | 来源 |
|---|---|---|
| cheap 档模型（默认） | `claude-haiku-4-5-20251001` | `llm.py:38-42` + `config.example.yaml` `llm.tiers` |
| cheap 档价格（USD/Mtok） | input 0.80 / output 4.00 | `MODEL_PRICES` |
| score prompt input 字符 | 552（中文字符为主） | `_SCORE_PROMPT` 单条实际渲染（含 1 条示例 topic + 2 个 Pillar） |
| score prompt est tokens | ~368（中文 1.5 字符/token 启发式） | 换算 |
| score prompt output tokens | ~60（`{"pillar","score","reason"}` JSON） | `_SCORE_PROMPT` 与 JsonResponseFormat |
| score `max_tokens` | 512（远高于实际输出） | `scorer.py:165` |
| **score 单次成本** | **$0.000534** | 计算 |
| prefilter prompt est tokens | ~167（更短：只 title+summary+输出 JSON） | 估算 |
| prefilter output tokens | ~40 | 估算 |
| **prefilter 单条成本** | **$0.000294** | 计算 |
| prefilter batch (B=10) per-item cost | $0.000181（摊薄 system prompt） | 计算 |
| `daily_quota` | 5（`topics.daily_quota`） | `config.example.yaml:35` |
| `min_score` | 6.0 | `config.example.yaml:36` |
| `BUDGET_LIMIT_USD` | 80 | `llm.py:45` + config `budget.monthly_usd` |
| 典型 N（每日 raw topic） | 50（hnrss 30 + github_trending 25 = 55，但去重后≈50；dailyhot 启用后 +20） | `config.example.yaml` `sources[*].max_items` |
| 月度 score 调用估算 | N × quota 占满可能做 min(quota, raw) = 实际 score 调用 = N（每 raw 都评） ≈ 1500/月（30 天 × 50） | 由 runner 流程推 |

#### 方案设计

**A 逐条**：`relevance_one(item) -> prefilter_score`，每条 cheap LLM 独立调；阈值 `THRESHOLD`（建议初值 5/10，对齐 `min_score`）后没过线 → 直接标 `rejected:low_relevance`。

**B 批处理 (B=10)**：单次 prompt 喂 N 条，输出 `[{idx, relevance, tags}, ...]`；按 system prompt 摊薄 60% 成本。批大小 10 是经验值（M1-7 dedup 经验：单 prompt 过 10 条 JSON 仍稳定）。

**两方案共同**：
- 复用 `complete_json()`（已有 JSON fence + 1 次 retry）
- 复用 `MODEL_PRICES` + 月度预算硬顶（HARD_PARTS §4）
- 失败静默 fallback 为「全部放过，让下游 score 兜底」（不能因预筛坏了全卡死）

#### ROI 数学（基于实测单价）

**公式**（N=每日 raw、H=命中率=通过预筛能进 score 的占比）：
- baseline cost = N × score_cost
- A cost = N × pre_cost + N × H × score_cost
- B cost = (N/B) × (B × pre_batch_per_item_cost) + N × H × score_cost

**单条视角：持平点（prefilter 与 baseline 同成本）**
- A: H ≤ 1 − pre_cost/score_cost = 1 − 0.000294/0.000534 = **45.1%**
- B: H ≤ 1 − 0.000181/0.000534 = **66.2%**

含义：A 在 H < 45% 才省钱；B 在 H < 66% 才省钱。**H 越高（预筛漏过越多），越贵**。

**典型日 @ N=50（baseline $0.0267）**

| 命中率 H | A 逐条 | 偏差 | B 批 (B=10) | 偏差 |
|---|---|---|---|---|
| 30% | $0.0227 | **−15%** | $0.0171 | **−36%** |
| 50% | $0.0280 | +5% | $0.0224 | −16% |
| 70% | $0.0334 | +25% | $0.0277 | +4% |
| 90% | $0.0387 | +45% | $0.0331 | +24% |

**N=100 大日**（baseline $0.0534）：偏差比例同上，金额翻倍。

**关键洞察**：
1. **绝对金额很小**（每天 $0.02–0.05）。即使预筛 +45% 也是 $0.04/day，月度增量 $1.2，远不及 `BUDGET_LIMIT_USD=80` 1.5%。
2. **真正风险不在成本在质量**：预筛与 score 评分 prompt 是两个不同模型/不同 prompt，对同一 relevance 维度的判断会**抖动不一致**——预筛放过的可能 score 低、预筛拒的可能 score 高。这是 Mode 失效的真正风险来源（远比 $0.02/day 重要）。
3. **N 当前上限 50**，batch 摊薄效应被低频调用稀释；想摊薄到 H > 50% 才有正收益，需 N 显著放大。

#### 与 M1-7 协同审视

M1-7 已在 URL dedup 之后、score 之前跑了一次 cheap LLM 调语义聚类。**预筛是再插一次 LLM 调，分两次"问 LLM 同一批内容"**——叠加成本与抖动风险都翻倍。除非预筛承担更弱的语义（仅 relevance 0-10，比聚类简单），否则**边际收益不抵增加的系统复杂度**。

#### DECISION: 推迟（不落地也不放弃）

**理由**：
1. 成本绝对值太小（$0.02–0.05/日）→ 即使最坏情况 +45% 也是月度 $1.2，不触发预算报警 → 决策价值低。
2. **H 数值未知**：本仓库还没有足够 raw topic 让人工标 relevance ground truth。H 在 50–70% 之间没有外部数据校准，赌不准。TrendRadar 的 H 经验值不可移植（中文新闻 vs AI/科技日报子域差异极大）。
3. 风险在质量不在成本：预筛与 score 双层 LLM 引入「同一维度两次判断」抖动（false negative = 漏掉好题），HARD_PARTS §3 没规定锚点对齐机制。
4. 当前人工 review 已经在 M4/M3 处理「gated 内容」做最后一道闸，预筛拦下的低 relevance 条目在 score 阶段拿 0–5 分也会被 `min_score=6.0` 自然淘汰——**预筛承担的阈值功能已在 score 中现成实现**。

**不放弃的原因**：
- B 方案在 H < 50% 时可省 16–36% 成本——若 M6+ 阶段 ingest 量级跃迁（N > 200/日），重新评估可转正。
- TrendRadar 的设计思想存留，作为「cheap-LLM-first」架构范式参考（M7 观察项）。
- 一旦 M6 metrics 上线且人工 review 长期承担低 relevance 内容，预筛 ROI 可能转正。

**触发重新评估的具体条件**（任一满足 → 复评此决策，升级为 P2-M1-8）：
1. **N 增长门槛**：`30d_avg_raw_per_day > 200`（即典型 N 翻 4 倍），且月度 LLM 成本占比 > 60%（说明 score 是成本大头）。
2. **人工 review 时间成本量化**：M6 metrics 显示人工 review 平均每 gated 内容 < 60s 看不完（含低 relevance 误入门），或 review review-state 直方图在 0–4 分有多峰——意味着预筛的真实边际 ROI 出现。
3. **quota 显著放大**：从 5/日 扩到 ≥ 20/日——预筛可降低 top-N 排序的 LLM 总开销（即使 H=70% 也可正收益当 N 足够大）。
4. **FAIL MODE**：`score` 阶段开始出现 `accepted=False`（JSON 解析失败）> 5%——预筛可拦截模型风格漂移（不是 ROI 而是 robustness）。

**回看时间窗**：M6 完成 + 30 天运营数据后；最迟 M6 完成后 60 天必须重新评估一次（即使未触发上面 4 条），避免永久挂着。

**不写 P2-M1-8 任务草案**：落地条件未达，转正需先有真实 H / 真实 N / 真实 review 时间数据。新任务草案由重新评估时按届时数据写。

---



---

## M11-0 发布通道开源集成评估 — 决策记录（2026-07-10，用户驱动）

> 方式：`gh api` 取 metadata/README（GitHub 走 gh CLI，见 memory bypass 笔记）+ `git clone --depth 1` 深读适配器源码。
> 目标：确定国内发布走哪个开源件、图文 vs 视频分别用什么、怎么接进 `PublisherAdapter`（签名不变）。**仅评估，不真发，不碰真账号。**
> 快照：`/tmp/pubeval/{wechatsync,multipost}`（MPP 因带 videos/media 示例过重 clone 超时，改 `gh api` 读）。移植时需重新 clone 并 pin commit。

### 三候选实读对比

| 维度 | **Wechatsync** | **MultiPost** | **MPP** (funfan0517/MediaPublishPlatform) |
|---|---|---|---|
| 定位 | 文章同步助手 | 全内容多平台发布 | 自媒体发布平台 |
| 技术路线 | **B 浏览器扩展** | **B 浏览器扩展**(Chrome/Edge) | **A Playwright 后端自动化**(非扩展) |
| 语言 | TS(monorepo) | TS(Plasmo) | **Python + Vue**(sau_backend/sau_frontend) |
| 内容类型 | **图文 only** | **图文 + 视频 + 动态** | **图文 + 视频** |
| 视频发布 | **❌ 无** | ✅ `src/sync/video/` 29 平台(抖音/快手/B站/视频号/TikTok/YT/腾讯视频/优酷/爱奇艺…) | ✅ 无人值守(小红书/视频号/抖音/快手/TikTok/IG/FB/B站/百家号) |
| 图文平台 | 20(知乎/掘金/头条/CSDN/简书/微博/公众号/语雀…) | ~40(article/) + 31(dynamic/) | 同上视频平台的图文模式 |
| 触发/回传 | **现成 MCP server + CLI + ws-bridge**(`wechatsync sync x.md --platforms zhihu,juejin` → SyncResult[]) | **postMessage 动作 + Extension API + RESTful API**(`MULTIPOST_EXTENSION_PUBLISH`/`_PUBLISH_NOW`/`_PLATFORMS`/`_GET_ACCOUNT_INFOS`；trusted-domain 握手) | Python CLI(`cli_main.py`) + Web；`platform_configs.py` 配置化加平台 |
| 无人值守 | ❌(需浏览器) | ❌(需浏览器，半自动) | ✅(headless 可无人值守) |
| 风控 | 低(真人会话，官方 web API) | 低(真人会话) | **高**(Playwright 后端特征明显，同 MediaForge 现状) |
| License | **GPL-3.0** ⚠️(copyleft) | **Apache-2.0** ✅ | **MIT** ✅ |
| 活跃/规模 | 5.9k⭐ v2 活跃 | 2.8k⭐ 活跃，Chrome/Edge 商店在架 | 126⭐，2026-01 活跃，基于 social-auto-upload |

### DECISION

- **图文分发主通道 → 采用 MultiPost**（B 路线）。理由：① Apache-2.0 宽松，可安全集成/借鉴；② **RESTful API + Extension API 最易被 MediaForge 编排层触发并回传结果**；③ 图文+视频一把梭，一个扩展覆盖两条线，减少集成面。
- **图文长尾补充 → 参考 Wechatsync（进程外调用，不 vendor 源码）**。理由：博客/资讯类平台(语雀/掘金/CSDN/简书)覆盖好，且 **MCP/CLI 现成**可被 Claude Code 直接触发；但 **GPL-3.0 有传染性 → 只能通过 CLI/MCP 进程外调用（聚合，非派生），严禁 import/vendor 其源码进 MediaForge**。
- **视频分发 → 稳态采用 MultiPost 视频扩展**（半自动、浏览器开着、真人会话、风控最低）；**无人值守兜底参考 MPP**。
- **无人值守/兜底(A 路线) → 参考 MPP 的 `platform_configs.py` 架构移植**，而非从零写 headless。理由：MPP 与 MediaForge 现状同构(Python+Playwright)，MIT 可借鉴；它已把「加平台=改配置」做出来了。**但 A 路线风控高，仅作降级，不作主通道。**

### 触发/回传如何塞进 `PublisherAdapter`（签名不变）

- 新增 `MultiPostExtensionPublisher(PublisherAdapter)` 作为 B 路线实现，与现有 headless A 路线**并存**（`PublisherAdapter` 抽象签名一字不改）。
- `publish(bundle)` 内部：编排层起一个**本地可信页面/消息桥**，发 `MULTIPOST_EXTENSION_PUBLISH` 带 SyncData(title/正文/图片/视频/目标平台+账号)，收扩展回传的 `platform_post_id`/`platform_url`/状态 → 写回 `publications`。需先完成 trusted-domain 握手。
- **三重锁 / safe_publish / dry-run / 意图日志 一律在编排层复用，扩展只当"最后一公里执行器"，不绕任何锁**（HARD_PARTS §1）。
- 触发方式候选（M11-E 定）：本地 HTTP 桥(MultiPost RESTful API) ‖ 扩展 Extension API ‖ 半自动人工点。**优先 RESTful API**（进程外、无侵入、契约清晰）。

### License 合规红线

- **Wechatsync GPL-3.0**：仅**进程外 CLI/MCP 调用**（聚合），**不 import、不 vendor、不静态/动态链接其源码**；一旦要读它的适配器逻辑照抄，必须自己重写而非拷贝。
- **MultiPost Apache-2.0 / MPP MIT**：宽松，可集成/借鉴/移植；保留 `LICENSE`/`NOTICE`，移植代码注明出处与 pin commit。

### 落 M11-E 的子任务拆细（细粒度，弱模型可接棒；**真发部分高危、人工、不进自治流**）

1. **M11-E-1（低危，可自治）**：装 MultiPost 扩展 + 起本地桥，跑通 `MULTIPOST_EXTENSION_PLATFORMS`/`_GET_ACCOUNT_INFOS`——**只读**平台/账号列表，不发任何内容。产出：桥连通性验证 + 账号映射表。
2. **M11-E-2（低危，可自治）**：`MultiPostExtensionPublisher(PublisherAdapter)` 骨架 + **dry-run**：把 approved 内容转 MultiPost SyncData，校验 bundle 完整，**只打印不发**。单测覆盖 SyncData 转换。
3. **M11-E-3（高危，人工，不进自治流）**：真发单条——`publish.enabled=true`+白名单+用户显式确认，走 safe_publish 三重锁，人工核对无重复帖。
4. **M11-E-4（参考，非阻塞）**：评估移植 MPP `platform_configs.py` 到 A 路线兜底 adapter。

DECISION: **采用 MultiPost 为图文+视频主通道(B 路线扩展)；参考 Wechatsync 作图文长尾(进程外/GPL 隔离)；参考 MPP 作无人值守兜底(A 路线/移植 platform_configs)。** 因为 MultiPost 的 Apache-2.0 + RESTful API + 图文视频全覆盖最契合"复用开源、本仓库只写编排层"，且触发/回传路径最清晰。
