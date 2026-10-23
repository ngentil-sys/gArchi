#!/usr/bin/env bash
# Deploy gArchi to a Google Cloud project (designed for an Argolis self-managed org).
#
#   scripts/deploy_argolis.sh --project garchi-demo-001 --region us-central1 \
#       --iap-user user:admin@ngentil.altostrat.com --iap-user user:ngentil@google.com \
#       --share-with ngentil@google.com [--create] [--budget 100]
#
# Steps: (1) create/link project  (2) state bucket + terraform init  (3) apply foundations
#        (4) build image with Cloud Build  (5) apply Cloud Run + IAP  (6) upload icons
#        (7) pull a local copy of the Terraform state to output/
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

GCLOUD="${GCLOUD:-$(command -v gcloud || echo /google/data/ro/teams/cloud-sdk/gcloud)}"
TF="${TERRAFORM_BIN:-$(command -v terraform)}"
PROJECT=""; REGION="us-central1"; CREATE=0; BUDGET=""; ENVIRONMENT="dev"; APP="garchi"
IAP_USERS=(); SHARE_WITH=""; ORG=""; FOLDER=""; BILLING=""; SKIP_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --create) CREATE=1; shift;;
    --org) ORG="$2"; shift 2;;
    --folder) FOLDER="$2"; shift 2;;
    --billing) BILLING="$2"; shift 2;;
    --iap-user) IAP_USERS+=("$2"); shift 2;;
    --share-with) SHARE_WITH="$2"; shift 2;;
    --budget) BUDGET="$2"; shift 2;;
    --environment) ENVIRONMENT="$2"; shift 2;;
    --skip-build) SKIP_BUILD=1; shift;;
    *) echo "unknown flag $1"; exit 2;;
  esac
done
[ -n "$PROJECT" ] || { echo "--project is required"; exit 2; }

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ACCOUNT=$("$GCLOUD" config get-value account 2>/dev/null)
echo "Deploying as $ACCOUNT to project $PROJECT ($REGION)"

# ---------------------------------------------------------------------------- 1. project
if [ "$CREATE" = 1 ]; then
  step "Create project $PROJECT"
  if "$GCLOUD" projects describe "$PROJECT" >/dev/null 2>&1; then
    echo "project exists, reusing"
  else
    PARENT=()
    if [ -n "$FOLDER" ]; then PARENT=(--folder "$FOLDER")
    else
      [ -n "$ORG" ] || ORG=$("$GCLOUD" organizations list --format='value(ID)' | head -1)
      PARENT=(--organization "$ORG")
    fi
    "$GCLOUD" projects create "$PROJECT" --name="gArchi" "${PARENT[@]}" --labels=application=garchi,managed_by=terraform
  fi
  [ -n "$BILLING" ] || BILLING=$("$GCLOUD" billing accounts list --filter=open=true --format='value(ACCOUNT_ID)' | head -1)
  step "Link billing account $BILLING"
  "$GCLOUD" billing projects link "$PROJECT" --billing-account "$BILLING" >/dev/null
fi
"$GCLOUD" config set project "$PROJECT" >/dev/null
PROJECT_NUMBER=$("$GCLOUD" projects describe "$PROJECT" --format='value(projectNumber)')

step "Enable bootstrap APIs"
"$GCLOUD" services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com storage.googleapis.com \
  cloudbilling.googleapis.com iam.googleapis.com --project "$PROJECT" >/dev/null

# ---------------------------------------------------------------------------- 2. state bucket + init
STATE_BUCKET="${PROJECT}-tfstate"
step "Terraform state bucket gs://$STATE_BUCKET"
if ! "$GCLOUD" storage buckets describe "gs://$STATE_BUCKET" >/dev/null 2>&1; then
  "$GCLOUD" storage buckets create "gs://$STATE_BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention >/dev/null
  "$GCLOUD" storage buckets update "gs://$STATE_BUCKET" --versioning >/dev/null
fi

cd infra
"$TF" init -input=false -reconfigure -backend-config="bucket=$STATE_BUCKET" -backend-config="prefix=garchi/$ENVIRONMENT" >/dev/null
echo "state: gs://$STATE_BUCKET/garchi/$ENVIRONMENT/default.tfstate"

IAP_HCL="[]"
if [ ${#IAP_USERS[@]} -gt 0 ]; then
  IAP_HCL="[$(printf '"%s",' "${IAP_USERS[@]}")]"; IAP_HCL="${IAP_HCL%,]}]"
fi
BUDGET_HCL="{}"
if [ -n "$BUDGET" ]; then
  [ -n "$BILLING" ] || BILLING=$("$GCLOUD" billing projects describe "$PROJECT" --format='value(billingAccountName)' | sed 's#billingAccounts/##')
  BUDGET_HCL="{ monthly = { billing_account = \"$BILLING\", amount_usd = $BUDGET } }"
fi
ICON_BUCKET="${PROJECT}-${APP}-icons"
OUTPUT_BUCKET="${PROJECT}-${APP}-output"
AR_REPO="${REGION}-docker.pkg.dev/${PROJECT}/${ENVIRONMENT}-${APP}-app"
IMAGE="${AR_REPO}/workspace"

write_tfvars() {  # $1 = image (empty => no Cloud Run yet)
  local image="$1" services="{}"
  if [ -n "$image" ]; then
    services=$(cat <<EOF
{
  workspace = {
    image = "$image"
    env = {
      GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
      GOOGLE_CLOUD_PROJECT      = "$PROJECT"
      GOOGLE_CLOUD_LOCATION     = "$REGION"
      ICON_HOST                 = "gcs"
      ICON_BUCKET               = "$ICON_BUCKET"
      ICON_PREFIX               = "icons/"
      OUTPUT_DIR                = "gs://$OUTPUT_BUCKET/engagements"
      SESSION_BACKEND           = "agent_engine"
      AGENT_ENGINE_ID           = "$AGENT_ENGINE_ID"
      SHARE_DECKS_WITH          = "$SHARE_WITH"
      ARCHITECT_THINKING_LEVEL  = "medium"
      RESEARCH_BUDGET_PER_TURN  = "6"
    }
  }
}
EOF
)
  fi
  cat > terraform.tfvars <<EOF
# generated by scripts/deploy_argolis.sh
project_id  = "$PROJECT"
region      = "$REGION"
environment = "$ENVIRONMENT"
app_name    = "$APP"
iap_users   = $IAP_HCL
budgets     = $BUDGET_HCL
cloud_run_services = $services
EOF
}

# ---------------------------------------------------------------------------- 3. foundations
step "Terraform apply: APIs, buckets, registry, service account, Agent Engine sessions"
AGENT_ENGINE_ID=""
write_tfvars ""
"$TF" apply -input=false -auto-approve -no-color | tail -5
AGENT_ENGINE_ID=$("$TF" output -json agent_engine_ids | python3 -c 'import json,sys; print(json.load(sys.stdin)["sessions"])')
echo "agent engine (sessions): $AGENT_ENGINE_ID"

# ---------------------------------------------------------------------------- 4. image
cd ..
if [ "$SKIP_BUILD" = 0 ]; then
  step "Build image with Cloud Build -> $IMAGE"
  TAG=$(git rev-parse --short HEAD 2>/dev/null || date +%s)
  "$GCLOUD" builds submit --project "$PROJECT" --region "$REGION" --tag "$IMAGE:$TAG" --quiet . >/dev/null
  IMAGE_REF="$IMAGE:$TAG"
else
  IMAGE_REF=$("$GCLOUD" artifacts docker images list "$IMAGE" --format='value(package)' --limit 1 --sort-by=~UPDATE_TIME):latest
fi
echo "image: $IMAGE_REF"

# ---------------------------------------------------------------------------- 5. Cloud Run + IAP
cd infra
step "Terraform apply: Cloud Run + IAP"
write_tfvars "$IMAGE_REF"
"$TF" apply -input=false -auto-approve -no-color | tail -5
URL=$("$TF" output -json workspace_urls | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace"])')

# ---------------------------------------------------------------------------- 6. icons
cd ..
step "Upload official icons to gs://$ICON_BUCKET/icons/"
.venv/bin/python scripts/setup_icons.py --bucket "$ICON_BUCKET" --prefix icons/ >/dev/null
curl -s -o /dev/null -w "icon probe: HTTP %{http_code}\n" "https://storage.googleapis.com/$ICON_BUCKET/icons/cloud_run.png"

# ---------------------------------------------------------------------------- 7. state copy
step "Terraform state"
mkdir -p output/state
( cd infra && "$TF" state pull > "../output/state/${PROJECT}-${ENVIRONMENT}.tfstate" )
echo "remote: gs://$STATE_BUCKET/garchi/$ENVIRONMENT/default.tfstate"
echo "local copy: output/state/${PROJECT}-${ENVIRONMENT}.tfstate"

cat <<EOF

Deployed.
  Workspace (IAP):   $URL
  Project:           $PROJECT  (number $PROJECT_NUMBER)
  Icons:             gs://$ICON_BUCKET/icons/
  Terraform output:  gs://$OUTPUT_BUCKET/engagements/
  Sessions:          Agent Engine $AGENT_ENGINE_ID
Allowed through IAP: ${IAP_USERS[*]:-<none - add --iap-user>}
EOF
