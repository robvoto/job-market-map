#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="$(uv run python -c 'from collector.settings import get_setting; print(int(get_setting("api.port")))')"
exec uv run uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
