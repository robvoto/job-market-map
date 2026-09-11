#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${JMM_BROWSER_CDP_PORT:-9223}"
PYTHON="$ROOT/.venv/bin/python3"
PROFILE="$ROOT/data/playwright_jmm_seek_user_data"

CHROME="$($PYTHON - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    print(playwright.chromium.executable_path)
PY
)"
mkdir -p "$PROFILE"

while true; do
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
    about:blank || true
  echo "JMM Chrome exited; restarting same profile in 2 seconds." >&2
  sleep 2
done
