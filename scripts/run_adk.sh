#!/usr/bin/env bash
# Start the stock ADK developer UI (useful for debugging traces/events).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8001}"
exec .venv/bin/adk web --port "$PORT" --reload_agents "$@" .
