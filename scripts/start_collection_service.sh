#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="${JMM_COLLECTION_UNIT:-job-market-map-collection}"
PYTHON="$ROOT/.venv/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
  echo "JMM virtualenv is missing: $PYTHON" >&2
  exit 2
fi

if systemctl --user is-active --quiet "$UNIT.service"; then
  echo "JMM collection service is already running: $UNIT.service" >&2
  exit 3
fi

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
  "$PYTHON" -m scripts.run_collection_cycle "$@"

echo "Started $UNIT.service"
echo "Status: systemctl --user status $UNIT --no-pager"
echo "Logs:   journalctl --user -u $UNIT -f"
