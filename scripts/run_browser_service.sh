#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${JMM_BROWSER_CDP_PORT:-9223}"
PYTHON="$ROOT/.venv/bin/python3"
PROFILE="$ROOT/data/playwright_jmm_seek_user_data"
LOCK="$ROOT/data/browser-service.lock"
OWNER_PID="${JMM_BROWSER_OWNER_PID:-}"
CHROME_PID=""

mkdir -p "$ROOT/data"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "JMM_BROWSER_SERVICE_ALREADY_RUNNING" >&2
  exit 3
fi

CHROME="$($PYTHON - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    print(playwright.chromium.executable_path)
PY
)"
mkdir -p "$PROFILE"

owner_active() {
  [[ -z "$OWNER_PID" ]] && return 0
  [[ -r "/proc/${OWNER_PID}/cmdline" ]] || return 1
  [[ "$(readlink -f "/proc/${OWNER_PID}/cwd" 2>/dev/null || true)" == "$ROOT" ]] || return 1
  tr '\0' ' ' < "/proc/${OWNER_PID}/cmdline" | grep -Eq 'scripts/start-api\.sh|uvicorn api\.main:app'
}

cleanup() {
  if [[ -n "$CHROME_PID" ]] && kill -0 "$CHROME_PID" 2>/dev/null; then
    kill -TERM "$CHROME_PID" 2>/dev/null || true
    wait "$CHROME_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

while true; do
  if ! owner_active; then
    echo "JMM browser owner exited; stopping browser service." >&2
    exit 0
  fi

  "$CHROME" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE" \
    --disable-blink-features=AutomationControlled \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-software-rasterizer \
    --no-first-run \
    --no-default-browser-check \
    about:blank &
  CHROME_PID=$!

  while kill -0 "$CHROME_PID" 2>/dev/null; do
    if ! owner_active; then
      echo "JMM browser owner exited; stopping Chromium." >&2
      kill -TERM "$CHROME_PID" 2>/dev/null || true
      wait "$CHROME_PID" 2>/dev/null || true
      CHROME_PID=""
      exit 0
    fi
    sleep 1
  done
  wait "$CHROME_PID" 2>/dev/null || true
  CHROME_PID=""
  echo "JMM Chrome exited; restarting same profile in 2 seconds." >&2
  sleep 2
done
