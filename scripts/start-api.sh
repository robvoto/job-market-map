#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
mkdir -p data logs backups
exec 9>data/api-service.lock
if ! flock -n 9; then
  echo "JOB_MARKET_MAP_API_ALREADY_RUNNING"
  exit 0
fi

# Keep the foreground operator terminal as the live console for the whole local
# JMM service. Collection subprocesses inherit these descriptors, so their
# progress appears here as well as in the durable logs. The detached service
# launcher already redirects this script to logs/api.log, so do not add a
# second tee in that mode.
if [[ -t 1 ]]; then
  exec > >(tee -a "$ROOT/logs/api.log") 2>&1
fi

export JOB_MARKET_MAP_SCHEDULER_SERVICE=1
PORT="$(uv run python - <<'PY'
from collector.settings import get_setting
print(int(get_setting('api.port')))
PY
)"
"$ROOT/scripts/start_browser_service.sh"
echo "Job Market Map API/Admin starting on http://127.0.0.1:${PORT}/admin"
echo "In-app overnight scheduler enabled by service mode; schedule is controlled from Admin."
exec uv run uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
