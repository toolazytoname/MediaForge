## 图文真实安全交付（LAZY-40）

在已批准的 Project 上，article Deliverable 接到审批快照后：

- 微信只走 **draft**（官方 `draft/add`，成功必须有 `media_id`，`url` 为空，禁止群发）
- 头条只走 **export**（本地 ZIP / Markdown，人工导入可恢复）
- preview 走 `safe_publish(dry_run=True)`，不改 `topics/contents/publications/metrics`
- `delivery_attempts` 只插入终态一行；进行中靠 INTENT / `timeout_publishings`
- 同一 `idempotency_key` 不创建第二份 Publication / 正式草稿
- 无 Binding 的旧 `publish` CLI/API 行为不变；`publish-due` 跳过 `project:` 源
- Project UI 隐藏 direct，不复用旧 Publish Center「真实发布到頭條」按钮

回滚：把 `config.yaml` 的 `delivery.bridge` 设为 `off`，重启 API。交付接口返回 403，旧 ZIP 导出仍可用。新表可 `DROP`；已写入的冻结表行不要删。

真实微信 AppID / 头条导入未在本仓库自动测；离线夹具覆盖契约。
