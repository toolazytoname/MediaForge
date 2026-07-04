# 开源方案调研 — MediaForge 选型溯源

> 调研日期：2026-07-04（数据来自 GitHub API 实时查询）

## 视频生成

| 项目 | Stars | 说明 | 活跃度 | 在本系统中的角色 |
|------|-------|------|--------|-----------------|
| [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 95.6k | 主题→短视频一键生成（文案/素材/TTS/字幕/BGM），FastAPI 有 HTTP API | 极活跃，MIT | **默认视频引擎**（无头、可 cron） |
| [OpenMontage](https://github.com/calesthio/OpenMontage) | 32.9k | Agentic 视频制作系统：12 流水线/52 工具/500+ agent skills，Remotion/HyperFrames 合成，参考视频逆向，真实素材蒙太奇，成本 $0.15-1.5/条 | 极活跃，AGPL-3.0 | **高级视频引擎**（需 AI 编程助手驱动，见下方注意） |
| [NarratoAI](https://github.com/linyqh/NarratoAI) | 10.1k | AI 解说+自动剪辑（影视解说赛道） | 活跃，MIT | 备选（解说类赛道才用） |
| [MoneyPrinterPlus](https://github.com/ddean2009/MoneyPrinterPlus) | 6.6k | 批量混剪+发布 | ⚠️ 2025-03 停更 | 不采用 |

### OpenMontage 集成注意（重要）

- **定位差异**：MPT 是"无头服务"（HTTP API，cron 可调）；OpenMontage 是"agent 操作的工作室"——设计上由 Claude Code/Cursor 等读文件、跑代码来驱动流水线（`pipeline_defs/` + tool registry + stage skills），质量上限远高于 MPT（Remotion 合成、词级字幕、多维 provider 评分、自我审查）
- **与"脱离 Claude 独立运行"的矛盾及解法**：本系统把视频生成抽象为 `VideoEngine` 接口。默认引擎 `mpt` 纯 API 无头运行；`openmontage` 作为**可选高级引擎**，通过 headless agent 进程驱动（`claude -p` / Agent SDK 一次性任务），主流水线只负责投递任务与取回 mp4——OpenMontage 挂了/没装，不影响默认链路
- 零 API key 也能出片（Piper TTS + Archive.org 素材 + Remotion），与我们"低成本兜底"原则一致
- AGPL-3.0：自用无义务；若将来把系统做成对外服务需评估传染性
- 建议：M5 先跑 MPT 闭环拿到量，M5 之后做 OpenMontage 评估（TASKS M5-3），用于**头部内容精品化**（周更 1 条高质量 vs 日更量产）——两引擎分工而非二选一

## 多平台发布

| 项目 | Stars | 说明 | 活跃度 | 角色 |
|------|-------|------|--------|------|
| [social-auto-upload](https://github.com/dreammis/social-auto-upload) | 13k | 抖音/小红书/视频号/TikTok/YouTube/B站 Playwright 上传 | 活跃 ⚠️ 无 License | 国内发布器**源码参考**（vendor 只读） |
| [AiToEarn](https://github.com/yikart/AiToEarn) | - | 一人公司内容营销平台，13 平台发布，有 API/MCP，可 Docker 自部署 | 活跃，MIT | **M4-0 评估对象**，可能替代自写 Playwright |
| [Postiz](https://github.com/gitroomhq/postiz-app) | 32.7k | 海外平台排期发布（X/YT/TikTok/IG/LinkedIn/Reddit），API+日历 | 极活跃，AGPL | 海外扩展期（≥3 平台时）接入 |
| [Mixpost](https://github.com/inovector/mixpost) | 3.4k | 自托管 Buffer 替代 | 活跃，MIT | Postiz 备选 |

## 选题/数据源

| 项目 | Stars | 说明 | 角色 |
|------|-------|------|------|
| [newsnow](https://github.com/ourongxing/newsnow) | 20.9k | 全网实时热点聚合 | 热点源备选 |
| [DailyHotApi](https://github.com/imsyy/DailyHotApi) | 3.9k | 今日热榜 API（支持 RSS） | **热点源默认**（自部署） |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 55.3k | 各平台内容/评论爬虫 | ⚠️ 仅限研究，不进生产链路 |

## 第二轮广撒网新发现（2026-07-05，重要）

### 全链路同类项目（先深评再动工，避免重复造轮子）

| 项目 | Stars | 说明 | 评估要点 |
|------|-------|------|----------|
| [ai-trend-publish (TrendPublish)](https://github.com/liyown/ai-trend-publish) | 3.0k | **与本设计高度同构的公众号自动化流水线**：多源抓取（RSS/HN/arXiv/Twitter/搜索API）→ AI 选题聚类+编辑决策 → 证据链补全 → 质量审稿+一次定向修订 → 微信兼容排版+AI配图 → dry-run/草稿箱；有 Dashboard（运行时间线/质量复盘/选题工作台）、**多公众号矩阵**、provider/adapter 扩展架构。TypeScript，活跃 | **最高优先级评估**：它已实现我们 ARCHITECTURE 里 sources/topics/gate/review/webui 的公众号版。决策：公众号赛道直接用它 vs 借鉴其设计自研多平台版 |
| [AIMedia](https://github.com/Anning01/AIMedia) | 2.3k | 热点抓取→AI 文章→自动发布（头条/小红书/公众号），Django+PySide6 桌面端 | 偏重、商业化导向（新版闭源 SaaS）；主要参考其头条发布实现与热点源 |
| [AiToEarn](https://github.com/yikart/AiToEarn) | - | 13 平台发布+AI 创作+MCP，可自部署 | 已列入 M4-0 评估 |

### 小红书专项（发布器候选，可能替代自写 Playwright）

| 项目 | Stars | 说明 |
|------|-------|------|
| [XiaohongshuSkills](https://github.com/white0dew/XiaohongshuSkills) | 3.1k | 小红书自动发布/评论/检索 Skill，支持 CC/Codex/OpenClaw，活跃 |
| [Auto-Redbook-Skills](https://github.com/comeonzhj/Auto-Redbook-Skills) | 1.9k | 自动撰写笔记+生成图片+发布的 Skills |
| [xhs-toolkit](https://github.com/aki66938/xhs-toolkit) | 1.3k | 小红书创作者 MCP 工具包（内容创作+发布），可 API 化集成 |
| [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) | 11.8k | 小红书采集（爆款分析数据源，谨慎使用） |

### 视频/数字人补充

| 项目 | Stars | 说明 |
|------|-------|------|
| [AIGCPanel](https://github.com/modstart-lib/aigcpanel) | 5.2k | 一站式数字人系统：视频合成/声音克隆/本地模型管理，活跃 | 数字人口播赛道备选（V2+） |
| [LuoGen-agent](https://github.com/LuoGen-AI/LuoGen-agent) | 0.8k | 对标文案提取→仿写→声音克隆→数字人→发布全链 | 思路参考 |

### 结论修正

1. **公众号图文链路**：TrendPublish 已把"选题→证据→审稿→排版→草稿"做成了可观察流水线且支持矩阵。**M0 前增加评估任务（M0-0）**：若其质量审稿达到我们门禁标准，公众号赛道直接部署它，本系统只做"总编排+跨平台统一状态机"，公众号 lane 通过其 API/CLI 对接——省掉至少 2 周工作量
2. **小红书发布**：xhs-toolkit（MCP）与 XiaohongshuSkills 提供了比裸写 Playwright 更成熟的方案，M4-3 优先评估集成而非自写
3. 本系统的**独特价值**收敛为：跨平台统一编排（一料多吃）+ 统一质量门禁 + 统一发布状态机/日历 + 数据回流闭环——各垂直环节全部站在现成项目肩膀上

## 编排

| 项目 | Stars | 角色 |
|------|-------|------|
| [n8n](https://github.com/n8n-io/n8n) | 195k | V1 不用（cron 够）；流水线条数多了再迁移 |

## 总决策

**自研编排层（本仓库）+ 开源做引擎/发布器**。理由见 ARCHITECTURE §5。所有引擎/发布器/数据源都在适配器接口后面，任何一个开源项目死掉都可整体替换而不动编排逻辑（HARD_PARTS §7 备选登记表）。
