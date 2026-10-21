# gArchi — GCP Architect Agent

A locally hosted Gemini agent (built on the [Agent Development Kit](https://google.github.io/adk-docs/))
that designs **enterprise-grade Google Cloud architectures** and delivers them as
**editable Google Slides decks** using the **official Google Cloud icons** and links to
**official documentation** (cloud.google.com).

```
you ──chat──▶ gcp_architect (Gemini 3.8 Flash, ADK)
                 ├─ search_gcp_docs        Gemini + Google Search grounding, restricted to cloud.google.com
                 ├─ fetch_official_doc     reads a cloud.google.com page (limits, SLAs, best practices)
                 ├─ list/search/get_gcp_product   curated catalog: product → docs URL + official icon
                 ├─ validate_architecture_spec    layout dry-run
                 ├─ render_architecture_deck      spec → Google Slides (native shapes, bound connectors, icons)
                 └─ Terraform: scaffold_terraform_project · fetch_terraform_resource_doc (official provider docs)
                               · write_terraform_files (lint + fmt/validate) · read/lint_terraform_project
```

Generated deck: **cover → editable architecture diagram → component table (with docs links) →
design considerations (Architecture Framework pillars) → references**. Re-requests ("make it
multi-region") rebuild the same deck in place.

Generated Terraform (ask "generate the Terraform"): `output/<slug>/terraform/` with
`providers.tf` (pinned `hashicorp/google` + `google-beta`), `backend.tf` (GCS remote state),
`variables.tf` (typed, documented, validated), `terraform.tfvars`, `main.tf` (locals, API
enablement, one section per component) and `outputs.tf`. **Every resource is a `for_each` over a
typed `map(object({...}))` variable** — adding a bucket, service or database is a new entry in
`terraform.tfvars`, not new HCL. Every resource block is written after
reading the **official provider documentation** for that resource; a linter rejects resources
without `for_each` (or using `count`), literal
project ids/regions/secrets and undeclared variables, and runs `terraform fmt`/`validate` when a
`terraform` binary is present (`TERRAFORM_BIN`).

## Prerequisites

* Clone: `git clone git@github.com:ngentil-sys/gArchi.git gcp-architect-agent`
* glinux or a Chromebook Linux container with Python ≥ 3.11 (`python3-venv`), `gcloud`.
* A Google Cloud project you own with billing enabled (Vertex AI for Gemini, Slides/Drive APIs,
  a small bucket to host the icons).

## Setup (once)

```bash
./scripts/setup.sh
```

The script creates the venv, writes `gcp_architect/.env`, logs you in with the needed scopes
(`cloud-platform`, `presentations`, `drive.file`), enables the APIs, downloads the official icon
pack from https://cloud.google.com/icons, mirrors the PNGs to your bucket and runs the tests.

### Why a bucket for icons?

The Slides API inserts images **by public URL** only (PNG/JPEG/GIF, no auth, no SVG). The setup
mirrors the official PNGs to `gs://<ICON_BUCKET>/icons/` and makes them readable. If your org
policy forbids public buckets, keep the bucket private and set `ICON_SIGNER_SA`; the agent then
mints short-lived V4 signed URLs through IAM `signBlob` (the script creates the SA and grants you
`roles/iam.serviceAccountTokenCreator` with `--create-signer`).

### Cloudtop (glinux) notes

* `gcloud` lives at `/google/data/ro/teams/cloud-sdk/gcloud` if it is not on your `PATH`
  (`export GCLOUD=/google/data/ro/teams/cloud-sdk/gcloud`).
* Over SSH there is no browser: the script passes `--no-launch-browser`, prints a URL, you open it
  on your laptop and paste the verification code back. Already logged in? `SKIP_LOGIN=1 ./scripts/setup.sh`.
* The ADK UI binds to `127.0.0.1:8000`. From your laptop either use Chrome Remote Desktop, or
  forward the port: `ssh -L 8000:localhost:8000 <you>.c.googlers.com` and open http://localhost:8000.

## Run

```bash
./scripts/run.sh            # ADK dev UI on http://127.0.0.1:8000  (select "gcp_architect")
# or: .venv/bin/adk run gcp_architect   (terminal chat)
```

Try: *"Design a PCI-compliant payments API for a retailer in EU regions: 5k TPS, RPO 0, RTO 15 min,
on-prem SAP integration."*

## Developing the diagram engine without hitting the API

```bash
.venv/bin/python -m pytest -q                     # validates generated requests against the Slides schema
.venv/bin/python scripts/preview_diagram.py       # renders /tmp/diagram_preview.png from the example spec
.venv/bin/python scripts/preview_diagram.py my_spec.json -o out.png
.venv/bin/python scripts/render_example.py        # real end-to-end deck (needs auth)
```

## Layout of the repo

| Path | Purpose |
|---|---|
| `gcp_architect/agent.py` | `root_agent` (architect) + grounded `gcp_docs_researcher` sub-agent |
| `gcp_architect/prompts.py` | system instructions (method, enterprise baseline, spec schema) |
| `gcp_architect/tools.py` | ADK function tools (catalog, docs, deck) |
| `gcp_architect/tools_terraform.py` | ADK function tools (Terraform) |
| `gcp_architect/catalog/products.json` | curated product catalog: id, name, category, official docs URL, official icon id |
| `gcp_architect/diagram/spec.py` | `ArchitectureSpec` (zones / nodes / edges / components / considerations / references) |
| `gcp_architect/diagram/layout.py` | deterministic zone-driven layout on the 720×405 pt page |
| `gcp_architect/diagram/routing.py` | connector site selection + label placement |
| `gcp_architect/diagram/slides_builder.py` | spec → Slides `batchUpdate` requests (all native, editable objects) |
| `gcp_architect/diagram/icons.py` | icon id → public URL (GCS public / signed / static) |
| `gcp_architect/gapi/` | ADC auth + Slides/Drive client |
| `gcp_architect/terraform/registry.py` | product → Terraform resources/APIs map; official provider doc fetcher (hashicorp/terraform-provider-google) |
| `gcp_architect/terraform/scaffold.py` | deterministic project skeleton from the spec |
| `gcp_architect/terraform/lint.py` | HCL structure/variable/secret linter + optional `terraform fmt/validate` |
| `gcp_architect/catalog/terraform_resources.json` | product id → resources, APIs, beta flag |
| `scripts/setup_icons.py` | official icon pack download + manifest + bucket mirror |

## Catalog notes

* Icon ids come from the official pack (216 product icons). Products newer than the pack
  (AlloyDB, Agent Engine, Model Armor, …) are flagged `icon_approx` and the agent tells you which
  slide element to swap manually.
* Generic non-GCP nodes (users, on-prem, SaaS, other cloud) use Google's Apache-2.0 Material icons.
* Add a product: append to `products.json` (`docs` must be an official URL) and rerun the tests.

## Roadmap (after the agent is "perfect")

1. Package as a desktop/web app (FastAPI + simple chat UI, or ADK `api_server` behind a thin UI).
2. Speaker notes with the full design narrative; cost estimate slide via Cloud Billing Catalog API.
3. Vertex AI Search datastore over cloud.google.com for higher-precision grounding.
4. Deploy to Agent Engine for team use.
