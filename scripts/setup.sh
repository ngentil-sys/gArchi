#!/usr/bin/env bash
# One-time local setup for the GCP Architect agent (glinux / Chromebook Linux container).
set -euo pipefail
cd "$(dirname "$0")/.."

GCLOUD="${GCLOUD:-$(command -v gcloud || echo /google/data/ro/teams/cloud-sdk/gcloud)}"
ENV_FILE=gcp_architect/.env

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

step "Python environment"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e ".[dev]"

step "Configuration"
if [ ! -f "$ENV_FILE" ]; then
  cp gcp_architect/.env.example "$ENV_FILE"
  read -r -p "Google Cloud project id: " PROJECT
  sed -i "s/your-gcp-project-id/$PROJECT/g" "$ENV_FILE"
  echo "Wrote $ENV_FILE (edit it any time)."
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

step "gcloud: login + Application Default Credentials (Vertex AI + Slides + Drive scopes)"
# Over SSH (no DISPLAY) gcloud cannot open a browser: it prints a URL to open on your laptop
# and asks you to paste the code back. Force that mode with NO_BROWSER=1.
BROWSER_FLAG=()
if [ -n "${NO_BROWSER:-}" ] || [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then BROWSER_FLAG=(--no-launch-browser); fi
if [ -z "${SKIP_LOGIN:-}" ]; then
  "$GCLOUD" auth login "${BROWSER_FLAG[@]}"
  "$GCLOUD" auth application-default login "${BROWSER_FLAG[@]}" \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/drive.file
fi
"$GCLOUD" auth application-default set-quota-project "$GOOGLE_CLOUD_PROJECT"
"$GCLOUD" config set project "$GOOGLE_CLOUD_PROJECT"

step "Enable APIs"
"$GCLOUD" services enable aiplatform.googleapis.com slides.googleapis.com drive.googleapis.com \
  storage.googleapis.com iamcredentials.googleapis.com --project "$GOOGLE_CLOUD_PROJECT"

step "Official icons"
if [ -n "${ICON_BUCKET:-}" ] && [ "${ICON_HOST:-gcs}" = "gcs" ]; then
  ARGS=(--bucket "$ICON_BUCKET" --create --public)
  if [ -n "${ICON_SIGNER_SA:-}" ]; then ARGS+=(--signer "$ICON_SIGNER_SA" --create-signer); fi
  .venv/bin/python scripts/setup_icons.py "${ARGS[@]}"
else
  .venv/bin/python scripts/setup_icons.py
fi

step "Smoke test"
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/check_setup.py

cat <<EOF

Done. Start the agent UI with:
  ./scripts/run.sh          # then open http://127.0.0.1:8000
EOF
