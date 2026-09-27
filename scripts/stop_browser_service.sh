#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/browser-service.pid"
PROFILE="$ROOT/data/playwright_jmm_seek_user_data"

managed_runner() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  [[ "$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)" == "$ROOT" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -q 'scripts/run_browser_service.sh'
}

managed_chrome() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq -- "--user-data-dir=$PROFILE"
}

runner_pid=""
if [[ -f "$PID_FILE" ]]; then
  candidate="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$candidate" ]] && managed_runner "$candidate"; then
    runner_pid="$candidate"
  fi
fi
if [[ -z "$runner_pid" ]]; then
  while read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if managed_runner "$candidate"; then
      runner_pid="$candidate"
      break
    fi
  done < <(pgrep -f '[r]un_browser_service\.sh' || true)
fi

if [[ -n "$runner_pid" ]]; then
  kill -TERM "$runner_pid"
  for _ in $(seq 1 20); do
    kill -0 "$runner_pid" 2>/dev/null || break
    sleep 0.25
  done
fi

# Clean up Chromium from an older browser runner that did not yet own/terminate
# its child process. Only JMM's dedicated profile is eligible.
while read -r candidate; do
  [[ -n "$candidate" ]] || continue
  if managed_chrome "$candidate"; then
    kill -TERM "$candidate" 2>/dev/null || true
  fi
done < <(pgrep -f '[c]hrome.*playwright_jmm_seek_user_data' || true)

rm -f "$PID_FILE"
echo "JMM_BROWSER_SERVICE_STOPPED"
