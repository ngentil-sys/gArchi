output "workspace_urls" {
  description = "Cloud Run URLs (behind IAP)."
  value       = { for k, s in google_cloud_run_v2_service.this : k => s.uri }
}

output "buckets" {
  description = "Bucket names by role."
  value       = { for k, b in google_storage_bucket.this : k => b.name }
}

output "artifact_repositories" {
  description = "Docker repository paths."
  value       = { for k, r in google_artifact_registry_repository.this : k => "${r.location}-docker.pkg.dev/${var.project_id}/${r.repository_id}" }
}

output "service_accounts" {
  description = "Service account emails by key."
  value       = { for k, sa in google_service_account.this : k => sa.email }
}

output "agent_engine_ids" {
  description = "Reasoning engine ids (AGENT_ENGINE_ID for managed sessions)."
  value       = { for k, e in google_vertex_ai_reasoning_engine.this : k => element(split("/", e.name), length(split("/", e.name)) - 1) }
}
