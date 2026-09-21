#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data logs backups
PID_FILE="data/api-service.pid"
ROOT="$(pwd)"

configured_port() {
  # Read api.port without importing collector.settings/get_setting. That path
  # calls init_db(), which may need a write lock and must never be required to
  # inspect or stop an already-running service.
  uv run python - <<'PY'
import json
import sqlite3
from pathlib import Path

root = Path.cwd()
catalog = json.loads((root / "config" / "settings_catalog.json").read_text(encoding="utf-8"))
spec = next(row for row in catalog["settings"] if row["key"] == "api.port")
port = int(spec["default"])
db_path = root / "data" / "market.db"
if db_path.exists():
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        if table:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key='api.port'"
            ).fetchone()
            if row:
                port = int(json.loads(row[0]))
    finally:
        conn.close()
print(port)
PY
}

managed_pid() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  [[ "$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)" == "$ROOT" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Eq 'uvicorn api\.main:app|scripts/start-api\.sh'
}

port_from_pid() {
  local pid="$1"
  local cmd port
  managed_pid "$pid" || return 1
  cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline")"
  port="$(printf '%s\n' "$cmd" | sed -nE 's/.* --port ([0-9]+)( |$).*/\1/p')"
  [[ -n "$port" ]] || return 1
  printf '%s\n' "$port"
}

find_managed_api_pid() {
  local pid
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    if managed_pid "$pid"; then
      echo "$pid"
      return 0
    fi
  done < <(pgrep -f '[u]v run uvicorn api\.main:app --host 127\.0\.0\.1 --port [0-9]+' || true)
  return 1
}

adopt_running_api_pid() {
  local pid
  pid="$(find_managed_api_pid)" || return 1
  echo "$pid" > "$PID_FILE"
  echo "$pid"
}

health_ok() {
  local port="$1"
  curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1
}

case "${1:-status}" in
  start)
    if pid="$(find_managed_api_pid)"; then
      port="$(port_from_pid "$pid")" || {
        echo "JOB_MARKET_MAP_SERVICE_PORT_UNKNOWN pid=${pid}" >&2
        exit 1
      }
      if health_ok "$port"; then
        echo "$pid" > "$PID_FILE"
        echo "JOB_MARKET_MAP_SERVICE_ALREADY_RUNNING pid=${pid} http://127.0.0.1:${port}/admin"
        exit 0
      fi
      echo "JOB_MARKET_MAP_SERVICE_RUNNING_UNHEALTHY pid=${pid}; refusing to launch a duplicate" >&2
      exit 1
    fi

    port="$(configured_port)"
    base="http://127.0.0.1:${port}"
    nohup ./scripts/start-api.sh >>logs/api.log 2>&1 &
    pid=$!
    echo "$pid" > "$PID_FILE"
    for _ in $(seq 1 20); do
      if health_ok "$port"; then
        echo "JOB_MARKET_MAP_SERVICE_STARTED pid=${pid} admin=${base}/admin"
        exit 0
      fi
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.25
    done
    echo "JOB_MARKET_MAP_SERVICE_FAILED; see logs/api.log" >&2
    exit 1
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      pid="$(cat "$PID_FILE")"
    else
      pid=""
    fi

    if [[ -z "$pid" ]] || ! managed_pid "$pid"; then
      stale_pid="$pid"
      if pid="$(adopt_running_api_pid)"; then
        if [[ -n "$stale_pid" ]]; then
          echo "JOB_MARKET_MAP_SERVICE_PID_REPAIRED stale=${stale_pid} active=${pid}"
        else
          echo "JOB_MARKET_MAP_SERVICE_PID_ADOPTED active=${pid}"
        fi
      else
        echo "JOB_MARKET_MAP_SERVICE_PID_UNKNOWN; refusing to kill an unverified process" >&2
        exit 1
      fi
    fi

    port="$(port_from_pid "$pid")" || {
      echo "JOB_MARKET_MAP_SERVICE_PORT_UNKNOWN pid=${pid}; refusing to signal" >&2
      exit 1
    }
    base="http://127.0.0.1:${port}"
    if health_ok "$port"; then
      curl -fsS -X POST "${base}/v3/admin/collection/stop" >/dev/null 2>&1 || true
    fi
    kill -TERM "$pid"
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    rm -f "$PID_FILE"
    echo "JOB_MARKET_MAP_SERVICE_STOPPED"
    ;;
  status)
    if pid="$(find_managed_api_pid)"; then
      port="$(port_from_pid "$pid")" || {
        echo "JOB_MARKET_MAP_SERVICE_PORT_UNKNOWN pid=${pid}" >&2
        exit 1
      }
      if health_ok "$port"; then
        echo "JOB_MARKET_MAP_SERVICE_RUNNING admin=http://127.0.0.1:${port}/admin"
        exit 0
      fi
      echo "JOB_MARKET_MAP_SERVICE_RUNNING_UNHEALTHY pid=${pid}"
      exit 1
    fi

    port="$(configured_port)"
    if health_ok "$port"; then
      echo "JOB_MARKET_MAP_SERVICE_RUNNING_UNMANAGED; refusing to claim ownership" >&2
      exit 1
    fi
    echo "JOB_MARKET_MAP_SERVICE_STOPPED"
    exit 1
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
