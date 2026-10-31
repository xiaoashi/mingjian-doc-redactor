#!/bin/zsh
set -eu
TASK_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$TASK_ROOT"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m mingjian "$@"
fi
echo '缺少开发环境。请优先打开 dist/明鉴.app；或按 README.md 安装依赖。'
read -r '?按回车关闭窗口'
