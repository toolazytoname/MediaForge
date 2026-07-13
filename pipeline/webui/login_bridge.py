"""U7-7 Web UI 一键登录后台编排桥接层。

职责：
  - 把 "一键登录" 这个动作从 HTTP 路由层完全抽象出来：accounts.py 只
    负责 platform 白名单 + 路由装饰器；mutex / 后台任务 / progress
    监听 / run registry 写入 / 错误分类全部在这层。
  - 进度通过 `login_cmd.add_progress_listener(cb)` 订阅（R7-7 已落地的
    listener API），回调签名 `cb(platform, account, msg)`；bridge 自己
    按 (platform, account) 闭包过滤，多 run 并发不串消息。
  - 成功 / PublishError / 其他异常分别写 runs registry 不同 status。

设计原则：
  - **不重写**登录业务逻辑（必须复用 `run_login`，U7-7 红线）
  - listener 仅在 execute_login_run 期间注册，finally 移除（避免泄漏 +
    多 run 并行时串消息——filter 仍在 cb 内）
  - 任何异常都被分类成三种 status（succeeded / failed-login / failed-internal），
    失败也保证 cleanup（finally + cleanup 由 start_login_background 负责）

历史背景：之前用 `logging.Handler` 监听 `pipeline.publishers.login` logger
但 `get_logger` 内部在 name 后加 `@<log_dir>` 后缀并 `propagate=False`，
导致外部 `logging.getLogger("pipeline.publishers.login")` 拿不到对应 logger，
handler 永远不触发。改用 listener API 后彻底解耦（不依赖 logger 命名规则）。

循环 import 处理：本模块依赖 `pipeline.webui.api.runs` 的 `register_run` /
`update_run_message`，而 `pipeline.webui.api.__init__.py` 又会 eager-import
`accounts.py`，`accounts.py` 还要 import 本模块——形成循环。解法：所有从
`api.runs` 的 import 都挪到函数体内部（懒加载），模块顶层只放跨包安全的
import（login_cmd / FastAPI / 标准库）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import BackgroundTasks

from pipeline.publishers.base import PublishError
from pipeline.publishers.login_cmd import (
    DEFAULT_COOKIES_DIR,
    add_progress_listener,
    remove_progress_listener,
    run_login,
)
from pipeline.webui.config_edit import add_account_to_config


# 模块级 logger（bridge 自身日志）
_BRIDGE_LOG = logging.getLogger("pipeline.webui.login_bridge")


# ── 同账号互斥：`(platform, account) -> run_id` ─────────────────
# BackgroundTask 完成后 `_LOGIN_RUNS.pop((platform, account))`（finally）。
# 放 bridge 而不是 accounts.py 是为了断开 accounts.py ↔ login_bridge.py
# 的循环 import 风险（见 HARD_PARTS/U7-7 决策：login 编排逻辑一律收敛到 bridge）。
_LOGIN_RUNS: dict[tuple[str, str], str] = {}


class LoginInProgressError(Exception):
    """同账号已有运行中的 login run（accounts.py 转 409）。"""

    def __init__(self, existing_run_id: str) -> None:
        super().__init__(
            f"login already running for this account; run_id={existing_run_id}"
        )
        self.existing_run_id = existing_run_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_progress_cb(
    run_id: str, platform: str, account: str,
):
    """生成一个过滤 (platform, account) 的 progress listener。

    listener 签名 `(platform, account, msg)`；这里闭包持有本次 run 的
    (platform, account)，不匹配时直接丢弃——多 run 并发时各 cb 只看到自己
    那个账号的进度消息。

    `update_run_message` 在 cb 内 lazy import：避免模块顶层 import
    `pipeline.webui.api.runs` 触发循环（见模块 docstring）。
    """
    from pipeline.webui.api.runs import update_run_message

    def _on_progress(
        event_platform: str, event_account: str | None, msg: str,
    ) -> None:
        if event_platform != platform:
            return
        if event_account is not None and event_account != account:
            return
        update_run_message(run_id, msg)

    return _on_progress


def execute_login_run(run_id: str, platform: str, account: str) -> None:
    """在 background task 内调 run_login 并把进度实时写入 runs registry。

    Args:
        run_id: `register_run` 创建的 run 标识。
        platform: toutiao / xiaohongshu / douyin（白名单校验由调用方负责）。
        account: 账号别名（如 "main"）。

    Side Effects:
        - `_RUNS[run_id]` 字段被多次更新（status 流转 + message）
        - listener 在本函数调用期间挂载，finally 移除（listener 不泄漏）
    """
    # 懒加载 api.runs：模块顶层 import 会触发循环（见模块 docstring）
    from pipeline.webui.api.runs import register_run
    # 1. queued → running（保留 platform / account / stage 便于前端轮询展示）
    register_run(
        run_id,
        status="running",
        started_at=_now_iso(),
        stage="login",
        platform=platform,
        account=account,
    )

    # 2. 注册 progress listener（finally 移除）
    cb = _make_progress_cb(run_id, platform, account)
    add_progress_listener(cb)
    _BRIDGE_LOG.debug(
        "login run started: run_id=%s platform=%s account=%s",
        run_id, platform, account,
    )

    try:
        path = run_login(platform, account)
        # 一键登录只存 cookie/凭据文件，从不 touch config.yaml——但账号数/
        # 健康度全读 config.yaml 声明的账号列表，登录成功后必须把账号登记
        # 回去，否则 UI 永远显示 0 个账号（用户实测反馈，见 U7-10）。
        # 幂等：已登记过 / platform 未在 config.yaml 配置块 都是安全 no-op。
        add_account_to_config(platform, account)
        register_run(
            run_id,
            status="succeeded",
            finished_at=_now_iso(),
            result={"path": str(path)},
            message="登录完成",
            stage="login",
            platform=platform,
            account=account,
        )
        _BRIDGE_LOG.info(
            "login run succeeded: run_id=%s path=%s", run_id, path,
        )
    except PublishError as e:
        register_run(
            run_id,
            status="failed",
            finished_at=_now_iso(),
            error={"code": "login_failed", "message": str(e)},
            message=str(e),
            stage="login",
            platform=platform,
            account=account,
        )
        _BRIDGE_LOG.warning(
            "login run failed (login error): run_id=%s err=%s", run_id, e,
        )
    except Exception as e:  # noqa: BLE001 — 不让后台任务静默死掉
        register_run(
            run_id,
            status="failed",
            finished_at=_now_iso(),
            error={"code": "internal_error", "message": repr(e)},
            message=f"internal error: {e!r}",
            stage="login",
            platform=platform,
            account=account,
        )
        _BRIDGE_LOG.error(
            "login run failed (internal): run_id=%s err=%r", run_id, e,
        )
    finally:
        remove_progress_listener(cb)


# ── accounts.py 调用的入口 ─────────────────────────────────────


def _run_login_then_cleanup(
    run_id: str, platform: str, account: str,
) -> None:
    """后台任务包装：跑 execute_login_run，finally 释放互斥锁。

    互斥释放必须放在 finally —— 即便 execute_login_run 自身异常，
    也保证 `_LOGIN_RUNS` 不留死锁记录，否则同账号永远 409。
    """
    try:
        execute_login_run(run_id, platform, account)
    finally:
        _LOGIN_RUNS.pop((platform, account), None)


def start_login_background(
    run_id: str,
    platform: str,
    account: str,
    background: BackgroundTasks,
) -> None:
    """accounts.py 调用：注册 mutex + 把后台任务塞进 BackgroundTasks。

    Raises:
        LoginInProgressError: 同账号已有运行中的 run；accounts.py 把它转 409。

    Side Effects:
        - `_LOGIN_RUNS[(platform, account)] = run_id`
        - register_run（status=queued）
        - background.add_task(_run_login_then_cleanup, ...)
    """
    # 懒加载 api.runs：模块顶层 import 会触发循环（见模块 docstring）
    from pipeline.webui.api.runs import register_run

    key = (platform, account)
    if key in _LOGIN_RUNS:
        raise LoginInProgressError(existing_run_id=_LOGIN_RUNS[key])

    _LOGIN_RUNS[key] = run_id
    register_run(
        run_id,
        status="queued",
        stage="login",
        platform=platform,
        account=account,
    )
    _BRIDGE_LOG.info(
        "login run queued: run_id=%s platform=%s account=%s",
        run_id, platform, account,
    )
    background.add_task(_run_login_then_cleanup, run_id, platform, account)


def new_login_run_id() -> str:
    """生成新 run_id（暴露给 accounts.py 用，避免它直接依赖 uuid）。"""
    return f"login_{uuid.uuid4().hex[:12]}"


# ── U7-8: 删除已保存的登录凭据 ───────────────────────────────


def is_login_in_progress(platform: str, account: str) -> str | None:
    """查询 (platform, account) 是否有运行中的 login run，有则返回其 run_id。

    accounts.py 的 DELETE 端点用它判断 409（正在登录时不允许删凭据，
    避免删除掉即将被写入的文件、或跟登录过程中的 cookie 写入产生竞态）。
    """
    return _LOGIN_RUNS.get((platform, account))


def delete_login_credentials(platform: str, account: str) -> bool:
    """删除 `secrets/cookies/<platform>_<account>.json` 凭据文件。

    只管文件，不碰 config.yaml——config.yaml 账号条目的移除由
    `pipeline.webui.config_edit.remove_account_from_config` 负责，两者在
    `accounts.py::delete_login` 里各自独立调用一次（U7-9：用户实际用过
    U7-8 后发现账号行不消失、只是标红，明确要求"彻底移除账号"，见
    TASKS.md）。

    Returns:
        True 如果文件存在并被删除；False 如果文件本就不存在（幂等，
        不视为错误——UI 上"删除"一个已经是未授权状态的账号应该正常返回）。
    """
    path = DEFAULT_COOKIES_DIR / f"{platform}_{account}.json"
    if not path.exists():
        return False
    path.unlink()
    _BRIDGE_LOG.info(
        "login credentials deleted: platform=%s account=%s path=%s",
        platform, account, path,
    )
    return True


__all__ = [
    "execute_login_run",
    "start_login_background",
    "new_login_run_id",
    "LoginInProgressError",
    "is_login_in_progress",
    "delete_login_credentials",
]