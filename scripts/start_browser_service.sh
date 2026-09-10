#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="${JMM_BROWSER_UNIT:-job-market-map-browser}"
PORT="${JMM_BROWSER_CDP_PORT:-9223}"
PYTHON="$ROOT/.venv/bin/python3"
PROFILE="$ROOT/data/playwright_jmm_seek_user_data"

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

if systemctl --user is-active --quiet "$UNIT.service"; then
  echo "JMM browser service is active but CDP is not ready on port $PORT" >&2
  exit 3
fi

CHROME="$($PYTHON - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    print(playwright.chromium.executable_path)
PY
)"

mkdir -p "$PROFILE"
DISPLAY_VALUE="${DISPLAY:-:0}"
WAYLAND_VALUE="${WAYLAND_DISPLAY:-wayland-0}"
RUNTIME_VALUE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

systemd-run \
  --user \
  --unit="$UNIT" \
  --collect \
  --property="WorkingDirectory=$ROOT" \
  --setenv="DISPLAY=$DISPLAY_VALUE" \
  --setenv="WAYLAND_DISPLAY=$WAYLAND_VALUE" \
  --setenv="XDG_RUNTIME_DIR=$RUNTIME_VALUE" \
  "$CHROME" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --disable-blink-features=AutomationControlled \
  --no-sandbox \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  about:blank >/dev/null

for _ in $(seq 1 40); do
  if cdp_ready; then
    echo "Started persistent JMM browser service: $UNIT.service"
    exit 0
  fi
  sleep 0.25
done

echo "JMM browser service started but CDP did not become ready on port $PORT" >&2
exit 4
