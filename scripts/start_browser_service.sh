#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="${JMM_BROWSER_UNIT:-job-market-map-browser}"
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

if systemctl --user is-active --quiet "$UNIT.service" 2>/dev/null; then
  echo "JMM browser service is active but CDP is not ready on port $PORT" >&2
  exit 3
fi

mkdir -p "$PROFILE" "$LOG_DIR"
DISPLAY_VALUE="${DISPLAY:-:0}"
WAYLAND_VALUE="${WAYLAND_DISPLAY:-wayland-0}"
RUNTIME_VALUE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if command -v systemd-run >/dev/null 2>&1 && systemd-run \
  --user \
  --unit="$UNIT" \
  --collect \
  --property="WorkingDirectory=$ROOT" \
  --setenv="DISPLAY=$DISPLAY_VALUE" \
  --setenv="WAYLAND_DISPLAY=$WAYLAND_VALUE" \
  --setenv="XDG_RUNTIME_DIR=$RUNTIME_VALUE" \
  --setenv="JMM_BROWSER_CDP_PORT=$PORT" \
  "$ROOT/scripts/run_browser_service.sh" >/dev/null 2>&1; then
  for _ in $(seq 1 40); do
    if cdp_ready; then
      echo "Started persistent JMM browser service: $UNIT.service"
      exit 0
    fi
    sleep 0.25
  done
  echo "JMM browser service started but CDP did not become ready on port $PORT" >&2
  exit 4
fi

echo "No systemd user bus; starting JMM browser directly in WSL."
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
