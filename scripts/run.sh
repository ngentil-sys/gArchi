#!/usr/bin/env bash
# Start the local ADK web UI for the GCP Architect agent.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
exec .venv/bin/adk web --port "$PORT" --reload_agents "$@" .
