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

连带结论：
- **OpenMontage 从「第二引擎候选」降级为远期观察**——Pixelle-Video 在生产可用性（Docker + HTTP API + 进度回报）上全面胜出 agent 驱动模式。
- **国内发布回归自写 Playwright 主线**（TECH_SPEC §5.2 契约不变），AiToEarn 的 Electron 遗留代码（MIT，约 6800 行）作为平台接口摸底资料。

---

## 1. TrendPublish（liyown/ai-trend-publish，3.0k⭐，MIT，Deno/TS）

**DECISION: 参考（借鉴自研为主 + 局部代码级移植），不整体部署** 因为：架构与本系统同构 80%（同为「选题→创作→审稿→dry-run→发布」状态机），整体部署得到「双状态机、双配置、双人审入口」；其选题层与创作层强耦合，「只用公众号 lane」必须 fork（Deno/TS 与我们 Python 异构）；单人维护、脉冲式活跃，不宜做运行时依赖。

**评估要点**：
- 活跃度：最近 push 2026-06-14，仅 1 个 open issue，53 个单测，v2.0.7。单人项目（liyown）。
- 流水线：`workflow.ts` 1178 行状态机，每步产物落 artifact store 可复盘，有「编辑记忆」（人工反馈回流选题 prompt）。
- 质量审稿：7 维 JSON 评分 + normalize 交叉推导（低分无 issue 时合成 issue）+ 修订白名单（禁碰事实类/blocker 问题）+ 修订后强制复审、评分单调不降才采纳否则回滚 + fact 高危一票否决。**但审稿人=作者（同一 LLM 配置），无锚点校准、无强制批判配额**——「AI 给 AI 打高分」防线未闭合，恰好印证 HARD_PARTS §3 的设计必要性。
- 可集成性：有 HTTP API（POST /api/runs 支持 dryRun）+ CLI；dry-run 默认开，真发布也只到微信草稿箱。但无「从外部注入已选题内容」的入口。

**移植清单**（影响 M2-1/M2-2/后续公众号 lane）：
1. **门禁修订协议**（→ M2-2）：修订只许碰安全问题白名单（title/tone/structure/html），禁碰事实类；修订后强制复审，评分不降才采纳否则回滚。在此之上补我们的锚点校准 + 异构评分模型。
2. **微信兼容 HTML 后处理器**（→ 公众号 lane，Backlog）：`html-post-processor.ts` 231 行纯函数清洗（div→section、剥 class/script、外链转脚注）+ validateWeixinHtml，社区最完整的公众号排版知识沉淀。
3. **防幻觉 prompt 条款**（→ M2-1）：「商业状态/定价/API 开放性/参数规格，只有来源明确写出才能确定表述」+ 逐句实体支持度过滤（`dropUnsupportedEntityParagraphs` 模式），抄进 canonical 创作 prompt。
4. 其 `src/experiments/article-quality/`（审稿机制 A/B 评估器）思路可用于 M2-2 验收的门禁校准。

---

## 2. 小红书发布：XiaohongshuSkills vs xhs-toolkit vs 自写

### xhs-toolkit（aki66938，1.3k⭐）

**DECISION: 放弃** 因为：README 首行官宣停更（「后续也不再计划维护」）；正文编辑器选择器停留在 Quill，小红书 2026 年初已改版 TipTap/ProseMirror，社区修复 PR 悬空未合并；26 个 open issue 大量无人回应；Selenium+ChromeDriver 还有版本匹配包袱；反检测几乎为零。集成死项目 = 变相自写。

### XiaohongshuSkills（white0dew，3.1k⭐）

**DECISION: 采用（M4-3 集成为发布引擎）** 因为：三候选中唯一「能力全集 + 活维护」——图文多图、真话题标签（下拉候选选中）、视频、定时发布（`--post-time`）全覆盖；已适配 2026 年 2-3 月 DOM 改版，选择器集中在单文件 `SELECTORS` dict 且带三级 fallback；issue 响应以天计（05-30 提 → 05-31 关）；裸 CDP 驱动真 Chrome（无 webdriver 指纹）+ 逐字输入 45-95ms 随机延迟，反检测比 Selenium 强一代；CLI + 明确退出码 + `PUBLISH_STATUS:` 状态行，subprocess 封装进 `PublisherAdapter` 顺畅；Chrome profile 隔离天然支持多账号；附赠创作者数据看板 CSV 抓取，可复用到 M6-1 metrics 回流。

**集成护栏（写入 M4-3）**：
1. **mac 冒烟测试先行**——该项目 Windows 优先（chrome_launcher 已有 darwin 路径，profile 路径需验证），跑不通降级 Plan B；
2. **vendor 固定 commit**（2026-05-21 `fix(_click_tab)` 之后的 main），不追 HEAD；
3. **dry_run 语义分层**：契约的 `dry_run=True` 在 adapter 层校验 bundle 即返回、完全不碰浏览器；其 `--preview`（填充表单不点发布）仅作 M4 上线前人工验证档位；
4. **频控归编排层**：它无内建限流，社区有封号案例（2026-05）——日限额/间隔全按 HARD_PARTS §2 §6 在我们侧执行。

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

**适配要点（→ M5 新增任务）**：
- `VideoRequest` 映射：script→text（mode=fixed）、aspect→frame_template 尺寸目录、style→frame_template + prompt_prefix；`duration_s` 无法硬指定，适配层按语速预估校验；
- 轮询 404（服务重启丢任务）按 failed 处理 + 重提交；
- API 未暴露 split_mode，脚本分段在我们侧预处理为段落；
- 生图供应商 API Key 单列 config，走 `secrets/`；每条视频记录生成成本。

**连带决策**：OpenMontage 降级为远期观察（M5-3 评估范围相应缩减为 AIGCPanel 数字人 + 可选看一眼 OpenMontage），Pixelle-Video 接管「精品/差异化视觉」定位。

---

## 对 TASKS.md 的影响（已同步修改）

- **M2-1**：步骤补充「移植 TrendPublish 防幻觉 prompt 条款」
- **M2-2**：步骤补充「参考 TrendPublish 修订协议（白名单修订+复审单调不降）」
- **M4-3**：小红书改为「集成 XiaohongshuSkills（subprocess 封装，四条护栏）」；头条维持自写 Playwright（参考 AiToEarn electron 遗留代码 + social-auto-upload）
- **M5-1**：维持 MPT 默认引擎不变
- **M5-3** → 改为「Pixelle-Video 第二引擎接入」（原 OpenMontage/AIGCPanel 评估内容缩减进 Backlog/子项）
- **HARD_PARTS §7 备选表**：国内发布主选 = 自写 Playwright + XiaohongshuSkills（小红书）；AiToEarn 移出候选
