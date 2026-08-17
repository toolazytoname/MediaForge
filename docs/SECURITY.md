## 凭据安全边界

三类秘密不能混用，本仓库也不会在日志、API 或测试 fixture 里输出明文。

### 1. LLM / 图像 API key

- 存放：进程环境变量，或 Settings 写入的 `secrets/env.json`
- 写入：原子替换 + 文件权限 `0600`（`pipeline.env_keys.atomic_write_secret`）
- 用途：调用文本 / 图像模型。选择必须显式：`LLM_PROVIDER` 或 `config.yaml` 的 `llm.provider`。多个 key 同时存在且未指定时拒绝静默改选。
- `ANTHROPIC_API_KEY` 只路由到官方 Anthropic，不会落到 MiniMax。
- 对外只暴露 `mask()` 后的末 4 位。

### 2. 平台 OAuth / API token

- 存放：`secrets/x_<account>.json`、`secrets/wechat_mp_<account>.json`
- X 直发需要可验证的 user-context（`user_id` + `tweet.write` / `users.read`）。仅有 app-only bearer 时 `direct` 能力关闭，preview 仍可用。
- 微信公众号走官方草稿箱，能力是 draft/export，不是 direct publish。

### 3. 浏览器 cookie / storage_state

- 存放：`secrets/cookies/<platform>_<account>.json`
- 登录命令写入后 `chmod 600`
- 只用于 Playwright 平台（头条 / 抖音等），不能当作 LLM key 或 OAuth token 复用

`secrets/` 与 `config.yaml` 已 gitignore。本任务的 `verify` 质量门不读取、不索取真实凭据。
