# CLAUDE.md — MediaForge (self0704)

## 这个项目是什么

AI 自媒体矩阵全自动流水线：选题 → 创作 → 质量门禁 → 人审 → 多平台发布 → 数据回流。
Python 后端 + SQLite 状态机 + CLI 子命令 + cron 调度。发布端复用开源组件，本仓库只写编排层。

## 会话重启指引（READ THIS FIRST）

每次会话开始，按顺序读这三个文件再开工，**不要通读整个 codebase**：

1. `docs/TASKS.md` — 当前任务清单与恢复点，认领第一个 `[ ]` 未完成任务
2. `docs/TECH_SPEC.md` — 数据模型与接口契约（实现必须严格遵守，不得擅自改 schema）
3. `docs/HARD_PARTS.md` — 你要做的任务如果在这里有对应条目，先读完再动手

## 工作约定（强制）

1. **严格按 TASKS.md 顺序执行**，一次会话只做一个任务，做完勾选并在任务下方追加一行 `✅ 完成于 <日期>，commit <sha>，备注 <一句话>`
2. **接口契约不可变**：`pipeline/models.py` 的字段、`SourceAdapter`/`PublisherAdapter` 的方法签名、SQLite 表结构，都在 TECH_SPEC.md 里定死了。如果实现中发现契约有问题，**停下来在 TASKS.md 里记录问题**，不要擅自修改契约。
3. **TDD**：每个任务先写测试（TASKS.md 里已给出测试要点），RED → GREEN → 重构
4. **不可变数据**：函数返回新对象，不原地修改传入参数（遵守全局 coding-style 规则）
5. **每个任务完成即 commit**，格式 `feat: <任务编号> <描述>`，不留悬空状态
6. **凭据安全**：所有密钥/cookie 只放 `secrets/`（已 gitignore）和环境变量，代码里出现硬编码密钥 = 任务不合格
7. **不要越权发布**：`publish` 相关代码在 M4 之前只做 dry-run，真实发布需要 config 里 `publish.enabled: true` 且该平台在 `publish.allowed_platforms` 白名单中
8. **遇到卡点**：先查 `docs/HARD_PARTS.md` 对应章节；解决不了就在 TASKS.md 该任务下记录 `⚠️ BLOCKED: <原因>`，跳到下一个不依赖它的任务

## 常用命令

```bash
source .venv/bin/activate
python -m pipeline.run <stage>     # ingest|score|create|gate|review|schedule|publish|collect
python -m pytest tests/ -x -q      # 跑测试
python -m pipeline.run status      # 查看流水线各状态内容数量
```

## 目录速览

```
pipeline/
  run.py            # CLI 入口（argparse 子命令）
  models.py         # 数据模型（dataclass，冻结不可变）
  db.py             # SQLite 封装 + 状态机迁移
  sources/          # 选题数据源适配器（SourceAdapter 子类）
  topics/           # 选题评分与去重
  creators/         # 创作管道（调用 Claude / 视频生成）
  gate/             # 质量门禁（多轮批判+评分）
  review/           # 人审交互（生成审核清单/读取审核结果）
  publishers/       # 发布适配器（PublisherAdapter 子类）
  metrics/          # 数据回流
  utils/            # 日志、重试、限流等公共件
tests/              # pytest，镜像 pipeline/ 结构
output/             # 每日产出 output/YYYY-MM-DD/<content_id>/
secrets/            # 凭据（gitignored）
docs/               # 全部文档
```
