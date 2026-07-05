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
