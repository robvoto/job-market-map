#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data logs backups
exec 9>data/api-service.lock
if ! flock -n 9; then
  echo "JOB_MARKET_MAP_API_ALREADY_RUNNING"
  exit 0
fi
export JOB_MARKET_MAP_SCHEDULER_SERVICE=1
PORT="$(uv run python - <<'PY'
from collector.settings import get_setting
print(int(get_setting('api.port')))
PY
)"
echo "Job Market Map API/Admin starting on http://127.0.0.1:${PORT}/admin"
echo "In-app overnight scheduler enabled by service mode; schedule is controlled from Admin."
exec uv run uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
