#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${JMM_BROWSER_CDP_PORT:-9223}"
PYTHON="$ROOT/.venv/bin/python3"
PROFILE="$ROOT/data/playwright_jmm_seek_user_data"
LOG_DIR="$ROOT/logs"
PID_FILE="$ROOT/data/browser-service.pid"

if [[ ! -x "$PYTHON" ]]; then
  echo "JMM virtualenv is missing: $PYTHON" >&2
  exit 2
fi

cdp_ready() {
  "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
port=sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
}

if cdp_ready; then
  echo "Reusing existing JMM browser on CDP port $PORT"
  exit 0
fi

mkdir -p "$PROFILE" "$LOG_DIR"
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

rm -f "$PID_FILE"
nohup "$ROOT/scripts/run_browser_service.sh" \
  >>"$LOG_DIR/browser-service.log" 2>&1 </dev/null &
echo "$!" >"$PID_FILE"

for _ in $(seq 1 40); do
  if cdp_ready; then
    echo "Started persistent JMM browser directly (PID $(cat "$PID_FILE"))."
    exit 0
  fi
  sleep 0.25
done

echo "JMM browser service started but CDP did not become ready on port $PORT" >&2
exit 4
