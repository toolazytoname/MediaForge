# MediaForge — AI 原生个人创作工作台

一个主题 → 一篇有依据的主稿 → 可独立编辑的平台版本 → 人工批准后的安全交付。

Python 后端 + SQLite 状态机 + CLI + Vue SPA。既有 pipeline 是可复用后台能力，不是创作者主导航。

## 文档

| 文档 | 用途 |
|------|------|
| [AGENTS.md](./AGENTS.md) | 会话恢复 + 工作约定 |
| [docs/PRODUCT_RESET_PLAN.md](./docs/PRODUCT_RESET_PLAN.md) | 产品方向与边界 |
| [docs/product-validation/2026-08-12-confirmed-product-definition.md](./docs/product-validation/2026-08-12-confirmed-product-definition.md) | 已确认产品定义 |
| [docs/product-validation/2026-08-12-confirmed-product-tasklist.md](./docs/product-validation/2026-08-12-confirmed-product-tasklist.md) | 阶段闸门与任务 |
| [docs/TECH_SPEC.md](./docs/TECH_SPEC.md) | 数据模型与接口契约 |
| [docs/HARD_PARTS.md](./docs/HARD_PARTS.md) | 难点与红线 |
| [docs/TASKS.md](./docs/TASKS.md) | 任务入口（历史见 archive） |

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml

python -m pipeline.run init-db
python -m pipeline.run webui   # http://127.0.0.1:8787

cd frontend && npm install && npm run build
```

真实发布需要 `publish.enabled: true` + 平台白名单 + 登录态。默认关闭。

## 分支

- `main` — 稳定线
- `develop` — 个人创作工作台开发线
