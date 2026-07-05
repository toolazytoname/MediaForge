#!/usr/bin/env bash
# M3-2 每日 state.db 备份（HARD_PARTS §9）
# 保留 14 天（HARD_PARTS §9 约定），备份到 backups/state-YYYY-MM-DD.db
# 用法：被 launchd com.mediaforge.backup-db.plist 每日 03:30 触发
# 也可手动跑：./scripts/backup_db.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/backups"
DB_FILE="$PROJECT_ROOT/state.db"
TODAY="$(date -u +%Y-%m-%d)"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_FILE" ]]; then
    echo "No state.db to backup at $DB_FILE" >&2
    exit 1
fi

# 用 SQLite .backup 命令保证一致性（不是直接 cp，避免 WAL 中间态）
DEST="$BACKUP_DIR/state-$TODAY.db"
sqlite3 "$DB_FILE" ".backup '$DEST'"
echo "backup: $DEST ($(stat -c %s "$DEST" 2>/dev/null || stat -f %z "$DEST") bytes)"

# 清理 14 天前的备份（HARD_PARTS §9）
find "$BACKUP_DIR" -name "state-*.db" -type f -mtime +14 -delete
echo "cleanup: kept last 14 days"