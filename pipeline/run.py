"""CLI 入口（argparse 骨架 + 逐步实现）。

所有 TECH_SPEC §2 子命令已注册；M0-2 已填充 init-db（建表幂等），
其余子命令仍是占位，后续里程碑逐个填实现，argparse 形状不动。

M3-2 新增：每个子命令包一层 stage_lock 装饰器（HARD_PARTS §8），
防止 cron/launchd 重叠执行。
"""
from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path

from pipeline import db
from pipeline.utils.flock import LockHeld, acquire, release


_LOCKS_DIR = Path("locks")


def _stage_lock(stage: str):
    """装饰器：子命令入口加 flock（HARD_PARTS §8 cron 重叠防护）。

    拿到锁 → 执行；拿不到 → 打印提示 + return 0（不报错，
    让 cron 默默跳过；上一轮还没跑完是常态）。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(args: argparse.Namespace) -> int:
            lock_path = _LOCKS_DIR / f"{stage}.lock"
            try:
                acquire(lock_path)
            except LockHeld as e:
                print(f"{stage}: SKIP (lock held: {e})")
                return 0
            try:
                return fn(args)
            finally:
                release(lock_path)
        return wrapped
    return deco


def _not_implemented(name: str) -> int:
    """占位实现。M0-2 起逐个替换。"""
    print(f"{name}: not implemented (M0-1 placeholder)")
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    """建表（幂等）。默认 ./state.db；--config 提供的路径不影响 db 位置。

    M0-2 实现：open → init_db → close。多次调用无副作用（HARD_PARTS §5）。
    """
    conn = db.connect("state.db")
    try:
        db.init_db(conn)
    finally:
        conn.close()
    print("init-db: state.db ready")
    return 0


@_stage_lock("ingest")
def cmd_ingest(args: argparse.Namespace) -> int:
    """拉取所有启用的数据源 → 入库 → 去重（HARD_PARTS §5/§8）。

    单源失败不阻断（log warning + 跳过）。失败源不视为致命错误，
    exit code 仍为 0（M0-1 既有约定："有失败项但流程完成 exit 0"）。
    """
    from datetime import datetime, timezone

    from pipeline.config import load_config
    from pipeline.creators import llm as llm_mod
    from pipeline.ingest import run_ingest
    from pipeline.sources.registry import build_sources

    cfg = load_config(args.config)
    sources = build_sources(cfg.sources)
    if not sources:
        print("ingest: no enabled sources (check config.sources)")
        return 0

    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        llm_mod.setup_provider_from_env()  # 兼容真实 LLM 冒烟（M2-2+）
        now = datetime.now(timezone.utc).isoformat()
        result = run_ingest(conn, sources, now=now)
    finally:
        conn.close()

    # 部分或全部源失败 → exit 0（流程完成），但向运维发出信号
    return 1 if result.failed_sources else 0


@_stage_lock("score")
def cmd_score(args: argparse.Namespace) -> int:
    """给 raw topics 评分 + 选当日 selected。

    单条解析失败 / LLM 异常 → 该条 rejected，不阻断其他条（HARD_PARTS §5）。
    """
    from datetime import datetime, timezone

    from pipeline.config import load_config
    from pipeline.creators import llm as llm_mod
    from pipeline.topics.runner import score_all

    cfg = load_config(args.config)
    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        llm_mod.setup_provider_from_env()
        now = datetime.now(timezone.utc).isoformat()
        result = score_all(
            conn,
            pillars=cfg.pillars,
            quota=cfg.topics.daily_quota,
            min_score=cfg.topics.min_score,
            now=now,
        )
    finally:
        conn.close()

    print(
        f"score: {result.processed} processed, "
        f"{result.selected} selected, {result.rejected} rejected"
    )
    return 0


@_stage_lock("create")
def cmd_create(args: argparse.Namespace) -> int:
    """为 selected topics 生成 canonical 长文（M2-1）。

    单条 CreateError → 跳过该 topic、继续下一条；
    BudgetExceeded / 其他系统性错误 → 立即抛出退出。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from pipeline.config import load_config
    from pipeline.creators import llm as llm_mod
    from pipeline.creators.canonical import create_one
    from pipeline.models import TopicStatus
    from pipeline.utils.errors import BudgetExceeded, CreateError

    cfg = load_config(args.config)
    output_root = Path(getattr(args, "output", None) or "output")

    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        llm_mod.setup_provider_from_env()
        llm_mod.init_db_conn(conn)
        selected = db.get_topics_by_status(conn, TopicStatus.SELECTED.value)
        now = datetime.now(timezone.utc).isoformat()

        ok = 0
        failed = 0
        for topic in selected:
            try:
                content = create_one(
                    conn, topic, pillars=cfg.pillars,
                    output_root=output_root, now=now,
                )
                print(f"create: {topic.id} → {content.id} ({content.canonical_path})")
                ok += 1
            except CreateError as e:
                # 单条失败 → 跳过、继续
                print(
                    f"create: WARN topic={topic.id} skipped: {e}",
                    file=sys.stderr,
                )
                failed += 1
            except BudgetExceeded:
                # 系统性 → 终止整批
                raise
    finally:
        conn.close()

    print(f"create: {ok} ok, {failed} failed")
    return 0


@_stage_lock("gate")
def cmd_gate(args: argparse.Namespace) -> int:
    """质量门禁：draft → gated/discarded（M2-2）。

    单条 GateError/CreateError → skip 该条 + log warning；
    BudgetExceeded → 终止整批。
    """
    import sys
    from datetime import datetime, timezone

    from pipeline.config import load_config
    from pipeline.creators import llm as llm_mod
    from pipeline.gate import run_gate

    cfg = load_config(args.config)

    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        llm_mod.setup_provider_from_env()
        llm_mod.init_db_conn(conn)
        now = datetime.now(timezone.utc).isoformat()
        result = run_gate(conn, gate_cfg=cfg.gate, now=now)
    finally:
        conn.close()

    print(
        f"gate: {result.processed} processed, "
        f"{result.gated_count} gated, "
        f"{result.discarded_count} discarded, "
        f"{result.failed_count} failed"
    )
    # 失败条目细节打 stderr，便于运维定位
    for o in result.outcomes:
        if o.final_status in ("failed", "discarded"):
            print(
                f"  {o.final_status}: {o.content_id} — {o.reason}",
                file=sys.stderr,
            )
    return 0


@_stage_lock("review")
def cmd_review(args: argparse.Namespace) -> int:
    """生成/读取审核清单（M2-5）。

    流程（ARCHITECTURE §3.5 + HARD_PARTS §5）：
      1. 读旧 REVIEW.md → 应用人标记的 approved/rejected_by_human（幂等）
      2. 重新生成当日 REVIEW.md（基于当前 gated 状态）
      3. --notify 时：当日有 gated 才推 IM（webhook_url 为空则跳过）
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from pipeline.config import load_config
    from pipeline.review import run_review

    cfg = load_config(args.config)
    output_root = Path("output")
    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        now = datetime.now(timezone.utc).isoformat()
        result = run_review(
            conn,
            date_str=now[:10],
            output_root=output_root,
            now_iso=now,
            webhook_url=cfg.notify.webhook_url if args.notify else None,
        )
    finally:
        conn.close()

    print(
        f"review: {result.generated} generated, "
        f"{result.applied} approved, {result.rejected} rejected"
    )
    return 0


@_stage_lock("derivative")
def cmd_derivative(args: argparse.Namespace) -> int:
    """派生平台格式（M2-3 一料多吃）。

    输入：gated content（已通过门禁）
    输出：每平台目录（toutiao.md / xiaohongshu/{slides.json,caption.md,tags.txt} / x/thread.md）
    副作用：contents.formats 字段更新为本次派生成功的平台列表

    单条 CreateError → skip 该条 + log warning；
    BudgetExceeded → 终止整批。
    """
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from pipeline.config import load_config
    from pipeline.creators import llm as llm_mod
    from pipeline.creators.derivative import run_derivative

    cfg = load_config(args.config)
    output_root = Path(getattr(args, "output", None) or "output")

    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        llm_mod.setup_provider_from_env()
        llm_mod.init_db_conn(conn)
        now = datetime.now(timezone.utc).isoformat()
        results = run_derivative(conn, output_root=output_root, now=now)
    finally:
        conn.close()

    ok = sum(1 for r in results if not r.failed_platforms)
    partial = sum(
        1 for r in results
        if r.failed_platforms and len(r.failed_platforms) < 3
    )
    failed = sum(1 for r in results if len(r.failed_platforms) == 3)

    print(f"derivative: {len(results)} processed, {ok} ok, {partial} partial, {failed} failed")
    for r in results:
        if r.failed_platforms:
            print(
                f"  {r.content_id}: failed platforms = {r.failed_platforms}",
                file=sys.stderr,
            )
    return 0


@_stage_lock("schedule")
def cmd_schedule(args: argparse.Namespace) -> int:
    """为 approved 内容排期（M3-1）。

    流程（ARCHITECTURE §3.6 + HARD_PARTS §8）：
      1. 读 approved 内容
      2. 读已有 publications（含同平台已排期，约束新排期不冲突）
      3. 调 scheduler.plan() 计算新增排期（纯函数）
      4. db.insert_publication 落库（UNIQUE(content_id, platform, account_id) 防重）
    """
    import sys
    from datetime import datetime, timezone

    from pipeline.config import load_config
    from pipeline.models import ContentStatus, PublicationStatus
    from pipeline.scheduler import plan

    cfg = load_config(args.config)
    conn = db.connect("state.db")
    try:
        db.init_db(conn)
        now = datetime.now(timezone.utc).isoformat()

        approved = db.get_contents_by_status(conn, ContentStatus.APPROVED.value)
        # 已有排期（含所有 status —— schedule 只新增，不改变已存在时间）
        existing = []
        for st in PublicationStatus:
            existing.extend(
                db.get_publications_by_status(conn, st.value)
            )

        # platforms 配置 dict：{"x": PlatformAPI, "toutiao": PlatformPlaywright, ...}
        platforms_cfg = {
            name: getattr(cfg.platforms, name)
            for name in cfg.platforms.model_dump()
        }

        result = plan(
            approved_contents=approved,
            platform_configs=platforms_cfg,
            existing_publications=existing,
            now_iso=now,
            min_gap_hours=cfg.publish.min_gap_hours,
            cross_platform_gap_minutes=cfg.publish.cross_platform_gap_minutes,
            tz_name=cfg.timezone,
        )

        # 落库（UNIQUE 约束兜底；幂等：已有排期 → skip，非阻断）
        import sqlite3 as _sqlite3
        scheduled = 0
        skipped = 0
        failed = 0
        for pub in result.publications:
            try:
                db.insert_publication(conn, pub)
                scheduled += 1
            except _sqlite3.IntegrityError as e:
                # UNIQUE(content_id, platform, account_id) 命中 → 幂等成功
                skipped += 1
            except Exception as e:
                print(
                    f"schedule: WARN insert failed pub={pub.id}: {e}",
                    file=sys.stderr,
                )
                failed += 1
    finally:
        conn.close()

    print(
        f"schedule: {scheduled} scheduled, "
        f"{skipped} skipped (already exists), {failed} failed"
    )
    return 0 if failed == 0 else 1


@_stage_lock("publish")
def cmd_publish(args: argparse.Namespace) -> int:
    """发布到期的 publication（M4-1 安全框架）。

    流程（HARD_PARTS §1 + §9）：
      1. timeout_publishings() 清理超时 publishing 记录
      2. 取 queued + scheduled_at <= now 的 publications
      3. 每个走 safe_publish()——三层防御（config/乐观锁/UNIQUE）+ INTENT 日志

    M4-1 不实现具体平台 publisher（X/头条/小红书由 M4-2/3 实现）；
    当前所有 publication 都会被 safe_publish 拒绝（无 adapter 注册）。
    --dry-run 模式全流程走 INTENT 日志但不真发。
    """
    import sys
    from datetime import datetime, timezone

    from pipeline.config import load_config
    from pipeline.models import PublicationStatus
    from pipeline.publishers.safe_publish import (
        safe_publish,
        timeout_publishings,
    )

    cfg = load_config(args.config)
    dry_run = bool(getattr(args, "dry_run", False))
    now = datetime.now(timezone.utc).isoformat()

    conn = db.connect("state.db")
    try:
        db.init_db(conn)

        # 1. 超时清理
        timeouted = timeout_publishings(
            conn, timeout_minutes=30, now_iso=now,
        )
        if timeouted:
            print(f"publish: WARN {timeouted} publishing(s) timed out → failed")

        # 2. 取候选（queued + 已到期）
        queued = db.get_publications_by_status(
            conn, PublicationStatus.QUEUED.value,
        )

        # 3. 遍历（每个 publication 暂无具体 adapter → 走 config 校验拒绝）
        # M4-2/3 接入具体 publisher 后这里会按 platform 分发 adapter
        published = 0
        skipped = 0
        failed = 0
        for pub in queued:
            # 取首个账号（多账号由 M4-2+ 扩展）
            account_id = pub.account_id
            # M4-1 无具体 adapter：先校验 enabled/allowed_platforms 路径
            if not cfg.publish.enabled:
                skipped += 1
                continue
            if (cfg.publish.allowed_platforms
                    and pub.platform not in cfg.publish.allowed_platforms):
                print(
                    f"publish: SKIP {pub.id} platform={pub.platform!r} "
                    f"not in allowed_platforms",
                    file=sys.stderr,
                )
                skipped += 1
                continue
            # 具体平台 adapter 暂未实现 —— M4-1 安全框架通过 safe_publish
            # 校验后由 M4-2 (X) / M4-3 (头条/小红书) 接入具体 publisher
            # 当前阶段无 adapter 直接跳过（不在本任务范围）
            print(
                f"publish: {pub.id} platform={pub.platform} "
                f"— adapter 未注册（M4-2/3 接入）",
                file=sys.stderr,
            )
            skipped += 1
    finally:
        conn.close()

    print(
        f"publish: {published} published, "
        f"{skipped} skipped, {failed} failed"
    )
    return 0 if failed == 0 else 1


@_stage_lock("collect")
def cmd_collect(args: argparse.Namespace) -> int:
    return _not_implemented("collect")


@_stage_lock("status")
def cmd_status(args: argparse.Namespace) -> int:
    return _not_implemented("status")


@_stage_lock("reset")
def cmd_reset(args: argparse.Namespace) -> int:
    """reset 是唯一允许的逆向操作，需接收位置参数 id + target status。
    M0-1 仅占位，M1 起接 db.reset_state() 实现。"""
    print(
        f"reset: id={args.id} -> {args.status}: "
        "not implemented (M0-1 placeholder)"
    )
    return 0


def cmd_webui(args: argparse.Namespace) -> int:
    """启动本地 Web 控制台（M3-3）。

    绑定 config.webui.host:port（默认 127.0.0.1:8787）。
    与 launchd 流水线独立——UI 挂了 cron 照跑。
    """
    try:
        from pipeline.webui.app import main as webui_main
    except ImportError as e:
        print(f"webui: fastapi/uvicorn not installed: {e}")
        return 1
    return webui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description="MediaForge pipeline CLI",
    )
    parser.add_argument(
        "--config",
        default="./config.yaml",
        help="配置文件路径 (默认 ./config.yaml)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="启用 DEBUG 日志"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="建表（幂等）")
    sub.add_parser("ingest", help="拉取所有启用的数据源")
    sub.add_parser(
        "score", help="给 raw topics 评分并选出当日 selected"
    )
    sub.add_parser("create", help="为 selected topics 生成内容")
    sub.add_parser("gate", help="质量门禁")
    sub.add_parser(
        "derivative", help="派生平台格式（gated → toutiao/xiaohongshu/x）"
    )
    review_p = sub.add_parser("review", help="生成/读取审核清单")
    review_p.add_argument(
        "--notify", action="store_true", help="通过 webhook 通知"
    )
    sub.add_parser("schedule", help="为 approved 内容排期")
    publish_p = sub.add_parser("publish", help="发布到期的 publication")
    publish_p.add_argument(
        "--dry-run", action="store_true", help="只走流程，不实际发布"
    )
    sub.add_parser("collect", help="回流表现数据")
    sub.add_parser("status", help="打印各状态计数表")

    reset_p = sub.add_parser(
        "reset", help="人工重置状态（唯一允许的逆向操作）"
    )
    reset_p.add_argument("id", help="记录 id")
    reset_p.add_argument("status", help="目标状态")

    sub.add_parser(
        "webui", help="启动本地 Web 控制台（默认 127.0.0.1:8787）"
    )

    return parser


COMMANDS = {
    "init-db": cmd_init_db,
    "ingest": cmd_ingest,
    "score": cmd_score,
    "create": cmd_create,
    "gate": cmd_gate,
    "derivative": cmd_derivative,
    "review": cmd_review,
    "schedule": cmd_schedule,
    "publish": cmd_publish,
    "collect": cmd_collect,
    "status": cmd_status,
    "reset": cmd_reset,
    "webui": cmd_webui,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
