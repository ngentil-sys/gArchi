# gArchi hosting on Google Cloud (Argolis): Cloud Run + IAP, Artifact Registry, buckets,
# runtime service account, Agent Engine sessions. Every resource iterates a map variable.

locals {
  name_prefix = "${var.environment}-${var.app_name}"

  labels = merge(
    {
      environment = var.environment
      application = var.app_name
      managed_by  = "terraform"
    },
    var.labels,
  )

  # service_account key -> role bindings, flattened for for_each
  sa_role_bindings = merge([
    for sa_key, sa in var.service_accounts : {
      for role in sa.roles : "${sa_key}:${role}" => { sa = sa_key, role = role }
    }
  ]...)

  public_buckets = { for k, b in var.buckets : k => b if b.public }

  # Cloud Run service key -> IAP principal, flattened
  iap_bindings = merge([
    for svc_key, svc in var.cloud_run_services : {
      for member in var.iap_users : "${svc_key}:${member}" => { service = svc_key, member = member }
    } if svc.iap_enabled
  ]...)

  iap_services = { for k, s in var.cloud_run_services : k => s if s.iap_enabled }
}

data "google_project" "this" {
  project_id = var.project_id
}

# ---------------------------------------------------------------------------
# APIs
# ---------------------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset(var.enabled_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Buckets (icons: public read for the Slides API; output: Terraform projects)
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "this" {
  for_each = var.buckets

  name                        = "${var.project_id}-${var.app_name}-${each.key}"
  project                     = var.project_id
  location                    = var.region
  storage_class               = each.value.storage_class
  uniform_bucket_level_access = true
  public_access_prevention    = each.value.public ? "inherited" : "enforced" # public=false by default (private + signed URLs)
  force_destroy               = !var.deletion_protection
  labels                      = local.labels

  versioning {
    enabled = each.value.versioning
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.lifecycle_days == null ? [] : [each.value.lifecycle_days]
    content {
      action {
        type = "Delete"
      }
      condition {
        age        = lifecycle_rule.value
        with_state = "ARCHIVED"
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "public_read" {
  for_each = local.public_buckets

  bucket = google_storage_bucket.this[each.key].name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# ---------------------------------------------------------------------------
# Artifact Registry
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "this" {
  for_each = var.artifact_repositories

  project       = var.project_id
  location      = var.region
  repository_id = "${local.name_prefix}-${each.key}"
  description   = each.value.description
  format        = "DOCKER"
  labels        = local.labels

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = each.value.keep_count
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Service accounts + IAM
# ---------------------------------------------------------------------------
resource "google_service_account" "this" {
  for_each = var.service_accounts

  project      = var.project_id
  account_id   = "${local.name_prefix}-${each.key}"
  display_name = each.value.display_name

  depends_on = [google_project_service.apis]
}

# Allow a service account to sign URLs as itself via IAM signBlob (no key files).
resource "google_service_account_iam_member" "self_signer" {
  for_each = { for k, sa in var.service_accounts : k => sa if sa.self_signer }

  service_account_id = google_service_account.this[each.key].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.this[each.key].email}"
}

resource "google_project_iam_member" "sa_roles" {
  for_each = local.sa_role_bindings

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.this[each.value.sa].email}"
}

# ---------------------------------------------------------------------------
# Agent Engine (managed sessions)
# ---------------------------------------------------------------------------
resource "google_vertex_ai_reasoning_engine" "this" {
  for_each = var.agent_engines

  project      = var.project_id
  region       = var.region
  display_name = each.value.display_name
  description  = each.value.description
  labels       = local.labels

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Cloud Run (IAP in front, scale to zero)
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "this" {
  for_each = var.cloud_run_services

  project             = var.project_id
  location            = var.region
  name                = "${local.name_prefix}-${each.key}"
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = each.value.iap_enabled
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    service_account                  = google_service_account.this[each.value.service_account].email
    timeout                          = "${each.value.timeout_seconds}s"
    max_instance_request_concurrency = each.value.concurrency

    scaling {
      min_instance_count = each.value.min_instances
      max_instance_count = each.value.max_instances
    }

    containers {
      image = each.value.image

      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = each.value.env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [google_project_service.apis, google_project_iam_member.sa_roles]
}

# IAP's service agent must be allowed to invoke the service.
resource "google_project_service_identity" "iap" {
  provider = google-beta
  for_each = length(local.iap_services) > 0 ? { iap = "iap.googleapis.com" } : {}

  project = var.project_id
  service = each.value

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  for_each = local.iap_services

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"

  depends_on = [google_project_service_identity.iap]
}

resource "google_iap_web_cloud_run_service_iam_member" "users" {
  for_each = local.iap_bindings

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.this[each.value.service].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value.member
}

# ---------------------------------------------------------------------------
# Budget guardrail (optional)
# ---------------------------------------------------------------------------
resource "google_billing_budget" "this" {
  for_each = var.budgets

  billing_account = each.value.billing_account
  display_name    = "${local.name_prefix}-${each.key}"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(each.value.amount_usd)
    }
  }

  dynamic "threshold_rules" {
    for_each = each.value.thresholds
    content {
      threshold_percent = threshold_rules.value
    }
  }

  depends_on = [google_project_service.apis]
}
