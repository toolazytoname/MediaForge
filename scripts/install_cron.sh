#!/usr/bin/env bash
# M3-2 cron 安装脚本（Linux/无 launchd 环境备选）
# 用法：./scripts/install_cron.sh [install|uninstall|show]
#
# 与 launchd 等价：相同 6 个时刻表，相同 flock 锁（每个子命令内部已加）。
# 不在 macOS 上推荐使用（launchd 会更稳）。

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="${PROJECT_ROOT%/}"
LOCKS_DIR="$PROJECT_ROOT/locks"
LOG_DIR="$PROJECT_ROOT/logs"
PY="$PROJECT_ROOT/.venv/bin/python"

# cron 字段：分 时 日 月 周
# 06:00 ingest+score; 06:30 create+gate; 09:00 review --notify;
# 10:00 schedule; */30 publish --dry-run; 23:00 collect
CRON_LINES=(
    "0 6 * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml ingest >> $LOG_DIR/cron.ingest.log 2>&1"
    "30 6 * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml create >> $LOG_DIR/cron.create.log 2>&1"
    "0 9 * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml review --notify >> $LOG_DIR/cron.review.log 2>&1"
    "0 10 * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml schedule >> $LOG_DIR/cron.schedule.log 2>&1"
    "*/30 * * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml publish --dry-run >> $LOG_DIR/cron.publish.log 2>&1"
    "0 23 * * * cd $PROJECT_ROOT && $PY -m pipeline.run --config config.yaml collect >> $LOG_DIR/cron.collect.log 2>&1"
    "30 3 * * * $PROJECT_ROOT/scripts/backup_db.sh >> $LOG_DIR/cron.backup.log 2>&1"
)

action="${1:-show}"

mkdir -p "$LOCKS_DIR" "$LOG_DIR"

case "$action" in
    install)
        # 用临时文件存 crontab，避免与已有 cron 冲突
        TMP="$(mktemp)"
        # 保留用户现有 cron（过滤掉旧的 mediaforge 行）
        crontab -l 2>/dev/null | grep -v '# mediaforge:' > "$TMP" || true
        echo "# mediaforge: managed by scripts/install_cron.sh" >> "$TMP"
        for line in "${CRON_LINES[@]}"; do
            echo "$line" >> "$TMP"
        done
        crontab "$TMP"
        rm -f "$TMP"
        echo "crontab installed. Verify: crontab -l | grep mediaforge"
        ;;
    uninstall)
        TMP="$(mktemp)"
        crontab -l 2>/dev/null | grep -v '# mediaforge:' | grep -v "$PROJECT_ROOT" > "$TMP" || true
        crontab "$TMP"
        rm -f "$TMP"
        echo "crontab uninstalled."
        ;;
    show)
        echo "Cron schedule (preview, not installed):"
        echo "  06:00  ingest"
        echo "  06:30  create"
        echo "  09:00  review --notify"
        echo "  10:00  schedule"
        echo "  */30   publish --dry-run"
        echo "  23:00  collect"
        echo "  03:30  backup_db.sh"
        echo ""
        echo "Run '$0 install' to install, '$0 uninstall' to remove."
        ;;
    *)
        echo "Usage: $0 [install|uninstall|show]" >&2
        exit 1
        ;;
esac