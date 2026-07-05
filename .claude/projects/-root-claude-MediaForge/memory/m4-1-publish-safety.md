---
name: m4-1-publish-safety
description: MediaForge M4-1 发布安全框架（safe_publish 三重锁 + timeout 清理）的设计要点
metadata:
  type: project
---

MediaForge M4-1（发布安全框架）关键设计点与坑（commit cf10241）：

## 核心契约
- `pipeline/publishers/safe_publish.py` 是所有真实发布的**唯一入口**
- 返回 `SafePublishResult(published: bool, reason: str, ...)`
- M4-1 **不实现具体平台 publisher**——X/头条/小红书由 M4-2/3 接入

## 三层防御（HARD_PARTS §1）

1. **第一道锁（配置层，零 DB 开销）**：
   - `publish.enabled = False` → 拒绝
   - 平台不在 `allowed_platforms` 白名单 → 拒绝
   - `scheduled_at > now` → 拒绝
   - 任何一项不满足 → 直接返回 SafePublishResult(published=False)，**不触 DB**

2. **第二道锁（乐观锁，事务化）**：
   ```python
   UPDATE publications SET status='publishing' WHERE id=? AND status='queued'
   ```
   - rowcount==1 才继续；否则意味着另一进程已抢走
   - 抢锁成功 → INTENT 日志 → validate → publish → 落库 published/failed

3. **第三道锁（UNIQUE 兜底，db.py 已定义）**：
   - `UNIQUE(content_id, platform, account_id)` —— 任何路径漏过重复都在这里挡住
   - safe_publish 通常不重复触发，但作为最后防线

## INTENT 日志（§1 决策 4）

```python
log_event(logger, 20,
    f"INTENT publish {pub.id} platform={adapter.platform} account={account.id} dry_run={dry_run}",
    stage="publish", ref_id=pub.id)
```

- 调 adapter.publish **前**落 logs/pipeline.log
- 进程死在发布后落库前 → 重启时 timeout_publishings() 清理 → failed + "manual check needed"
- **绝不自动重试**（人工核实平台是否真发出）

## 超时清理（timeout_publishings）

```python
UPDATE publications SET status='failed', error='publishing timeout > 30min (manual check needed)'
WHERE status='publishing' AND updated_at < (now - 30min)
```

- cmd_publish 启动时调一次（不引入额外 cron）
- 30min 阈值（HARD_PARTS §1 决策 3）

## 异常处理

- `PublishError`（业务异常，如 cookie 失效、网络超时）→ failed + error 字段
- 其他异常（KeyError、TypeError 等）→ failed + "unexpected: ..."（不被外层吞）
- validate 失败（validate 返回非空 list）→ failed + "validate failed: ..."（不发出去）

## cmd_publish 编排顺序

```
1. timeout_publishings(conn, 30min, now)
2. 取 publications WHERE status='queued' AND scheduled_at <= now
3. 遍历每条 → safe_publish()
4. 摘要行 publish: N published, N skipped, N failed
```

M4-1 阶段无具体 adapter，所以全部 skipped（"adapter 未注册"提示）。M4-2/3 接入具体 publisher 时把 safe_publish 调用补上。

## 测试模式（test_publish_safety.py）

- Mock publisher adapter 用 plat_value 中间变量解决 class body 作用域问题
- `_seed_publication` 自动建 topic + content FK
- 配置层测试只验证 published=False 即可（不查 DB 状态）
- 状态机测试查 row 状态断言
- INTENT 日志测试读 logs/pipeline.log 文件验证关键字

**Why**: M4-1 是发布安全的"宪法"——一旦 M4-2/3/4 各平台 publisher 实现，全部走 safe_publish 入口。三层防御 + INTENT 日志 + timeout 清理是防重复发帖（封号风险）的最后底线。
**How to apply**: M4-2/3/4 任何 publisher 实现都调 safe_publish()；禁止绕过直接 conn.execute UPDATE publications。这条违反 = 评审打回。

**Why**: M4-1 是发布安全的"宪法"——一旦 M4-2/3/4 各平台 publisher 实现，全部走 safe_publish 入口。三层防御 + INTENT 日志 + timeout 清理是防重复发帖（封号风险）的最后底线。