"""M10-1 webui 共享依赖层。

集中存放跨 `app.py`（legacy htmx）与未来 `api/*`（JSON router）共享的依赖
常量与工具函数：
  - DB 路径 / config 路径常量
  - DB 连接 contextmanager
  - config 加载封装（带异常 → (None, err) 转换）

所有模块级可 monkeypatch 的绑定（`_DB_PATH` / `_CONFIG_PATH` / `load_config`）
都集中到这里，测试只需 patch 单一来源 `pipeline.webui.deps.*`，避免
`app_mod._DB_PATH` / `api_mod._DB_PATH` 这种漂移。

历史背景：M3-3 → M7-R7-1 期间这些常量与 helper 都散落在 `app.py` 模块级，
htmx 路由全部直接引用 `app_mod._DB_PATH` 做 monkeypatch。M10 引入
`pipeline/webui/api/` 后，router 与 app 都从 deps 导入一份真相。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Tuple

from pipeline import db

# re-export 让 monkeypatch.setattr(deps, "load_config", stub) 仍能生效
from pipeline.config import load_config  # noqa: F401


# 模块级常量（测试用 monkeypatch.setattr 覆盖）。
# 默认值与 M3-3 时期一致：项目根相对路径。
_DB_PATH = "state.db"
_CONFIG_PATH = "./config.yaml"


def get_conn() -> sqlite3.Connection:
    """单次 db 连接（调用方负责 close）。"""
    return db.connect(_DB_PATH)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """conn 生命周期 contextmanager（自动 close；init 仅在 create_app 启动时一次）。

    R7-1 修复：不在此 init_db——每请求跑 DDL 是浪费，create_app 启动时
    已用一次性连接建表完毕。
    """
    c = get_conn()
    try:
        yield c
    finally:
        c.close()


def get_config() -> Tuple[Any, Any]:
    """Load config；异常返回 `(None, err_str)` 元组。

    调用方代码：
        cfg, err = deps.get_config()
        if err: ...

    `load_config` 走模块级名字，monkeypatch 仍生效。
    """
    try:
        return load_config(_CONFIG_PATH), None
    except Exception as e:
        return None, str(e)


__all__ = [
    "_DB_PATH",
    "_CONFIG_PATH",
    "get_conn",
    "_db",
    "get_config",
    "load_config",
]
