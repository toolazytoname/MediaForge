#!/usr/bin/env bash
# Single local/CI quality gate. No real platform credentials required.
# Stages print their name so a failure can be located immediately.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

stage() {
  echo
  echo "======== verify: $* ========"
}

fail() {
  echo "VERIFY FAIL [${1}]: ${2}" >&2
  exit 1
}

PYTHON="${PYTHON:-python3}"
NODE_MIN_MAJOR=20
PYTHON_MIN="3.11"

stage "tooling"
"$PYTHON" - <<'PY' || fail "tooling" "Python >= 3.11 required"
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"need Python >= 3.11, got {sys.version}")
print(f"python {sys.version.split()[0]}")
PY

if ! command -v node >/dev/null 2>&1; then
  fail "tooling" "node is required (see frontend/.nvmrc, Node >= 20)"
fi
if ! command -v npm >/dev/null 2>&1; then
  fail "tooling" "npm is required (ships with Node >= 20)"
fi
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt "$NODE_MIN_MAJOR" ]; then
  fail "tooling" "Node >= ${NODE_MIN_MAJOR} required, got $(node -v)"
fi
echo "node $(node -v)"
echo "npm $(npm -v)"

stage "backend-deps"
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q -r requirements.txt

stage "backend-tests"
python -m pytest tests -q --tb=short

stage "frontend-install"
(
  cd frontend
  if [ -f package-lock.json ]; then
    npm ci
  else
    npm install
  fi
)

stage "frontend-typecheck-build"
(
  cd frontend
  npm run build
)

stage "secret-scan"
python scripts/secret_scan.py

echo
echo "verify: all stages passed"
