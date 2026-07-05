#!/usr/bin/env bash
# M3-2 launchd plist 安装脚本（macOS）
# 用法：
#   ./scripts/install_launchd.sh          # 安装全部
#   ./scripts/install_launchd.sh uninstall # 卸载全部
#
# 幂等：可重复运行；卸载后再装无残留。
# 设计：
#   - plist 模板在 launchd/ 下，路径含 <PROJECT_ROOT> 占位符
#   - 本脚本运行时 sed 替换为当前项目根绝对路径
#   - 复制到 ~/Library/LaunchAgents/ 下，launchctl load 启用

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHD_DIR="$PROJECT_ROOT/launchd"
AGENTS_DIR="${HOME}/Library/LaunchAgents"

PLISTS=(
    "com.mediaforge.ingest-score.plist"
    "com.mediaforge.create-gate.plist"
    "com.mediaforge.review-notify.plist"
    "com.mediaforge.schedule.plist"
    "com.mediaforge.publish-due.plist"
    "com.mediaforge.collect.plist"
    "com.mediaforge.backup-db.plist"
)

# Unix 化路径（去除可能存在的尾部 /）
PROJECT_ROOT="${PROJECT_ROOT%/}"

action="${1:-install}"

case "$action" in
    install)
        mkdir -p "$AGENTS_DIR"
        mkdir -p "$PROJECT_ROOT/logs"
        mkdir -p "$PROJECT_ROOT/locks"
        for p in "${PLISTS[@]}"; do
            src="$LAUNCHD_DIR/$p"
            dst="$AGENTS_DIR/$p"
            if [[ ! -f "$src" ]]; then
                echo "MISSING template: $src" >&2
                exit 1
            fi
            # 替换 __PROJECT_ROOT__ 为当前项目根（避免与 XML 标签冲突）
            sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$src" > "$dst"
            # launchctl unload 旧的（如有）→ 再 load（幂等）
            launchctl unload "$dst" 2>/dev/null || true
            launchctl load "$dst"
            echo "loaded: $p"
        done
        echo "All plists loaded. Verify: launchctl list | grep mediaforge"
        ;;
    uninstall)
        for p in "${PLISTS[@]}"; do
            dst="$AGENTS_DIR/$p"
            if [[ -f "$dst" ]]; then
                launchctl unload "$dst" 2>/dev/null || true
                rm -f "$dst"
                echo "unloaded: $p"
            fi
        done
        echo "All plists unloaded."
        ;;
    *)
        echo "Usage: $0 [install|uninstall]" >&2
        exit 1
        ;;
esac