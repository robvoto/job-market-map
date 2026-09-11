#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
  echo "JMM virtualenv is missing: $PYTHON" >&2
  exit 2
fi

"$ROOT/scripts/start_browser_service.sh"
echo "JMM collection log: $ROOT/logs/collection.log"
echo "Starting collection in this terminal..."
exec "$PYTHON" -u -m scripts.run_collection_cycle "$@"
