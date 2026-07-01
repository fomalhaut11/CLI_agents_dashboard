#!/usr/bin/env bash
# 启动 Stream Dashboard (类 Unix)。浏览器打开 http://127.0.0.1:5111
# 幂等: 已在运行则只提示。Ctrl+C 停止。
set -e
cd "$(dirname "$0")"

PORT="${STREAM_DASHBOARD_PORT:-5111}"
PY="${PYTHON:-python3}"

if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Dashboard already running on http://127.0.0.1:$PORT"
  exit 0
fi

echo "Starting Stream Dashboard on http://127.0.0.1:$PORT ... (Ctrl+C to stop)"
exec "$PY" app.py
