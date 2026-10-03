#!/usr/bin/env bash
# macOS / Linux 用の開発起動（モックモード）。本番は Windows サービス（scripts/install.ps1）。
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt; }
export JENKINS_MOCK="${JENKINS_MOCK:-true}"
export APP_DATA_DIR="${APP_DATA_DIR:-$PWD/var}"
[ -f seed.yaml ] && export SEED_FILE="${SEED_FILE:-seed.yaml}"
.venv/bin/python -m app --migrate-only
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8080}" --reload --reload-dir app --reload-dir static --workers 1
