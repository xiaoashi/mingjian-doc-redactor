#!/bin/zsh
set -eu
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT/frontend"
npm ci
npm run build
cd "$TASK_ROOT"
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python scripts/prepare_release.py
.venv/bin/python -m PyInstaller --noconfirm packaging/mingjian-web.spec
.venv/bin/python scripts/verify_web_bundle.py
