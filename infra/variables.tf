# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
variable "project_id" {
  description = "Google Cloud project that hosts gArchi (created by scripts/deploy_argolis.sh)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Vertex AI."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "test", "stage", "prod"], var.environment)
    error_message = "environment must be one of dev, test, stage, prod."
  }
}

variable "app_name" {
  description = "Naming prefix."
  type        = string
  default     = "garchi"
}

variable "labels" {
  description = "Extra labels merged into every resource."
  type        = map(string)
  default     = {}
}

variable "enabled_apis" {
  description = "APIs required by the workspace, the agent and the deploy pipeline."
  type        = list(string)
  default = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "drive.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "serviceusage.googleapis.com",
    "slides.googleapis.com",
    "storage.googleapis.com",
  ]
}

# ---------------------------------------------------------------------------
# Storage: buckets keyed by role (icons must be publicly readable for the Slides API)
# ---------------------------------------------------------------------------
variable "buckets" {
  description = "Buckets to create, keyed by role. Name = <project>-<app>-<key>."
  type = map(object({
    public         = optional(bool, false)
    versioning     = optional(bool, false)
    storage_class  = optional(string, "STANDARD")
    lifecycle_days = optional(number)
  }))
  default = {
    icons  = { public = true }
    output = { versioning = true, lifecycle_days = 365 }
  }
}

# ---------------------------------------------------------------------------
# Artifact Registry
# ---------------------------------------------------------------------------
variable "artifact_repositories" {
  description = "Docker repositories keyed by short name."
  type = map(object({
    description = optional(string, "gArchi images")
    keep_count  = optional(number, 10)
  }))
  default = {
    app = {}
  }
}

# ---------------------------------------------------------------------------
# Identity: one service account per workload with its project roles
# ---------------------------------------------------------------------------
variable "service_accounts" {
  description = "Service accounts keyed by short id with the project-level roles they need."
  type = map(object({
    display_name = string
    roles        = list(string)
  }))
  default = {
    workspace = {
      display_name = "gArchi workspace runtime"
      roles = [
        "roles/aiplatform.user",     # Gemini + Agent Engine Sessions
        "roles/storage.objectAdmin", # icons + Terraform output buckets
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
      ]
    }
  }
}

# ---------------------------------------------------------------------------
# Agent Engine (sessions only; the runtime itself is Cloud Run)
# ---------------------------------------------------------------------------
variable "agent_engines" {
  description = "Vertex AI Agent Engine resources used for managed sessions, keyed by short name."
  type = map(object({
    display_name = string
    description  = optional(string, "gArchi managed sessions")
  }))
  default = {
    sessions = { display_name = "gArchi sessions" }
  }
}

# ---------------------------------------------------------------------------
# Cloud Run services
# ---------------------------------------------------------------------------
variable "cloud_run_services" {
  description = "Cloud Run services keyed by short name. `image` is set by the deploy script after the first build."
  type = map(object({
    image           = string
    service_account = optional(string, "workspace") # key into var.service_accounts
    cpu             = optional(string, "1")
    memory          = optional(string, "1Gi")
    min_instances   = optional(number, 0)
    max_instances   = optional(number, 2)
    concurrency     = optional(number, 20)
    timeout_seconds = optional(number, 900) # long agent turns
    iap_enabled     = optional(bool, true)
    env             = optional(map(string), {})
  }))
  default = {}
}

variable "iap_users" {
  description = "Principals allowed through IAP to the workspace (user:, group:, domain:)."
  type        = set(string)
  default     = []
}

variable "deletion_protection" {
  description = "Protect stateful resources from destroy."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Cost guardrail
# ---------------------------------------------------------------------------
variable "budgets" {
  description = "Billing budgets keyed by name (requires billingbudgets.googleapis.com and billing account access)."
  type = map(object({
    billing_account = string
    amount_usd      = number
    thresholds      = optional(list(number), [0.5, 0.8, 1.0])
  }))
  default = {}
}
