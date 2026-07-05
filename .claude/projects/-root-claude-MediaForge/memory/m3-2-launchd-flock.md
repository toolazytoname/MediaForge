---
name: m3-2-launchd-flock
description: MediaForge M3-2 launchd 定时化 + flock 跨进程锁的实施心得
metadata:
  type: project
---

MediaForge M3-2（launchd 定时化 + flock 锁）关键设计点与坑（commit 5c7dcdb）：

## 核心契约
- `pipeline/utils/flock.py` — 跨进程锁，fcntl LOCK_EX | LOCK_NB（非阻塞），拿不到立即抛 LockHeld
- 模块级 `_HELD: dict[str, IO]` 防同进程重复锁（再 acquire 同锁 → LockHeld）
- `release` 幂等：未持锁 noop 不抛错
- `_stage_lock(stage)` 装饰器：拿不到锁 → 打印 `SKIP` + return 0（cron 默默跳过）

## 关键设计要点

1. **跨进程 vs 同进程双层防御**：fcntl 处理跨进程，_HELD dict 处理同进程重复。两层独立，任一失败都抛 LockHeld。

2. **cron 重叠防护语义**：拿不到锁不报错（exit 0），因为"上一轮还没跑完"是常态，报警会刷屏。这与发布子命令的"必须错误退出"语义不同。

3. **launchd plist 的 XML 陷阱**：
   - 占位符不能用 `<PROJECT_ROOT>`（XML 标签冲突，解析失败）→ 用 `__PROJECT_ROOT__`
   - 注释里不能含 `--`（XML spec 禁止 `--` 在 `<!-- -->` 内）→ 改 `(notify)` 等
   - 程序化校验：`xml.etree.ElementTree.parse()` 能解析才能用

4. **时刻表分层**：
   - 整点任务用 `StartCalendarInterval`（Hour/Minute）
   - 周期任务（每 30 分钟）用 `StartInterval`（秒数）

5. **备份不能用 cp**：SQLite 在 WAL 模式下 cp 可能截断热页。用 `sqlite3 .backup` 命令保证一致性。`scripts/backup_db.sh` 保留 14 天。

6. **跨平台兼容**：flock 用 fcntl，Linux/macOS 通用；Windows 退化 noop（开发期可用，发布期禁止——已在 PR 注释里标注）。

## cmd_schedule 经验（ref M3-1）
- UNIQUE 冲突 → skipped（幂等成功），非 failed（exit 0 不误报）

## launchd plist 时刻表
```
06:00  ingest + score
06:30  create + gate
09:00  review --notify
10:00  schedule
*/30   publish --dry-run
23:00  collect
03:30  backup-db
```

**Why**: M3-2 是把流水线从"手动跑"变"无人值守"的关键。M3-1 之后的真实运行基础。
**How to apply**: M4+ 任何"自动化触发"场景（cron 触发的发布、cron 触发的 collect）都依赖此处的锁机制——flock 装饰器已包好，不用再加。