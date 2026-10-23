#!/usr/bin/env bash
# Start the gArchi workspace GUI (customer brief + agent chat).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
exec .venv/bin/python -m uvicorn gcp_architect.ui.server:app --host "$HOST" --port "$PORT" "$@"
