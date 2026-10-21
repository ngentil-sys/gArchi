"""Deterministic scaffold for a Terraform project derived from an ArchitectureSpec."""

from __future__ import annotations

import re

from .. import catalog_data
from ..diagram.spec import ArchitectureSpec
from .registry import latest_provider_version, resources_for_product

BASE_VARIABLES = ["project_id", "region", "zone", "environment", "app_name", "labels", "enabled_apis", "deletion_protection"]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(s) > 40:
        s = s[:40].rsplit("-", 1)[0]
    return s or "gcp-architecture"


def _minor_constraint(version: str) -> str:
    major, minor, *_ = version.split(".")
    return f"~> {major}.{minor}"


def scaffold_files(spec: ArchitectureSpec | None, app_name: str, region: str = "us-central1") -> tuple[dict[str, str], list[dict]]:
    """Return ({filename: content}, plan) where plan lists the components the agent must implement."""
    version = latest_provider_version()
    constraint = _minor_constraint(version)
    app = slugify(app_name).replace("-", "_")[:30]

    plan: list[dict] = []
    apis: set[str] = {"cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com", "iam.googleapis.com"}
    seen: set[str] = set()
    needs_beta = False
    if spec:
        for n in spec.nodes:
            if not n.product:
                continue
            prod = catalog_data.get_product(n.product)
            if not prod or prod["category"] == "external":
                continue
            info = resources_for_product(n.product) or {}
            apis.update(info.get("apis", []))
            if info.get("provider") == "google-beta":
                needs_beta = True
            key = (n.product, n.sublabel or "")
            if key in seen:
                continue
            seen.add(key)
            plan.append({
                "node_id": n.id,
                "label": n.label,
                "role": n.sublabel,
                "product": prod["id"],
                "provider": info.get("provider", "google"),
                "resources": [r["name"] for r in info.get("resources", [])],
                "docs": [r["docs"] for r in info.get("resources", [])],
                "notes": info.get("notes"),
            })

    title = spec.title if spec else app_name
    files: dict[str, str] = {}

    files["providers.tf"] = f'''terraform {{
  required_version = ">= 1.9.0"

  required_providers {{
    google = {{
      source  = "hashicorp/google"
      version = "{constraint}"
    }}
    google-beta = {{
      source  = "hashicorp/google-beta"
      version = "{constraint}"
    }}
  }}
}}

provider "google" {{
  project               = var.project_id
  region                = var.region
  zone                  = var.zone
  default_labels        = local.labels
  user_project_override = true
  billing_project       = var.project_id
}}

provider "google-beta" {{
  project               = var.project_id
  region                = var.region
  zone                  = var.zone
  default_labels        = local.labels
  user_project_override = true
  billing_project       = var.project_id
}}
'''

    files["backend.tf"] = '''# Remote state (recommended for teams). Backend blocks cannot reference variables,
# so pass the bucket at init time:
#
#   terraform init \\
#     -backend-config="bucket=<your-tf-state-bucket>" \\
#     -backend-config="prefix=<environment>/<app_name>"
#
# Uncomment to enable:
#
# terraform {
#   backend "gcs" {}
# }
'''

    files["variables.tf"] = f'''# ---------------------------------------------------------------------------
# Core variables (every environment sets these in a *.tfvars file)
# ---------------------------------------------------------------------------

variable "project_id" {{
  description = "Google Cloud project ID that hosts the resources."
  type        = string

  validation {{
    condition     = can(regex("^[a-z][a-z0-9-]{{4,28}}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }}
}}

variable "region" {{
  description = "Default region for regional resources."
  type        = string
  default     = "{region}"
}}

variable "zone" {{
  description = "Default zone for zonal resources."
  type        = string
  default     = "{region}-a"
}}

variable "environment" {{
  description = "Deployment environment, used in names and labels."
  type        = string

  validation {{
    condition     = contains(["dev", "test", "stage", "prod"], var.environment)
    error_message = "environment must be one of dev, test, stage, prod."
  }}
}}

variable "app_name" {{
  description = "Short application / workload name used as a naming prefix (lowercase, hyphens)."
  type        = string
  default     = "{slugify(app_name)}"

  validation {{
    condition     = can(regex("^[a-z][a-z0-9-]{{1,20}}$", var.app_name))
    error_message = "app_name must be lowercase alphanumeric with hyphens, 2-21 characters."
  }}
}}

variable "labels" {{
  description = "Additional labels applied to every resource (merged with the standard labels)."
  type        = map(string)
  default     = {{}}
}}

variable "enabled_apis" {{
  description = "Google APIs to enable in the project."
  type        = list(string)
  default = [
{chr(10).join(f'    "{a}",' for a in sorted(apis))}
  ]
}}

variable "deletion_protection" {{
  description = "Protect stateful resources (databases, buckets with data, clusters) from accidental destroy. Set false only in non-production."
  type        = bool
  default     = true
}}

# ---------------------------------------------------------------------------
# Component variables (added by the architect; no literals in main.tf)
# ---------------------------------------------------------------------------
'''

    files["terraform.tfvars"] = f'''# {title}
# Copy per environment (e.g. prod.tfvars) and pass with: terraform apply -var-file=prod.tfvars

project_id  = "REPLACE_ME"
region      = "{region}"
zone        = "{region}-a"
environment = "dev"
app_name    = "{slugify(app_name)}"

labels = {{
  owner       = "REPLACE_ME"
  cost_center = "REPLACE_ME"
}}

deletion_protection = false # dev only; keep true in prod
'''

    component_blocks = []
    for p in plan:
        res = ", ".join(p["resources"]) if p["resources"] else "(API enablement / IAM only)"
        docs = "\n".join(f"#   {d}" for d in p["docs"]) or "#   (no Terraform resource; see catalog notes)"
        notes = f"\n# Note: {p['notes']}" if p.get("notes") else ""
        role = f" — {p['role']}" if p.get("role") else ""
        component_blocks.append(f'''# ---------------------------------------------------------------------------
# {p['label']}{role}
# Terraform: {res}{' (google-beta)' if p['provider'] == 'google-beta' else ''}
# Docs:
{docs}{notes}
# TODO(architect): implement with variables only (no literal names/regions/projects)
# ---------------------------------------------------------------------------
''')

    files["main.tf"] = f'''# {title}
# Generated by GCP Architect Agent. All environment-specific values come from variables.

locals {{
  name_prefix = "${{var.environment}}-${{var.app_name}}"

  labels = merge(
    {{
      environment = var.environment
      application = var.app_name
      managed_by  = "terraform"
    }},
    var.labels,
  )
}}

# Enable the Google APIs the architecture depends on.
resource "google_project_service" "apis" {{
  for_each = toset(var.enabled_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}}

{chr(10).join(component_blocks)}'''

    files["outputs.tf"] = '''# Outputs consumed by other stacks, CI/CD or humans.
# Add one output per externally relevant endpoint / identifier.
'''

    files["README.md"] = f'''# {title} — Terraform

Generated by the GCP Architect Agent from the approved architecture. Provider: `hashicorp/google {constraint}`
(latest at generation time: {version}).

## Layout

| File | Purpose |
|---|---|
| `providers.tf` | Terraform + provider version pins, `google` / `google-beta` providers |
| `backend.tf` | Remote state (GCS) — enable and pass `-backend-config` at init |
| `variables.tf` | Every tunable input, typed and documented; no literals live in `main.tf` |
| `terraform.tfvars` | Environment values (copy to `prod.tfvars`, …) |
| `main.tf` | Resources, grouped by architecture component |
| `outputs.tf` | Endpoints / identifiers for other stacks |

## Usage

```bash
terraform init                       # add -backend-config=... when using remote state
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

Authenticate with `gcloud auth application-default login` (or a CI service account via
Workload Identity Federation). Secrets must come from Secret Manager, never from tfvars.
'''
    if needs_beta:
        files["README.md"] += "\nSome components use the `google-beta` provider (see comments in `main.tf`).\n"
    return files, plan
