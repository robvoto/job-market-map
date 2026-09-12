#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${JMM_APP_DIR:-/home/ubuntu/job-market-map}"
PERSIST_ROOT="${JMM_PERSIST_ROOT:-/var/lib/job-hunter/job-market-map}"
UV_BIN="${JMM_UV_BIN:-/home/ubuntu/.local/bin/uv}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (for example via AWS SSM or sudo)." >&2
  exit 2
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "JMM repo is missing at $APP_DIR" >&2
  exit 3
fi
if [[ ! -x "$UV_BIN" ]]; then
  echo "uv is missing at $UV_BIN" >&2
  exit 4
fi
if ! mountpoint -q /var/lib/job-hunter; then
  echo "/var/lib/job-hunter persistent EBS mount is required" >&2
  exit 5
fi

install -d -o ubuntu -g ubuntu "$PERSIST_ROOT" \
  "$PERSIST_ROOT/data" "$PERSIST_ROOT/backups" "$PERSIST_ROOT/logs" \
  "$PERSIST_ROOT/playwright-browsers"

for name in data backups logs; do
  target="$PERSIST_ROOT/$name"
  path="$APP_DIR/$name"
  if [[ -e "$path" && ! -L "$path" ]]; then
    if find "$path" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      echo "$path is a non-empty real directory; refusing to replace it" >&2
      exit 6
    fi
    rmdir "$path"
  fi
  ln -sfn "$target" "$path"
done

if [[ -e "$APP_DIR/.venv" && ! -L "$APP_DIR/.venv" ]]; then
  echo "$APP_DIR/.venv is a real path; refusing to replace it" >&2
  exit 7
fi

sudo -u ubuntu env \
  UV_PROJECT_ENVIRONMENT="$PERSIST_ROOT/venv" \
  PLAYWRIGHT_BROWSERS_PATH="$PERSIST_ROOT/playwright-browsers" \
  "$UV_BIN" sync --frozen --no-dev --project "$APP_DIR"
ln -sfn "$PERSIST_ROOT/venv" "$APP_DIR/.venv"

sudo -u ubuntu env \
  PLAYWRIGHT_BROWSERS_PATH="$PERSIST_ROOT/playwright-browsers" \
  "$PERSIST_ROOT/venv/bin/python" -m playwright install chromium

install -m 0644 "$APP_DIR/scripts/ec2/job-market-map.service" /etc/systemd/system/job-market-map.service
install -m 0644 "$APP_DIR/scripts/ec2/job-market-map-browser.service" /etc/systemd/system/job-market-map-browser.service
systemctl daemon-reload
systemctl enable job-market-map-browser.service job-market-map.service >/dev/null

echo "JMM_AWS_INSTALL_READY app=$APP_DIR persistent=$PERSIST_ROOT"
echo "Seed $PERSIST_ROOT/data/market.db before starting job-market-map.service."
