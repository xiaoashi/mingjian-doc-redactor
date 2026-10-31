#!/bin/zsh
set -eu
TASK_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$TASK_ROOT"
echo '明鉴本地网页：http://127.0.0.1:8765'
echo '关闭本窗口或按 Ctrl+C 停止本机服务。普通文档在本机处理；OCR 发送前单独确认。'
exec .venv/bin/python -m mingjian.web.launcher "$@"
