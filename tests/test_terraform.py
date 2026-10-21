"""Terraform scaffold + lint tests (offline except one live doc fetch, skipped without network)."""

from __future__ import annotations

import httpx
import pytest

from gcp_architect.diagram.spec import ArchitectureSpec, example_spec
from gcp_architect.terraform import lint as tflint
from gcp_architect.terraform import registry
from gcp_architect.terraform.scaffold import scaffold_files


@pytest.fixture(scope="module")
def scaffold():
    spec = ArchitectureSpec.model_validate(example_spec())
    files, plan = scaffold_files(spec, spec.title, region="europe-west1")
    return files, plan


def test_scaffold_has_required_files_and_plan(scaffold):
    files, plan = scaffold
    for f in tflint.REQUIRED_FILES + ["outputs.tf", "backend.tf", "README.md"]:
        assert f in files
    products = {p["product"] for p in plan}
    assert {"cloud_run", "cloud_sql", "cloud_load_balancing", "bigquery"} <= products
    assert next(p for p in plan if p["product"] == "cloud_run")["map_variable"] == "cloud_run_services"
    assert "for_each = var.cloud_run_services" in files["main.tf"]
    assert "users" not in products  # external nodes are not Terraform components
    assert "run.googleapis.com" in files["variables.tf"]
    assert 'default     = "europe-west1"' in files["variables.tf"]
    assert "~> " in files["providers.tf"]


def test_scaffold_lint_only_todos(scaffold):
    files, _ = scaffold
    findings = tflint.lint_files(files)
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], [f.message for f in errors]
    assert any("TODO(architect)" in f.message for f in findings)


def test_lint_catches_hardcodes_and_secrets(scaffold):
    files, _ = dict(scaffold[0]), scaffold[1]
    files["main.tf"] += '''
resource "google_sql_database_instance" "db" {
  name             = "my-db"
  region           = "us-central1"
  project          = "my-prod-project"
  database_version = var.db_version
  settings {
    tier = var.db_tier
  }
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = google_sql_database_instance.db.name
  password = "SuperSecret123"
}
'''
    findings = tflint.lint_files(files)
    msgs = [f.message for f in findings if f.severity == "error"]
    assert any("hard-coded region" in m for m in msgs)
    assert any("hard-coded project" in m for m in msgs)
    assert any("plaintext secret" in m for m in msgs)
    assert any("var.db_version is referenced but not declared" in m for m in msgs)
    assert any("var.db_tier is referenced but not declared" in m for m in msgs)


def test_lint_clean_component(scaffold):
    files, _ = dict(scaffold[0]), scaffold[1]
    files["main.tf"] = files["main.tf"].replace("TODO(architect)", "implemented") + '''
resource "google_storage_bucket" "this" {
  for_each = var.storage_buckets

  name                        = "${local.name_prefix}-${each.key}"
  location                    = each.value.location != null ? each.value.location : var.region
  storage_class               = each.value.storage_class
  uniform_bucket_level_access = true
  force_destroy               = !var.deletion_protection
  labels                      = local.labels
  depends_on                  = [google_project_service.apis]
}
'''
    files["variables.tf"] += '''
variable "storage_buckets" {
  description = "Buckets to create, keyed by short name."
  type = map(object({
    location      = optional(string)
    storage_class = optional(string, "STANDARD")
  }))
  default = {}
}
'''
    files["terraform.tfvars"] += '\nstorage_buckets = {\n  assets = { storage_class = "STANDARD" }\n}\n'
    files["outputs.tf"] += '\noutput "bucket_names" {\n  value = { for k, b in google_storage_bucket.this : k => b.name }\n}\n'
    findings = tflint.lint_files(files)
    assert [f for f in findings if f.severity == "error"] == []


def test_lint_requires_for_each(scaffold):
    files, _ = dict(scaffold[0]), scaffold[1]
    files["main.tf"] += '''
resource "google_pubsub_topic" "events" {
  name   = "${local.name_prefix}-events"
  labels = local.labels
}

resource "google_pubsub_subscription" "counted" {
  count = 2
  name  = "${local.name_prefix}-sub-${count.index}"
  topic = google_pubsub_topic.events.name
}

resource "google_pubsub_topic" "listy" {
  for_each = var.topic_names
  name     = each.value
}
'''
    files["variables.tf"] += '\nvariable "topic_names" {\n  type    = list(string)\n  default = []\n}\n'
    findings = tflint.lint_files(files)
    errors = [f.message for f in findings if f.severity == "error"]
    assert any("google_pubsub_topic.events: missing for_each" in m for m in errors)
    assert any("google_pubsub_subscription.counted: uses count" in m for m in errors)
    assert any("topic_names drives for_each" in f.message for f in findings if f.severity == "warning")
    # the scaffold's own google_project_service.apis is exempt / already for_each
    assert not any("google_project_service" in m for m in errors)


def test_variable_default_detection_ignores_nested_blocks():
    tf = '''
variable "a" {
  type = string
  validation {
    condition     = length(var.a) > 0
    error_message = "default = nope"
  }
}
variable "b" {
  type    = string
  default = "x"
}
'''
    assert tflint._variable_defaults(tf) == {"a": False, "b": True}


def test_live_doc_fetch():
    try:
        httpx.get("https://raw.githubusercontent.com", timeout=5)
    except httpx.HTTPError:
        pytest.skip("no network")
    doc = registry.fetch_resource_doc("google_cloud_run_v2_service")
    assert doc["found"] and doc["provider"] == "google"
    assert "location" in doc["argument_reference"]
    assert doc["examples"] and 'resource "google_cloud_run_v2_service"' in doc["examples"][0]["hcl"]
    missing = registry.fetch_resource_doc("google_definitely_not_a_resource")
    assert missing["found"] is False
