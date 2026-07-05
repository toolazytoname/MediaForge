"""CLI 入口（argparse 骨架 + 逐步实现）。

所有 TECH_SPEC §2 子命令已注册；M0-2 已填充 init-db（建表幂等），
其余子命令仍是占位，后续里程碑逐个填实现，argparse 形状不动。
"""
from __future__ import annotations

import argparse
import sys

from pipeline import db


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


def cmd_review(args: argparse.Namespace) -> int:
    return _not_implemented("review")


def cmd_schedule(args: argparse.Namespace) -> int:
    return _not_implemented("schedule")


def cmd_publish(args: argparse.Namespace) -> int:
    return _not_implemented("publish")


def cmd_collect(args: argparse.Namespace) -> int:
    return _not_implemented("collect")


def cmd_status(args: argparse.Namespace) -> int:
    return _not_implemented("status")


def cmd_reset(args: argparse.Namespace) -> int:
    """reset 是唯一允许的逆向操作，需接收位置参数 id + target status。
    M0-1 仅占位，M1 起接 db.reset_state() 实现。"""
    print(
        f"reset: id={args.id} -> {args.status}: "
        "not implemented (M0-1 placeholder)"
    )
    return 0


def cmd_webui(args: argparse.Namespace) -> int:
    return _not_implemented("webui")


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
