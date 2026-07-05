---
name: m3-3-webui
description: MediaForge M3-3 Web 控制台 v1 实施心得——FastAPI + Jinja2 + htmx 栈的关键陷阱
metadata:
  type: project
---

MediaForge M3-3（Web 控制台 v1）关键设计点与坑（commit ba7310b + eval 修复 3d353b4）：

## 核心契约
- FastAPI app factory `create_app()` + `main()` 入口走 uvicorn
- UI 不直接写 SQL——读走 db 查询函数，写走 db.transition() 状态机
- 错误统一 role=alert 片段；htmx swap='outerHTML' 局部刷新
- Pico.css v2 vendored 单文件（83KB，无 npm）；htmx 1.9.10 CDN

## 关键陷阱（绝对要避开）

1. **Jinja2 'unhashable type: dict' 缓存键错误**：Starlette 老 API `TemplateResponse(name, context)` 把 context 当模板名 hash。**必须用新 API**：
   ```python
   templates.TemplateResponse(request, "name.html", {"key": val})
   ```
   而不是：
   ```python
   templates.TemplateResponse("name.html", {"request": request, ...})
   ```

2. **POST Form vs JSON body**：endpoint 用 `Form(...)` 时，TestClient 必须用 `data=` 不用 `json=`。FastAPI 对 JSON body 不会自动 parse form fields，返回 422。

3. **测试 helper 必须 auto-create FK**：`_seed_content` 插入 contents 时 contents.topic_id 是 FK 到 topics.id，必须先 insert_topic。复用模式：
   ```python
   topic_id = "t_" + content_id.removeprefix("c_")
   _make_topic(conn, id=topic_id)
   db.insert_content(conn, ...)
   ```
   不要假设外层已建。

4. **publish 三重锁对 UI 天然生效**：retry 路由只调 `db.transition(failed → queued)`，不调真实 publish。发布由 `cmd_publish` 触发，且 publish.enabled=false 时整体阻断——UI 操作不会绕过。

5. **测试 _seed_topic 默认值冲突**：`_seed_topic(id="t_seed0001")` + `_seed_content()` 自动建 topic "t_seed0001" → UNIQUE content_hash 冲突。content 的 auto-topic 已覆盖，不要再调 _seed_topic。

## 单文件 ≤400 行硬约束（TECH_SPEC §10）
- 初版 app.py 449 行被 eval 标记**必须修复**
- 拆分策略：mdrender.py（_md_to_html + esc，52 行）+ sanitize.py（_sanitize_config，48 行）
- 11 个路由 try/finally conn.close() → with _db() as conn（_db 是 contextmanager）
- 移除 @app.on_event("startup") 弃用 API → /output 工厂时同步挂载
- 结果：app.py 缩到 371 行 ✓

## reschedule 状态守卫
- 初版用裸 UPDATE `WHERE id=?` 绕过 db.transition()
- 修：`WHERE id=? AND status='queued'` —— 仅 queued 可改时间，其他状态（published/publishing/failed/cancelled）→ alert 片段
- TECH_SPEC §4 没定义 reschedule 这条边，但保留 scheduled_at 字段在 queued 时可变是合理工程妥协

## 模块布局
- `pipeline/webui/app.py` — FastAPI app，~370 行（拆分后）
- `pipeline/webui/mdrender.py` — 极简 markdown→HTML（XSS 转义含单引号）
- `pipeline/webui/sanitize.py` — config 脱敏（白名单字段值替换 ***）
- `pipeline/webui/templates/` — base + 6 页面
- `pipeline/webui/static/pico.min.css` — vendored 单文件
- `tests/test_webui.py` — 17 测试（每个路由 + 状态机约束 + retry 不调 publish）

## cmd_webui 接线
- 启动：绑 config.webui.host:port（默认 127.0.0.1:8787）
- 与 launchd 流水线独立——UI 挂了 cron 照跑
- fastapi/uvicorn 未装时优雅降级（exit 1 + 清晰错误）

**Why**: M3-3 是"无人值守"完整闭环的最后一块——前 11 个里程碑都是命令行/CI 可测的，webui 把它们变成人类可点的图形。
**How to apply**: M4-4 webui v2（日历 + 设置页）直接复用本模式；任何"加 htmx 端点"的工作都先看本条目的关键陷阱。