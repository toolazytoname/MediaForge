"""CLI 入口（argparse 骨架，M0-1 占位实现）。

所有 TECH_SPEC §2 子命令已注册为占位函数，打印 'not implemented' 后退出 0。
后续里程碑（M0-2 起）逐个子命令填充实现，argparse 形状不动。
"""
from __future__ import annotations

import argparse
import sys


def _not_implemented(name: str) -> int:
    """M0-1 占位实现。所有子命令的兜底。"""
    print(f"{name}: not implemented (M0-1 placeholder)")
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    return _not_implemented("init-db")


def cmd_ingest(args: argparse.Namespace) -> int:
    return _not_implemented("ingest")


def cmd_score(args: argparse.Namespace) -> int:
    return _not_implemented("score")


def cmd_create(args: argparse.Namespace) -> int:
    return _not_implemented("create")


def cmd_gate(args: argparse.Namespace) -> int:
    return _not_implemented("gate")


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
