#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data logs backups
PID_FILE="data/api-service.pid"
PORT="$(uv run python - <<'PY'
from collector.settings import get_setting
print(int(get_setting('api.port')))
PY
)"
BASE="http://127.0.0.1:${PORT}"

health_ok() { curl -fsS "${BASE}/health" >/dev/null 2>&1; }
managed_pid() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Eq 'uvicorn api\.main:app|scripts/start-api\.sh'
}

case "${1:-status}" in
  start)
    if health_ok; then
      echo "JOB_MARKET_MAP_SERVICE_ALREADY_RUNNING ${BASE}/admin"
      exit 0
    fi
    nohup ./scripts/start-api.sh >>logs/api.log 2>&1 &
    pid=$!
    echo "$pid" > "$PID_FILE"
    for _ in $(seq 1 20); do
      if health_ok; then
        echo "JOB_MARKET_MAP_SERVICE_STARTED pid=${pid} admin=${BASE}/admin"
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
    if health_ok; then
      curl -fsS -X POST "${BASE}/v3/admin/collection/stop" >/dev/null 2>&1 || true
    fi
    if [[ ! -f "$PID_FILE" ]]; then
      echo "JOB_MARKET_MAP_SERVICE_PID_UNKNOWN; refusing to kill an unverified process" >&2
      exit 1
    fi
    pid="$(cat "$PID_FILE")"
    if ! managed_pid "$pid"; then
      echo "JOB_MARKET_MAP_SERVICE_PID_NOT_MANAGED pid=${pid}; refusing to signal" >&2
      exit 1
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
    if health_ok; then
      echo "JOB_MARKET_MAP_SERVICE_RUNNING admin=${BASE}/admin"
    else
      echo "JOB_MARKET_MAP_SERVICE_STOPPED"
      exit 1
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
