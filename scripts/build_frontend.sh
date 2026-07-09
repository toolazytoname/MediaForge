#!/usr/bin/env bash
# M10-9 一键构建 frontend SPA
#
# 用法：bash scripts/build_frontend.sh
#
# 行为：
#  1) cd frontend && npm ci（按 package-lock.json 装依赖）
#  2) npm run build（产物输出到 frontend/dist/，默认 commit 到仓库）
#
# 前置：Node 18+；前端目录 frontend/ 必须存在
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d frontend ]]; then
  echo "❌ frontend/ 不存在。先按 M10-7 跑 npm create vite@latest frontend -- --template vue-ts"
  exit 1
fi

cd frontend
echo "📦 npm ci..."
npm ci
echo "🔨 npm run build..."
npm run build
echo "✅ 产物输出到 frontend/dist/"
