---
name: m4-2-x-publisher
description: MediaForge M4-2 X Publisher（OAuth2 + 链式 thread + LoginExpired）的设计要点与坑
metadata:
  type: project
---

MediaForge M4-2（X / Twitter Publisher）关键设计点（commit e1a2c82 + eval fix 70c7c7f）。

## 入口

- `pipeline/publishers/x_api.py::XApiPublisher` 是唯一平台实现
- 走 `safe_publish` 入口 → 三层防御（config/乐观锁/UNIQUE）由编排层守
- adapter 自身不写 publication.status，零状态机干预

## 凭据加载（HARD_PARTS §9）

- `secrets/x_<account>.json` 格式：`{"bearer_token": "AAAA..."}`
- `load_x_credentials(path)` 读 → `XApiPublisher(bearer_token=...)`
- 不硬编码路径，不 chmod（用户手动 chmod 600）
- 不日志 token（_httpx_post 只 log status code）

## thread 拆分（`split_thread`）

- 输入：M2-3 `output/<date>/<c_id>/x/thread.md`
- 每条匹配 `^(\d+)/(\d+)\s+(.*)$` 才视为有效
- 按空行分段；非编号段（标题/附录）宽容忽略
- 不校验编号顺序（"1/5, 10/5 混排"——属于弱校验，可接受）

## chain in_reply_to_tweet_id

- 第 1 条无 reply_to；后续每条 `in_reply_to_tweet_id = 上一条返回的 id`
- 中断后下一进程抢占 → safe_publish 乐观锁失败 → 不重发
- 配合 §1 决策 1，中断安全

## partial 信息（HARD_PARTS §1 决策 4）

mid-failure 时 PublishError 信息格式：
```
X API failed on tweet 3/5: <cause>; partial_published=[('id1', 'https://x.com/i/status/id1'), ('id2', '...')]; manual cleanup needed — visit each URL to delete the partial thread
```

- `_partial_msg(i, total, published, cause, kind=...)` 统一三处文案
- `_parse_post_id(...)` 把 dict → (id, url)；解析失败抛同样格式
- safe_publish 把 message 写 `publications.error`（截断 1000 字符）
- 编排层 + IM 通知要从 error 抽 URL 给人审——`manual cleanup` 关键字可作为 grep 钩子

## LoginExpired 子类（HARD_PARTS §2）

- `_httpx_post` 401/403 → `LoginExpired`（不是 `PublishError`）
- 编排层收到 LoginExpired 子类 → 停止该平台所有任务 + IM 告警
- 不带失效 bearer 反复撞（避免被 X 风控）
- XApiPublisher.publish 的 `except PublishError` 块要透传 LoginExpired：
  ```python
  except PublishError as e:
      if isinstance(e, LoginExpired):
          raise   # 不包外层
      raise PublishError(_partial_msg(...)) from e
  ```
- 这是 eval 评估的 must-fix #1（base.py 已定义 LoginExpired，契约要求）。

## 网络异常防护

- httpx.ConnectError / TimeoutException → PublishError
- resp.json() JSONDecodeError → PublishError
- 任何非 PublishError 的异常被 XApiPublisher 兜底为 partial PublishError

## registry（pipeline/publishers/__init__.py）

```python
_BUILDERS = {"x": _build_x}  # M4-3 加 toutiao/xiaohongshu

def get_adapter(platform, *, account, config) -> PublisherAdapter: ...
def build_adapters(cfg) -> dict[str, list[(AccountConfig, PublisherAdapter)]]: ...
```

- M4-3 接入：`_BUILDERS` 加一行 + `cmd_publish` 零改动
- `get_adapter` 抛 ValueError 含 supported list（cmd_publish 当 fail 处理）

## 双 sender 验证是否有用

可能需要在 M4-3 头条/小红书前考虑：
- 多账号矩阵（按 `account_id` 在 cfg.platforms.x.accounts 找 → M4-3 也会需要）
- 重发（safe_publish framework 不负责；只有 reset 命令能 failed→queued，M3-3 webui 已有 retry 按钮）

## X thread 长度上限

- 260 字符（X 上限 280，留 20 字符给 future 编辑）
- `TWEET_MAX_LEN = 260` 是 hard limit 写在 `validate` 里
- 超出 → Validate `issues` 含具体 positions + lengths

**Why**: M4-2 是 M4 阶段最简单平台（OAuth2 + REST），先跑通整个 safe_publish + registry 框架。LoginExpired + partial 信息是 X 平台独有的两个易踩坑——后续 M4-3 头条/小红书会复用同套 partial_msg 模式（Playwright 失败也需记录已发部分）。

**How to apply**: M4-3 头条/小红书接入时：① XApiPublisher 的 `_partial_msg` 抽出成 `publishers/partial.py` 模块复用（不同平台 URL 模板 + 小红书"已发某条但需撤稿"语义不同）；② LoginExpired 子类同样适用（cookie 失效也是 LoginExpired）；③ registry 加 builder 一行，cmd_publish 零改动。

[[m4-1-publish-safety]] [[m4-3-toutiao-xiaohongshu]]（未来）
