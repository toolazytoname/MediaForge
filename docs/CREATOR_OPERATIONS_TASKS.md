# 易用性改造与账号运营 — 本轮唯一任务入口

> 交接日期：2026-09-10。基线 commit：`502b75c`（公众号草稿正文配图对编辑器可见）。
> 用户已批准本清单的开发范围。**不要**因 `PRODUCT_RESET_PLAN.md` 里“多账号 / 定时运营 / 无人值守发布后置”再请示一遍。
> **批准开发 ≠ 批准对任意测试文章公开发布。** 部署升级不得自动开启现有账号的运营或公开发布；必须由用户在账号设置中明确开启后，再按该账号当时的授权规则执行。

本文件是本轮唯一执行入口。`docs/TASKS.md` 只保留指向这里的接手段落；不要机械认领旧 M* 或未勾选的 R0。

---

## 0. 最新基线（以 git 与磁盘为准，不以过期段落为准）

| 项 | 状态 |
| --- | --- |
| 真实项目 | `prj_a63f79b2` 保留；不覆盖已有主稿/selected 图片 |
| Web UI | `http://127.0.0.1:8788/`（config `webui.port: 8788`）。Mac 走 Tailscale：`http://100.105.249.48:8791/` |
| BYOK | 中转站、Responses/`gpt-5.6-sol`、GPT Image 2 生图改图、Markdown、公众号草稿发送均已提交 |
| 公众号草稿 | attempt `da_8fa0c0ba`，`media_id=T0wXMUxGNoEwkhzIN5QdEFUVn2YpxoR2v0avT8k5WaZcSVVU2xWPs1gXBBMpDmgA`。已 `draft/update` 补正文配图。**未公开发布、未群发** |
| 凭据 | 公众号 AppID 已写入 `secrets/wechat_mp_main.json`（0600、gitignore）。BYOK 文档顶部“仍缺凭据 / 未送草稿箱”为过期句，以本表和 git 为准 |
| 发布开关 | 接手时以 `config.yaml` 的 `publish.enabled` 与 `allowed_platforms` 为准；代码路径不得绕过 `safe_publish` |
| 回归旧基线 | 曾记录 1848 passed（无 SOCKS）；配图修复后无代理全量 1851 passed。**每次逻辑改动后必须重跑** |
| 未跟踪截图 | `docs/product-validation/byok-browser/open-*.png` 三张**保留，不删除、不纳入提交** |

### 产品目标（本轮同时建设）

- **A 个人创作**：想法/链接/书籍摘录 → AI 询问观点、动机、经历 → 确认方向 → 写稿配图 → 人工修改确认 → 平台交付。
- **B AI 运营**：按账号定位与固定频率自动选题、研究、写稿、配图和交付；质量不足或失败时暂停并通知（先做站内待办）。

范围：微信公众号、头条、多账号。其他平台及视频后置。

### 页面

- 主导航：**今天 / 作品 / 账号**；明显的「开始创作」和「设置」。
- 今天：真实待办、异常、下一次计划；不是后台状态大屏。
- 作品：文章居中，AI 在侧边；资料、配图、版本、交付记录按需展开。
- 账号：定位、读者、风格、参考资料、创作方式、频率、预算、交付目的地。
- 设置：模型及连接；技术日志放到高级故障详情。日常无需进入「开发者 / 运行状态」。
- Markdown 编辑、图文预览和真实平台交付为主路径；ZIP 仅备份。

### 已发现缺口（必须处理，不要绕过）

1. 首页主要是静态建议，不是真实待办队列。
2. 排期和部分交付默认 `platforms.*.accounts[0]`（`pipeline/scheduler.py::_first_account`、`pipeline/webui/api/delivery.py::_adapter_for`）。
3. Project v0 无账号字段；用独立 sidecar 绑定，**不改冻结 Project / models / SQLite**。
4. `prepare_pack` 用主题拼接 + 占位段落凑满 800 字（`pipeline/pack.py::_candidate_body`），不是真实创作管道。
5. 公众号项目交付已有 draft，没有完成 direct 公开发布。
6. 头条项目交付主要是导出；旧浏览器发布器 ≠ 已接通项目草稿/公开发布。
7. 项目自主程度 ≠ 账号运营授权；禁止伪造人工审批放行自动文章。

### 工程红线

- TDD；函数返回新对象。
- 不修改冻结 `models.py` 字段、Adapter 公共签名、SQLite 表结构；必要迁移先写 RFC 等确认。
- 账号规则、作品绑定、访谈、计划用独立 sidecar；复用 `durable_jobs` 与 delivery attempts。
- API 必须校验账号身份和作品归属。
- 默认时区 `Asia/Shanghai`，可按账号覆盖。通知先做站内待办。
- AI 不静默覆盖用户正文或平台稿；不编造个人经历；不把机器判断标成人工核实。
- 保留发布锁、幂等、审批 stale 检查；结果未知不盲目重试。
- 密钥只从 `secrets/` 或环境读取；不打印、不写入提交。
- 每个任务独立测试、更新本文件、独立 commit（`feat:`/`fix:` + 任务编号）。
- 公开发布实测必须使用用户明确授权的账号和内容；没有权限或有效登录时记录阻塞，不宣称验收通过，继续其他独立任务。

执行顺序：**账号基础 → 个人创作 → 平台交付 → 自动排期 → 综合验收。** 不要做完第一项就停。

---

## UX-01 统一产品方向与最新基线

- [ ] **目标**：把本轮授权、当前 git/凭据/草稿事实和过期交接写进本文件，并让 `TASKS.md` / `AGENTS.md` / BYOK 过期句指向这里。
- **步骤**：落盘本清单；修正“缺公众号凭据、未送草稿箱、多账号一律后置、不得进入真实发布开发”等过期表述；保留 R0 未勾选与未跟踪截图。
- **声明改动文件**：`docs/CREATOR_OPERATIONS_TASKS.md`、`docs/TASKS.md`、`docs/AGENTS.md` 或 `AGENTS.md`、`docs/BYOK_FIRST_USE_TASKS.md`、必要时 `docs/PRODUCT_RESET_PLAN.md` 文首指向。
- **验收**：任意空白上下文只读本文件即可知道下一步；不再把缺凭据当成当前阻塞；三张 `open-*.png` 仍未跟踪。
- **红线**：不改业务代码；不提交截图和 secrets。

---

## UX-02 账号创作档案与作品绑定

- [ ] **目标**：每个微信/头条账号有独立创作档案；作品明确绑定账号；凭据按账号隔离；交付必须带账号身份。禁止回退到配置里的第一个账号。
- **步骤**：
  1. sidecar `output/accounts/<account_id>/account.json`（`acc_` 前缀，`valid_sidecar_id`）；字段含定位、读者、风格、参考资料、创作方式、频率、时区、预算、交付目的地、`operations_enabled`（默认 false）、`credentials_ref`（只存 secrets 相对路径）。
  2. sidecar `output/projects/<project_id>/account_binding.json`：按平台绑定 `account_id`，不允许空绑定后猜 `accounts[0]`。
  3. 从 `config.yaml` 的 wechat_mp/toutiao 账号可导入档案，但 **不得** 把 `operations_enabled` 设为 true。
  4. 交付/排期 API 与 `scheduler` 必须要求显式 `account_id`；缺失或与绑定不一致 → 4xx，不选第一个账号。
- **测试**：创建/读取/原子写；路径穿越拒绝；两账号凭据 ref 不同；绑定缺失拒绝交付；`_adapter_for` / `_first_account` 不再静默取 `[0]`；导入 config 账号后 `operations_enabled is False`。
- **声明改动文件**：`pipeline/account_profiles.py`、`pipeline/account_bindings.py`、`tests/test_account_profiles.py`、`tests/test_account_bindings.py`、`pipeline/webui/api/delivery.py`、`pipeline/scheduler.py`、相关 API/测试、本文件。
- **红线**：不改 Project frozen 字段；不把 secret 写入 sidecar；不开启现有账号运营。

---

## UX-03 参考资料访谈 → 账号定位

- [ ] **目标**：用户提供参考文章/可读取链接 + 简短访谈后，生成账号定位与风格建议，经用户确认才写入档案。
- **步骤**：读取公开 URL 或粘贴正文；访谈观点/读者/不能写什么；LLM 只产出**建议**；确认接口才更新 `account.json`。读失败明确报错，不编造已读。
- **测试**：建议不落盘；确认后落盘；URL 失败不写风格；无访谈不能确认。
- **声明改动文件**：`pipeline/account_onboarding.py`、`pipeline/webui/api/` 账号路由、测试、前端账号页最小表单、本文件。
- **红线**：不静默覆盖已确认档案；不把建议标成已确认。

---

## UX-04 简化导航与真实待办首页

- [ ] **目标**：主导航收敛为今天 / 作品 / 账号；明显开始创作与设置；首页接真实待办/异常/下次计划。旧路由保持兼容。
- **步骤**：改 `AppShell`；`/projects` 别名作品；设置从开发者抽屉提升；Today 读待办 sidecar/delivery/jobs，去掉静态“下一步”卡片作为唯一内容。
- **测试**：路由别名；待办 API 空队列/异常/计划；桌面+窄屏导航可点到开始创作和设置。
- **声明改动文件**：`frontend/src/layouts/AppShell.vue`、`frontend/src/router/index.ts`、`frontend/src/views/Today.vue`、待办 API 与测试、本文件。
- **红线**：不删除旧路由文件；开发者抽屉仍可进运行状态，但不是日常入口。

---

## UX-05 个人创作先访谈后写稿

- [ ] **目标**：个人稿在写正文前先访谈并保存作者观点和经历；只有书名时不能假装读过原书。
- **步骤**：作品 sidecar `interview.json`；无确认访谈不得调用主稿 LLM；来源是书名且无摘录 → 明确“未读原书，只根据书名/公开信息，经历待作者补”。
- **测试**：缺访谈拒绝起草；有访谈的观点出现在 prompt；书名-only 标记 unread。
- **声明改动文件**：`pipeline/interviews.py`、主稿 API、测试、作品页访谈 UI、本文件。
- **红线**：不编造个人经历；AI 稿仍是建议，不自动覆盖现稿。

---

## UX-06 文章居中工作台

- [ ] **目标**：文章居中，AI 建议在侧边，先审阅后接受；版本可恢复；微信和头条稿独立编辑。
- **步骤**：重组项目详情布局；沿用已有 suggestion/accept/version；配图、资料、交付按需展开。
- **测试**：接受前正文不变；恢复版本；改微信稿不动头条稿。浏览器走查桌面/手机。
- **声明改动文件**：`frontend/src/views/Projects.vue`（或拆分组件）、相关测试/走查截图目录（勿提交未批准的 open-*.png）、本文件。
- **红线**：不静默应用 AI 建议。

---

## DEL-01 统一账号交付与授权

- [ ] **目标**：区分「人工版本审批」与「账号自动运营规则授权」；保存授权版本和质量结果。自主程度不能代替账号授权。
- **步骤**：账号 sidecar `authorization.json`（规则版本、允许 draft/direct、质量下限、启用时间）；交付前校验：作品绑定账号 + 内容审批 +（自动路径还要）账号授权版本未过期。
- **测试**：仅有 pack 自主程度不能 direct；授权关闭则自动任务暂停；人工审批一条作品不能被拿去另一账号。
- **声明改动文件**：`pipeline/account_authorization.py`、delivery service、测试、本文件。
- **红线**：不伪造审批记录；不把机器质量分写成 human_verified。

---

## DEL-02 公众号多账号草稿与公开发布

- [ ] **目标**：按绑定账号发送草稿；在用户明确授权的账号上实现 direct；权限检查与结果查询。
- **步骤**：`_adapter_for` 按 account_id 选凭据；draft 沿用现有；direct 走官方发布接口但必须 `publish.enabled` + 白名单 + 账号 `delivery_target=direct` + 内容授权。结果以平台回执为准。无权限记录阻塞。
- **测试**：两账号凭据不串；缺权限不调用发布；未知结果不自动重试。
- **声明改动文件**：`pipeline/publishers/wechat_mp.py`、delivery、测试、本文件。
- **红线**：不得对 `prj_a63f79b2` 或未授权内容做公开发布实测。无授权则标 BLOCKED 并继续其他任务。

---

## DEL-03 头条真实草稿与公开发布

- [ ] **目标**：项目交付接通头条保存草稿及公开发布；会话隔离；登录恢复；回执核对。导出不算发送成功。
- **步骤**：把现有 toutiao publisher 接到 project delivery；每账号独立 cookie/profile；login_expired 停止并进待办。
- **测试**：无 cookie 不宣称成功；账号 A 的 storage 不读账号 B；回执字段进入 attempt。
- **声明改动文件**：`pipeline/publishers/toutiao.py`、delivery、login、测试、本文件。
- **红线**：同 DEL-02；登录失效不反复撞。

---

## AUTO-01 真实自动创作管道

- [ ] **目标**：替换 `prepare_pack` 占位正文；真实选题去重、研究、写稿、封面插图、平台适配；最多自动修订两轮，不合格暂停。
- **步骤**：删除/停用 `_candidate_body` 凑字；调用研究+主稿 LLM+视觉+适配；gate 失败暂停，不进入交付。
- **测试**：产出正文不是主题循环粘贴；无来源/无访谈则失败；修订计数 ≤ 2。
- **声明改动文件**：`pipeline/pack.py`、自动创作编排、测试、本文件。
- **红线**：自动路径仍不得 `safe_publish(dry_run=False)`，除非 DEL-01 授权且任务要求交付。

---

## AUTO-02 账号频率、持久任务与去重

- [ ] **目标**：按账号固定频率、时区、预算启停；持久任务、阶段恢复、暂停取消；重启不重复创建计划。
- **步骤**：计划 sidecar；`durable_jobs` kind 扩展需评估是否改 SQLite（能不加列就用 request_json；加列先 RFC）。幂等键含 account_id+slot+date。
- **测试**：重启不插入第二份同一 slot；预算超限暂停；operations_enabled=false 不调度。
- **声明改动文件**：计划模块、jobs、测试、本文件。
- **红线**：不改 publications UNIQUE 语义；结果未知不重试发送。

---

## UX-07 异常回到今天

- [ ] **目标**：失败、缺资料、登录过期、质量不合格出现在今天，并提供补资料、编辑、重试、跳过、核对结果。
- **测试**：各类异常都生成待办；核对结果不自动重发。
- **声明改动文件**：待办模块、Today 页、测试、本文件。

---

## QA-01 回归与真实验收

- [ ] **目标**：全量 pytest、生产构建、桌面/手机走查、真实流程验收。
- **步骤**：无 SOCKS 跑 `pytest tests/ -q`；`cd frontend && npm run build`；Playwright 走今天/作品/账号/设置；公众号/头条以真实回执为准。
- **红线**：不把导出当发送成功；不提交 secrets 和那三张未跟踪截图。
- **声明改动文件**：本文件、`docs/TASKS.md` 完成摘要、走查证据（新文件，不含保留的 open-*.png）。
