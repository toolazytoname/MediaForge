## 组图安全导出（LAZY-52）

`Deliverable(kind=gallery)` 只引用本项目 selected `vas_`，不复制像素文件。审批检查封面、连续 slides、引用资产仍 selected。交付只开 preview/export：

- 小红书走 **assisted/export**；Product 层关闭 draft/direct
- 导出 ZIP 含 `gallery.json` / 顺序 / 封面 / 文案 / 资产引用，以及 prompt/模型/成本审计
- 无 `platform_post_id` / URL 不得记平台成功；未知 CLI 回执继续失败
- Project UI 不暴露 direct

## 图文真实安全交付（LAZY-40）

在已批准的 Project 上，article Deliverable 接到审批快照后：

- 微信只走 **draft**（官方 `draft/add`，成功必须有 `media_id`，`url` 为空，禁止群发）
- 头条只走 **export**（本地 ZIP / Markdown，人工导入可恢复）
- preview 走 `safe_publish(dry_run=True)`，不改 `topics/contents/publications/metrics`
- `delivery_attempts` 只插入终态一行；进行中靠 INTENT / `timeout_publishings`
- 同一 `idempotency_key` 不创建第二份 Publication / 正式草稿
- 无 Binding 的旧 `publish` CLI/API 行为不变；`publish-due` 跳过 `project:` 源
- Project UI 隐藏 direct，不复用旧 Publish Center「真实发布到頭條」按钮
- `autonomy` 按阶段生效：`assist` 零 LLM；`collaborate` 的 AI draft/adapt 不落盘；`pack` 终态最多 `ready_for_approval`，且不得 draft/direct
- 确认文案走 CapabilityRegistry 的 `ui.confirm_copy`，不写死「头条」

回滚：把 `config.yaml` 的 `delivery.bridge` 设为 `off`，重启 API。交付接口返回 403，旧 ZIP 导出仍可用。新表可 `DROP`；已写入的冻结表行不要删。

真实微信 AppID / 头条导入未在本仓库自动测；离线夹具覆盖契约。
