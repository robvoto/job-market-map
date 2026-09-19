#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python3"
PID_FILE="$ROOT/data/collection-service.pid"
LOG_FILE="$ROOT/logs/collection-service.log"

if [[ ! -x "$PYTHON" ]]; then
  echo "JMM virtualenv is missing: $PYTHON" >&2
  exit 2
fi

mkdir -p "$ROOT/data" "$ROOT/logs"

managed_pid() {
  local pid="$1"
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)" == "$ROOT" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Eq 'scripts\.run_collection_cycle'
}

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null && managed_pid "$pid"; then
    echo "JMM collection is already running: pid=$pid" >&2
    exit 3
  fi
  rm -f "$PID_FILE"
fi

DISPLAY_VALUE="${DISPLAY:-:0}"
WAYLAND_VALUE="${WAYLAND_DISPLAY:-wayland-0}"
RUNTIME_VALUE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DISPLAY="$DISPLAY_VALUE"
export WAYLAND_DISPLAY="$WAYLAND_VALUE"
export XDG_RUNTIME_DIR="$RUNTIME_VALUE"

"$ROOT/scripts/start_browser_service.sh"

nohup "$PYTHON" -u -m scripts.run_collection_cycle "$@" \
  >>"$LOG_FILE" 2>&1 </dev/null &
pid="$!"
echo "$pid" > "$PID_FILE"

echo "Started JMM collection: pid=$pid"
echo "Status: ps -p $pid -o pid=,stat=,cmd="
echo "Logs:   tail -f $LOG_FILE"
