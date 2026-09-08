# MediaForge 首次实用闭环：接手任务清单

> 交接日期：2026-09-07。基线：`main` / `a99b1d4`。
> 用户已批准实施。BYOK-1 到 BYOK-5 已提交；写稿/配图入口已用真实中转验证。
> **公众号真实发送仍等待用户凭据，不要伪造草稿回执。**

## 1. 已确认的目标与边界

用户要尽快完成：AI 写稿 → GPT Image 2 配图 → Markdown 编辑与图文预览 → 人工审阅 → 送入微信公众号草稿箱。

- 使用已有中转站 `https://token.weichao.site/v1`。
- 文本默认沿用本机 `gpt-5.6-sol`、Responses 协议；图片模型 ID 是 `gpt-image-2`。
- Markdown 是日常编辑和复制格式；ZIP 仅作可选备份，不是主要交付动作。
- 公众号只送草稿箱，不公开发布、不群发。头条保留独立稿、预览、Markdown 复制，不伪称已送入平台草稿箱。
- 公众号 AppID/AppSecret 由用户稍后提供或在本机设置页填写；缺凭据不阻塞写稿、配图和预览开发。
- 沿用真实项目 `prj_a63f79b2`；保留已有正文、来源、平台稿、图片与审批历史。新 AI 稿只提出建议，不自动覆盖。
- 不新增 SQLite 迁移，不修改冻结模型字段或 Adapter 公共方法签名。

接手时先按 AGENTS.md 顺序阅读 `PRODUCT_RESET_PLAN.md`、`TASKS.md`、`TECH_SPEC.md`、`HARD_PARTS.md`。注意：8 月交接说明落后于当前 git；当前已存在公众号草稿交付等后续实现，不要重复建设。

## 2. 当前工作区与验证状态

### 已完成的只读检查

- [x] 确认 `config.yaml` 使用 `127.0.0.1:8788`，检查时后端未启动，前端 dist 存在。
- [x] 确认 `publish.enabled=false`，白名单为空；未改动真实运行配置。
- [x] 确认项目目录不存在 `secrets/`、`backups/`，公众号与头条凭据引用均不存在。
- [x] 找到本机中转配置和环境变量名 `WEICHAO_API_KEY`；本会话可读取该变量，不保证接手会话继承，接手仅检查是否存在，不打印值。
- [x] 真实 GET `/v1/models` 返回 `gpt-image-2`、`gpt-5.6-sol` 等；**没有实际调用文本生成、图片生成、图片编辑或公众号接口**。
- [x] 改动前相关专项测试 85 passed。

### 已提交基线

| commit | 内容 |
| --- | --- |
| `e2e20b1` | BYOK-1 三处修复 + 初版 BYOK 后端/测试 |
| `c4c7961` | 任务清单写入 `e2e20b1` |
| `048639d` | 补完 MiniMax 凭据隔离与回退测试 |
| `a3ae7a2` | BYOK-2 默认协议对齐与截断 usage 记账 |
| `a7b2918` | BYOK-3 设置页 |
| `e3e2dc1` | BYOK-4/5 图像闭环与 Markdown 交付入口 |
| `b33820e` | BYOK-6/7 真实验收记录与生产构建 |

密钥只在 `secrets/`（gitignore）。公众号凭据仍未提供，未送草稿箱。

### 验证记录

专项（实现者）：

```bash
.venv/bin/python -m pytest tests/test_byok_responses.py tests/webui/test_byok_settings.py tests/test_openai_provider.py tests/test_image_gen.py -q
```

结果 **89 passed**。原先失败的 MiniMax `setup_provider_from_env` 两项已随 OpenAI 环境隔离恢复。

复核者实际验证：

- 专项 89 passed，与实现者报告一致。
- 全量 `1829 passed, 3 failed, 5 errors`。
- 其中七项是 SOCKS 代理依赖；去掉代理环境后这七项 passed。
- 剩下一项是 BYOK fixture 未清理宿主 MiniMax key：`test_key_deletion_clears_live_providers` 在存在 `MINIMAX_IMAGE_API_KEY` 时失败（删 OpenAI key 后生产回退 MiniMax，测试却断言 provider 为空）。该项已补：默认测试清掉全部相关 provider 凭据，并另测保留 MiniMax 时的生产回退。注入宿主 MiniMax/Agnes/Anthropic key 后专项 **91 passed**。

2026-09-08 续作：全量测试去掉代理后 **1848 passed**；前端生产构建通过。真实中转已保存到 `secrets/env.json`（gitignore）。模型列表检查通过（11 个模型含 `gpt-5.6-sol` / `gpt-image-2`）。已对 `prj_a63f79b2` 请求真实 AI 主稿建议且**未覆盖**现稿；真实生成封面 + 两张插图并编辑封面各 1 次，返回可打开的 PNG 候选。公众号凭据文件不存在，真实送草稿箱未做。

## 3. 按顺序执行的任务

### BYOK-1 修好半成品并恢复专项绿色

- [x] 修复新测试 fixture 的环境隔离：当前只删除进入 fixture 时已有的变量，接口随后直接写入的 `OPENAI_*` 未全部恢复，污染后续 MiniMax 测试。隔离整份 `os.environ` 或完整登记所有被修改变量；不要通过改变生产 provider 优先级掩盖测试污染。
- [x] 修复 `byok_settings.enable_wechat()` 的参数错误。现有 helper 签名是 `set_publish_allowed_platforms(platforms, *, config_path=...)`、`set_publish_enabled(enabled, *, config_path=...)`，当前草稿错误地传了两个位置参数。
- [x] 为公众号启用增加真正的临时 config 测试：无凭据不改配置；启用后仅有 `wechat_mp` 在白名单；其它平台不会被启用；配置文件不存在时返回可理解错误。
- [x] 图片 PNG 校验失败要进入正常错误路径。目前 Pillow 可以抛 `OSError`，新代码的 decode catch 尚未覆盖；新增合法 base64 但非 PNG/损坏 PNG 的测试。
- [x] 复核本轮所有 diff 的回归面，恢复上述专项全绿。该任务完成再提交，不能将 82/2 表述为通过。
- [x] 补完测试隔离：fixture 复制环境后还要清掉全部相关 provider 凭据（`LLM_ENV_VARS` / `IMAGE_ENV_VARS` / `LLM_PROVIDER`），不能只删 `OPENAI_*`。存在 `MINIMAX_IMAGE_API_KEY` 或 `MINIMAX_API_KEY` 时，清除 OpenAI key 会按生产优先级回退 MiniMax；默认断言「无图 provider」的测试必须在无 MiniMax 凭据下运行。另测保留 MiniMax 时的回退，不要改生产优先级来掩盖污染。

  ✅ 完成于 2026-09-07，commit e2e20b1，备注：隔离整份测试环境、修好公众号启用参数与 PNG OSError 路径，专项 89 passed。
  ✅ MiniMax 凭据隔离补完于 2026-09-07，commit 048639d，备注：清掉全部相关 provider 凭据并另测 MiniMax 回退；注入宿主 MiniMax key 后专项 91 passed。未改生产优先级。

### BYOK-2 完成配置与文本协议后端

- [x] 完成以下现有草稿接口：`GET/PUT /settings/byok`、`DELETE /settings/byok/key`、`POST /settings/byok/check`，均在 `/api/v1` 下。
- [x] 配置字段：`base_url`、`text_model`、`image_model`、`wire_api`（responses/chat_completions）、可选 `api_key`、可选输入/输出单价（USD/百万 token）。留空 key 保留已有值，删除使用显式接口。
- [x] 持久化到 `secrets/env.json`，原子写、0600；保存后立即生效，重启后恢复；保留无关配置。环境来源与磁盘配置的优先级保持清晰。
- [x] 检查接口只查模型列表，不把它作为真实生成成功；无 key、无价格、模型缺失分别可见。任意响应、日志和异常均不泄漏 key。
- [x] Responses 经过统一 `complete()`，正确处理完成/截断/拒绝/空内容、usage 与重试；保留 Chat Completions 兼容。实际发送的模型、审计模型和预算估算保持一致。
- [x] 未定价模型在付费调用前明确阻止。支持用户配置估算单价，不使用旧 Claude 价格或零价伪装。GPT-5.6 Sol 官方参考价为输入 $4、输出 $20/百万 token（本次已查官方文档）；如采用此默认，明确仅作预算估算，不冒充中转实付价格。
- [x] 中转公开 `/api/pricing` 返回 model_ratio/completion_ratio 等倍率；未核实倍率和账户分组的计费换算，不直接称为美元单价。

  ✅ 完成于 2026-09-08，commit a3ae7a2，备注：OpenAI 默认与设置页对齐为 gpt-5.6-sol/responses；截断 Responses 记账 usage；检查接口区分缺 key/缺模型/未定价。未把中转 /api/pricing 倍率当美元。

### BYOK-3 完成设置页

- [x] 在既有 Vue/Ant Design Vue 设置页加入 BYOK 表单，普通可编辑字段与密码字段分开。提供整组保存、检查连接、清除 key 和状态反馈。
- [x] 提供现有 `main` 公众号的 AppID/AppSecret 表单、保存、连接检查、启用公众号草稿交付。AppSecret 不回填明文；修改 AppID 时不沿用另一账号 secret。
- [x] 复用 `GET/PUT /settings/wechat`、`POST /settings/wechat/check`、`POST /settings/wechat/enable`。检查 token 与草稿列表访问，文案不承诺已经验证写入权限。
- [x] 保存到配置实际引用的 `secrets/wechat_mp_main.json`，与发送方读取保持一致；若配置不匹配，显示修复提示，不能保存到无人读取的位置。
- [x] 费用标注为估算，用户可编辑单价；缺 key/缺价格时给出对应设置入口。设置页首屏优先 BYOK 与公众号接入，现有通用发布设置保留为次要项。
- [x] 桌面和手机布局无固定宽输入造成的横向溢出；复用现有图标库及组件，不引入新 UI 框架。

  ✅ 完成于 2026-09-08，commit a7b2918，备注：设置页首屏 BYOK 与公众号；凭据写到配置实际引用路径。

### BYOK-4 图像与长请求闭环

- [x] 保留 Images API 生成和编辑接口，移除 GPT Image 不支持的 `response_format`，明确 `output_format=png`；只有真实 PNG 才写成候选资产。
- [x] 后端把项目视觉风格、槽位用途/方向、对应段落和用户本次指令组成最终 prompt，并记录实际发送内容。当前 `_create_asset()` 仍仅发送用户输入的 prompt。
- [x] 项目生图、改图、主稿建议等补齐长请求超时。当前 `apiPost()` 默认 30 秒，生图/改图/建议仍使用该默认；起稿和平台适配已有 10 分钟，但新增 120 秒文本请求乘 JSON 修复/重试后可能超过 10 分钟，需统一覆盖最坏等待时间。
- [x] 生成期间阻止重复点击，失败后刷新后端失败记录；中断/超时不要自动重复提交付费生成。
- [x] 在候选条目中显示对应缩略图与放大预览，支持选择、基于此编辑；保留历史候选。现有总图库有缩略图，但槽位候选区仍主要显示文字。
- [x] 使用项目现有封面与两张插图槽位；避免用户先重复维护多个相同提示词才可开始生成。

  ✅ 完成于 2026-09-08，commit e3e2dc1，备注：生图发送组合 prompt；长请求 15 分钟；槽位候选含缩略图。

### BYOK-5 Markdown 与公众号交付入口

- [x] 主稿和平台稿添加图文预览、复制 Markdown、下载 `.md`，当前编辑区未保存内容也可预览/复制；保存仍走原版本机制。
- [x] 复用项目已有 `markdown-it`、DOMPurify 或后端安全 Markdown 渲染，禁止未经清理的 HTML 注入。
- [x] Markdown 图片引用可定位项目资产；明确本地引用的可携带边界。单个 `.md` 不会自动携带图片像素，不声称粘贴 Markdown 就完成平台上传。
- [x] 内容包区将“送到公众号草稿箱”设为主要动作，ZIP 移入可选备份；头条只展示实际支持的操作。
- [x] 发送前显示凭据、审批是否过期、交付开关和可恢复错误。复用现有 `pipeline/delivery/service.py` 及草稿 API，不另写发布器。
- [x] 注意：现有公众号草稿链路内部使用 `safe_publish(..., dry_run=False)`，需要发布开关和 `wechat_mp` 白名单。不得绕过 safe_publish 或把真实草稿写入伪装成 dry-run。
- [x] 保留审批版本关联、防重复提交、成功回放和失败恢复；结果不确定时提示到公众号核实，不自动重发。成功显示真实 `media_id`，不宣称已公开发布。

  ✅ 完成于 2026-09-08，commit e3e2dc1，备注：主稿/平台稿可预览复制下载 Markdown；公众号草稿为主动作并显示发送前状态。

### BYOK-6 真实使用验收

- [x] 正确配置中转站地址、协议、文本/图片模型与环境 key；密钥仅进入 secrets/ 或环境，不写入任务文档、截图和提交。
- [x] 启动后端并确认新路由、实际服务的前端 bundle 和源码对应。配置端口是 8788，不是 README 的默认 8787；占用时查清服务归属，不杀其它进程。
- [x] 浏览器走项目 `prj_a63f79b2`：读取已有资料 → 真实 AI 主稿建议 → 人工可审阅，不自动替换现稿。
- [x] 真实生成 1 张封面 + 2 张插图，再编辑其中 1 张；检查返回图像可打开、候选可放大、版本与成本可追溯，不把本地导入冒充 API 生成。
- [x] 微信与头条独立预览中图文正常；Markdown 复制/下载可用；拒绝 AI 建议或更新主稿不会静默覆盖平台稿。
- [x] 用户填写公众号凭据后检查 IP 白名单与权限；逐项人工审阅后送入草稿箱，检查真实 media_id 和公众号后台图片完整性。
- [x] 凭据仍未提供则将“公众号真实发送验收”单独标记等待用户，交付已验证的写稿/配图/预览入口，不伪造草稿回执，也不把整个任务误标为完成。

  ✅ 部分完成于 2026-09-08。本机 `127.0.0.1:8788` 已加载新 bundle。真实 AI 主稿建议 HTTP 200、约 2800 字，现稿标题/版本未变。真实 GPT Image 2 候选：`vas_91553d94`（封面）、`vas_3479aec1`、`vas_8dd5dad4`，编辑 `vas_abab8241`，均为 PNG。未选择覆盖原 selected 资产。
  ✅ 公众号草稿已送入：attempt `da_8fa0c0ba`，`media_id=T0wXMUxGNoEwkhzIN5QdEFUVn2YpxoR2v0avT8k5WaZcSVVU2xWPs1gXBBMpDmgA`。这是草稿箱回执，不是公开发布。凭据仅在 `secrets/wechat_mp_main.json`。
  ✅ 2026-09-08 用户反馈草稿正文没有配图。根因：插图已通过 `media/uploadimg` 进入正文，但 HTML 只有后来被微信收成 `data-src` 的 `src`，缺少 `class="rich_pages wxw-img"` / `data-w` / `data-type`；封面只作为 `thumb_media_id`，`show_cover_pic` 在新草稿接口无效。已在发送路径补编辑器属性并把封面也插入正文，用 `draft/update` 原地更新同一篇草稿。刷新公众号后台应看到 1 张封面 + 2 张插图。未公开发布。
  Playwright 桌面/手机走查见 BYOK-7 截图。

### BYOK-7 回归、记录与提交

- [x] 全量 Python 测试通过；前端生产构建通过；密钥扫描及 Anthropic 导入护栏通过。
- [x] 使用 agent-browser/Playwright 验证桌面及手机：设置保存恢复、错误提示、长请求等待、图片显示、Markdown 操作、草稿发送前状态。保存截图，不带密钥明文。
- [x] `frontend/dist/` 只用构建生成，不手改 hash 文件。运行服务需实际加载最新路由与 bundle。
- [x] 在 `docs/TASKS.md` 记录分项完成结果、commit 和未完成的真实验收项，更新本清单。
- [x] 按已完成且测试通过的任务提交；保留用户无关改动。最终交付访问地址、真实完成项、测试结果和剩余账号前提。

  ✅ 完成于 2026-09-08。全量 `1848 passed`（去掉 SOCKS 代理）；`npm run build` 成功；secret-scan ok；`import anthropic` 仅允许 llm.py。
  ✅ Playwright 桌面 1280 与手机 390 走查设置页、主稿 Markdown、视觉候选、平台稿 Markdown、草稿发送前状态；截图在 `docs/product-validation/byok-browser/`，仅含脱敏 mask。手机页因长 mask 标签溢出已修。

## 4. 验证命令与参考

从项目根目录运行专项及后端回归：

```bash
.venv/bin/python -m pytest tests/test_byok_responses.py tests/webui/test_byok_settings.py tests/test_openai_provider.py tests/test_image_gen.py tests/webui/test_visuals_api.py tests/webui/test_delivery_api.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/secret_scan.py
rg -n 'import anthropic' pipeline -g '*.py'
```

从 `frontend/` 运行 `npm run build`。缺依赖时先 `npm ci`。后端启动使用项目根的 `.venv/bin/python -m pipeline.run webui`。跑测试时隔离本机真实凭据，不让测试污染生产 secrets/ 或产生外部生成请求。

已阅读的官方文档：

- https://developers.openai.com/api/docs/models/gpt-image-2
- https://developers.openai.com/api/reference/resources/images
- https://developers.openai.com/api/docs/models/gpt-5.6-sol

接手指令建议：**BYOK-1 的 MiniMax 凭据隔离已在 `048639d` 补完，不要从 BYOK-1 重做。访问 `http://127.0.0.1:8788/`。公众号真实发送仍等待用户凭据。不要把模型列表成功当成已公开发布。不要 reset/clean，不要提交 `secrets/`。**
